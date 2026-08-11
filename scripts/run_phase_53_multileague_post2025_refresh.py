"""Refresca el snapshot pre-match con una muestra reciente de varias ligas.

La ejecución es ``dry-run`` por defecto. La bandera ``--activate`` es el único
mecanismo que puede publicar y cambiar el puntero activo del registro.

Requirements:
    - requests
    - tenacity

Version: 1.0.0
Created: 2026-07-27
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
import time
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.espn_phase_7_15_r3 import _normalize
from src.espn_prospective_connector import (
    EspnConnectorConfig,
    EspnProspectiveConnector,
    scoreboard_references,
)
from src.prematch_snapshot_registry import activate_snapshot, publish_snapshot, read_snapshot_rows, resolve_active_snapshot
from scripts.run_phase_52_post2025_snapshot_refresh import _materialize

OUTPUT = ROOT / "artifacts/phase_53_multileague_post2025_refresh_v1"
DISCOVERY = ROOT / "artifacts/phase_36_multileague_discovery/references.json"
LOGGER = logging.getLogger(__name__)
LEAGUE_PATTERN = re.compile(r"^[a-z0-9_.-]+$")


def _parser() -> argparse.ArgumentParser:
    """Define argumentos seguros para descubrimiento, refresco y activación."""

    parser = argparse.ArgumentParser(description="Refresco multi-liga post-2025 con activación explícita.")
    parser.add_argument("--league", help="Slugs separados por coma; por defecto usa los documentados.")
    parser.add_argument("--start-date", default="20260401")
    parser.add_argument("--end-date", default="20260727")
    parser.add_argument("--max-matches-per-league", type=int, default=10)
    parser.add_argument("--sleep-seconds", type=float, default=0.2)
    parser.add_argument("--workers", type=int, default=1, help="Reservado para futura concurrencia; 1 es deliberado.")
    parser.add_argument("--snapshot-id", default="phase53_multileague_post2025_v1_20260727")
    parser.add_argument("--output-dir", default=None, help="Directorio de artefactos; permite ejecuciones incrementales aisladas.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--activate", action="store_true", help="Publica y activa tras superar los gates.")
    mode.add_argument("--dry-run", action="store_true", help="No modifica el registro; es el modo predeterminado.")
    return parser


def _dates(start: str, end: str) -> list[str]:
    """Genera fechas inclusivas y rechaza rangos excesivos o mal formados."""

    try:
        first = date.fromisoformat(f"{start[:4]}-{start[4:6]}-{start[6:]}")
        last = date.fromisoformat(f"{end[:4]}-{end[4:6]}-{end[6:]}")
    except ValueError as error:
        raise ValueError("invalid_refresh_date") from error
    if first > last or (last - first).days > 366:
        raise ValueError("invalid_refresh_date_range")
    return [(first + timedelta(days=i)).strftime("%Y%m%d") for i in range((last - first).days + 1)]


def _documented_leagues(argument: str | None) -> list[str]:
    """Obtiene slugs documentados y valida cualquier selección explícita."""

    payload = json.loads(DISCOVERY.read_text(encoding="utf-8"))
    available = sorted({str(row["league_slug"]) for row in payload if isinstance(row, dict) and row.get("league_slug")})
    selected = available if not argument else sorted({item.strip() for item in argument.split(",") if item.strip()})
    if not selected or any(not LEAGUE_PATTERN.fullmatch(item) for item in selected):
        raise ValueError("invalid_league_slug")
    unknown = sorted(set(selected) - set(available))
    if unknown:
        raise ValueError(f"undocumented_league:{','.join(unknown)}")
    return selected


def _references(leagues: list[str], args: argparse.Namespace) -> list[dict[str, str]]:
    """Descubre y deduplica referencias recientes por liga."""

    refs: dict[tuple[str, str, str], dict[str, str]] = {}
    for league in leagues:
        cache = OUTPUT / "cache" / league
        connector = EspnProspectiveConnector(EspnConnectorConfig(league=league, cache_dir=cache, cache_ttl_seconds=86400))
        for day in _dates(args.start_date, args.end_date):
            for ref in scoreboard_references(connector.scoreboard(day)):
                key = (league, str(ref["provider_match_id"]), str(ref["competition_id"]))
                refs[key] = {**ref, "league_slug": league}
        LOGGER.info("Descubrimiento completado league=%s refs=%s", league, sum(key[0] == league for key in refs))
    return [refs[key] for key in sorted(refs)]


def _selected(refs: list[dict[str, str]], limit: int) -> list[dict[str, str]]:
    """Aplica un límite independiente por liga para controlar el coste."""

    if limit < 0:
        raise ValueError("max_matches_per_league_must_be_nonnegative")
    counts: Counter[str] = Counter()
    selected: list[dict[str, str]] = []
    for ref in refs:
        league = str(ref["league_slug"])
        if not limit or counts[league] < limit:
            selected.append(ref)
            counts[league] += 1
    return selected


def _fetch(reference: dict[str, str], args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, str] | None]:
    """Descarga, normaliza y materializa un partido sin persistir payload crudo."""

    league = str(reference["league_slug"])
    try:
        if args.sleep_seconds:
            time.sleep(args.sleep_seconds)
        connector = EspnProspectiveConnector(EspnConnectorConfig(league=league, cache_dir=OUTPUT / "cache" / league, cache_ttl_seconds=86400))
        batch, _ = _normalize(connector, reference)
        identity = batch["identity"]
        if not identity.get("complete") or identity.get("home_score") is None or identity.get("away_score") is None:
            raise ValueError("match_not_complete")
        return _materialize(batch, league), None
    except (OSError, ValueError, RuntimeError) as error:
        return [], {"league_slug": league, "match_id": str(reference["provider_match_id"]), "reason": str(error)[:160]}


def _merge(new_rows: list[dict[str, Any]]) -> tuple[Path, int, int, str]:
    """Combina filas nuevas con el snapshot activo usando una clave estable."""

    current = resolve_active_snapshot()
    old = read_snapshot_rows(current)
    rows = {(int(row["match_id"]), int(row["team_id"]), int(row["window_index"])): row for row in old}
    for row in new_rows:
        rows[(int(row["match_id"]), int(row["team_id"]), int(row["window_index"]))] = row
    merged = [rows[key] for key in sorted(rows)]
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / "merged_event_windows.json"
    target.write_text(json.dumps(merged, indent=2, sort_keys=True), encoding="utf-8")
    return target, len(old), len(merged), hashlib.sha256(target.read_bytes()).hexdigest()


def _write(name: str, payload: Any) -> None:
    """Escribe un artefacto JSON sanitizado mediante reemplazo atómico."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / name
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temporary.replace(target)


def run(args: argparse.Namespace) -> dict[str, Any]:
    """Ejecuta el refresco y activa sólo cuando se solicita explícitamente."""

    global OUTPUT
    if getattr(args, "output_dir", None):
        OUTPUT = Path(str(args.output_dir)).resolve()
    if args.workers != 1:
        raise ValueError("workers_must_be_one_for_safe_refresh")
    leagues = _documented_leagues(args.league)
    refs = _references(leagues, args)
    selected = _selected(refs, args.max_matches_per_league)
    windows: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for reference in selected:
        rows, failure = _fetch(reference, args)
        windows.extend(rows)
        if failure:
            failures.append(failure)
    complete_matches = len({int(row["match_id"]) for row in windows})
    target, old_rows, merged_rows, merged_hash = _merge(windows)
    activation: dict[str, Any] | None = None
    manifest: dict[str, Any] | None = None
    if args.activate and complete_matches:
        manifest_obj = publish_snapshot(target, args.snapshot_id)
        manifest = manifest_obj.as_dict()
        activation = activate_snapshot(args.snapshot_id)
    classification = "multileague_snapshot_activated" if activation else "multileague_refresh_dry_run"
    if not complete_matches:
        classification = "multileague_no_new_complete_matches"
    result = {"classification": classification, "leagues": leagues, "references": len(refs), "selected": len(selected), "complete_matches": complete_matches, "new_windows": len(windows), "failures": failures, "old_rows": old_rows, "merged_rows": merged_rows, "merged_hash": merged_hash, "snapshot_id": args.snapshot_id if manifest else None, "manifest": manifest, "activation": activation, "dry_run": not bool(args.activate), "registry_write": bool(manifest), "postgresql_written": False, "raw_payloads_persisted": False, "evaluation_executed": False, "markov_promoted": False}
    _write("config.json", vars(args))
    _write("audit.json", result)
    _write("failures.json", failures)
    title = "Fase 57 — refresco incremental" if "phase_57_" in str(OUTPUT) else "Fase 53 — refresco multi-liga post-2025"
    report = [f"# {title}", "", f"**Clasificación:** `{classification}`", "", f"- ligas documentadas procesadas: `{len(leagues)}`", f"- referencias ESPN: `{len(refs)}`", f"- referencias seleccionadas: `{len(selected)}`", f"- partidos completos materializados: `{complete_matches}`", f"- ventanas nuevas: `{len(windows)}`", f"- fallos excluidos: `{len(failures)}`", f"- filas finales: `{merged_rows}`", f"- dry-run: `{not bool(args.activate)}`", f"- escritura del registro: `{bool(manifest)}`", f"- PostgreSQL escrito: `False`", "", "La activación sólo ocurre con `--activate` y con al menos un partido completo que supere la reconciliación de ventanas."]
    (OUTPUT / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"}
    _write("hashes.json", hashes)
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    try:
        outcome = run(_parser().parse_args())
    except (OSError, ValueError, RuntimeError) as error:
        LOGGER.error("Refresco multi-liga rechazado: %s", error)
        raise SystemExit(2) from error
    LOGGER.info("Fase 53: %s", outcome["classification"])

# Version: 1.0.0
# Created: 2026-07-27
