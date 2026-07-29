"""Amplía el snapshot activo con partidos completos posteriores a 2025.

La fuente es ESPN, no PostgreSQL: esto permite refrescar aunque el servicio de
staging esté detenido. Sólo se conservan partidos con marcador, timeline y
reconciliación de goles; los payloads crudos se descartan después de construir
las ventanas.

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
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.espn_phase_7_15_r3 import _normalize
from src.espn_prospective_connector import EspnConnectorConfig, EspnProspectiveConnector, scoreboard_references
from src.event_windows_v1 import EventWindowsConfig, build_windows
from src.prematch_snapshot_registry import activate_snapshot, publish_snapshot, resolve_active_snapshot
from scripts.run_phase_38_multileague_event_windows import _is_shootout, _mismatches

OUTPUT = ROOT / "artifacts/phase_52_post2025_snapshot_refresh_v1"
LOGGER = logging.getLogger(__name__)


def _write(name: str, payload: Any) -> None:
    """Publica un artefacto JSON sanitizado de forma atómica."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / name
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temporary.replace(target)


def _parser() -> argparse.ArgumentParser:
    """Define el refresco acotado por liga y rango."""

    parser = argparse.ArgumentParser(description="Refresca un snapshot multi-liga con partidos ESPN post-2025.")
    parser.add_argument("--league", default="mex.1")
    parser.add_argument("--start-date", default="20260101")
    parser.add_argument("--end-date", default="20260727")
    parser.add_argument("--max-matches", type=int, default=0)
    parser.add_argument("--sleep-seconds", type=float, default=0.2)
    parser.add_argument("--snapshot-id", default="phase52_post2025_mex_v1_20260727")
    return parser


def _dates(start: str, end: str) -> list[str]:
    """Genera fechas ESPN inclusivas y valida un rango acotado."""

    first, last = date.fromisoformat(f"{start[:4]}-{start[4:6]}-{start[6:]}") , date.fromisoformat(f"{end[:4]}-{end[4:6]}-{end[6:]}")
    if first > last or (last - first).days > 366:
        raise ValueError("invalid_refresh_date_range")
    return [(first + timedelta(days=i)).strftime("%Y%m%d") for i in range((last - first).days + 1)]


def _references(config: argparse.Namespace) -> list[dict[str, str]]:
    """Descubre y deduplica referencias de la liga."""

    connector = EspnProspectiveConnector(EspnConnectorConfig(league=config.league, cache_dir=OUTPUT / "cache", cache_ttl_seconds=86400))
    refs: dict[tuple[str, str], dict[str, str]] = {}
    for day in _dates(config.start_date, config.end_date):
        board = connector.scoreboard(day)
        for ref in scoreboard_references(board):
            refs[(str(ref["provider_match_id"]), str(ref["competition_id"]))] = {**ref, "league_slug": config.league}
    return [refs[key] for key in sorted(refs)]


def _match(batch: dict[str, Any], league: str) -> dict[str, Any]:
    """Convierte identidad ESPN al contrato de ventanas."""

    identity = batch["identity"]
    return {"match_id": int(identity["provider_match_id"]), "competition_id": str(identity["competition_id"]), "home_team_id": int(identity["home_provider_team_id"]), "away_team_id": int(identity["away_provider_team_id"]), "match_date": str(identity["kickoff_ts"]), "season": str(identity["kickoff_ts"][:4]), "home_score": int(identity["home_score"]), "away_score": int(identity["away_score"]), "league_slug": league}


def _events(batch: dict[str, Any]) -> list[dict[str, Any]]:
    """Convierte eventos normalizados sin conservar payload crudo."""

    rows = []
    for event in batch["events"]:
        raw = event.get("raw_data") if isinstance(event.get("raw_data"), dict) else {}
        source = {"event_type_raw": event.get("event_type_raw"), "event_text": raw.get("text")}
        rows.append({"event_id": int(event["event_index"]), "match_id": int(event["provider_match_id"]), "minute": int(event.get("minute") or 0), "second": int(event.get("second") or 0), "team_id": event.get("team_provider_id"), "event_type": "penalty_shootout" if _is_shootout(source) else str(event.get("event_type") or "unclassified"), "annulled": bool(event.get("annulled", False))})
    return rows


def _materialize(batch: dict[str, Any], league: str) -> list[dict[str, Any]]:
    """Construye y reconcilia las doce ventanas de un partido."""

    match = _match(batch, league)
    events = _events(batch)
    if not events:
        raise ValueError("match_timeline_empty")
    windows, _ = build_windows([match], events, EventWindowsConfig(competition_id=league, competition_name=league))
    for window in windows:
        window["league_slug"] = league
    if _mismatches(windows, [match]):
        raise ValueError("window_score_mismatch")
    return windows


def _merge(old_path: Path, new_rows: list[dict[str, Any]]) -> tuple[Path, int, str]:
    """Combina ventanas sin duplicar partido, equipo ni ventana."""

    old_rows = json.loads(old_path.read_text(encoding="utf-8"))
    rows = { (int(row["match_id"]), int(row["team_id"]), int(row["window_index"])): row for row in old_rows }
    for row in new_rows:
        rows[(int(row["match_id"]), int(row["team_id"]), int(row["window_index"]))] = row
    merged = [rows[key] for key in sorted(rows)]
    target = OUTPUT / "merged_event_windows.json"
    target.write_text(json.dumps(merged, indent=2, sort_keys=True), encoding="utf-8")
    return target, len(old_rows), hashlib.sha256(target.read_bytes()).hexdigest()


def run(args: argparse.Namespace) -> dict[str, Any]:
    """Descarga, materializa, publica y activa una versión post-2025."""

    refs = _references(args)
    selected = refs[: args.max_matches] if args.max_matches else refs
    windows, failures = [], []
    for reference in selected:
        try:
            if args.sleep_seconds:
                time.sleep(args.sleep_seconds)
            connector = EspnProspectiveConnector(EspnConnectorConfig(league=args.league, cache_dir=OUTPUT / "cache", cache_ttl_seconds=86400))
            batch, public = _normalize(connector, reference)
            if not batch["identity"].get("complete") or batch["identity"].get("home_score") is None or batch["identity"].get("away_score") is None:
                raise ValueError("match_not_complete")
            windows.extend(_materialize(batch, args.league))
        except (OSError, ValueError, RuntimeError) as error:
            failures.append({"match_id": reference["provider_match_id"], "reason": str(error)[:160]})
    current = resolve_active_snapshot()
    merged_path, old_rows, merged_hash = _merge(current, windows)
    manifest = publish_snapshot(merged_path, args.snapshot_id)
    activation = activate_snapshot(args.snapshot_id)
    result = {"classification": "post2025_snapshot_activated" if windows else "post2025_snapshot_no_new_complete_matches", "references": len(refs), "selected": len(selected), "new_matches": len(windows) // 12, "new_windows": len(windows), "failures": failures, "old_rows": old_rows, "merged_rows": len(json.loads(merged_path.read_text(encoding="utf-8"))), "merged_hash": merged_hash, "manifest": manifest.as_dict(), "activation": activation, "postgresql_written": False, "raw_payloads_persisted": False, "evaluation_executed": False, "markov_promoted": False}
    _write("config.json", vars(args))
    _write("audit.json", result)
    report = ["# Fase 52 — refresco post-2025 del snapshot", "", f"**Clasificación:** `{result['classification']}`", "", f"- liga: `{args.league}`", f"- referencias ESPN: `{len(refs)}`", f"- partidos completos añadidos: `{result['new_matches']}`", f"- ventanas añadidas: `{result['new_windows']}`", f"- fallos excluidos: `{len(failures)}`", f"- filas finales: `{result['merged_rows']}`", f"- snapshot activo: `{args.snapshot_id}`", "- PostgreSQL escrito: `False`", "- evaluación ejecutada: `False`"]
    (OUTPUT / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"}
    _write("hashes.json", hashes)
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    try:
        outcome = run(_parser().parse_args())
    except (OSError, ValueError, RuntimeError) as error:
        LOGGER.error("Refresco post-2025 rechazado: %s", error)
        raise SystemExit(2) from error
    LOGGER.info("Fase 52: %s", outcome["classification"])

# Version: 1.0.0
# Created: 2026-07-27
