"""Descubre partidos ESPN por múltiples ligas sin escribir PostgreSQL.

La fase sólo consulta ``scoreboard?dates=YYYYMMDD`` según el catálogo local.
La descarga de eventos y persistencia se ejecutarán en una fase posterior con
``league_slug`` obligatorio.

Version: 1.0.0
Created: 2026-07-26
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests

# Permite ejecutar el script directamente desde la raíz del proyecto.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.espn_prospective_connector import EspnConnectorConfig, EspnProspectiveConnector, scoreboard_references

LOGGER = logging.getLogger(__name__)
CATALOG = ROOT / "docs/league_catalog_v1.json"
OUTPUT = ROOT / "artifacts/phase_36_multileague_discovery"


@dataclass(frozen=True, slots=True)
class DiscoveryTask:
    """Unidad de consulta de una liga y fecha."""

    league: str
    date: str


def _load(path: Path) -> Any:
    """Carga JSON local."""

    return json.loads(path.read_text(encoding="utf-8"))


def _dates(start: str, end: str) -> list[str]:
    """Genera fechas inclusivas en formato ESPN."""

    first, last = datetime.strptime(start, "%Y%m%d").date(), datetime.strptime(end, "%Y%m%d").date()
    if first > last: raise ValueError("start_date_after_end_date")
    if (last - first).days > 366: raise ValueError("date_range_exceeds_366_days")
    return [(first + timedelta(days=index)).strftime("%Y%m%d") for index in range((last - first).days + 1)]


def _leagues(path: Path, requested: list[str] | None) -> list[str]:
    """Obtiene slugs habilitados y valida filtros explícitos."""

    rows = [row for row in _load(path)["leagues"] if bool(row.get("enabled"))]; available = {str(row["slug"]) for row in rows}
    if not requested: return sorted(available)
    unknown = sorted(set(requested) - available)
    if unknown: raise ValueError(f"unknown_leagues:{','.join(unknown)}")
    return list(dict.fromkeys(requested))


def _probe(task: DiscoveryTask, sleep_seconds: float) -> dict[str, Any]:
    """Consulta un scoreboard y devuelve referencias sanitizadas."""

    cache = OUTPUT / "cache" / task.league
    connector = EspnProspectiveConnector(EspnConnectorConfig(league=task.league, cache_dir=cache, cache_ttl_seconds=86400))
    try:
        if sleep_seconds: time.sleep(sleep_seconds)
        payload = connector.scoreboard(task.date); refs = scoreboard_references(payload)
        references = [{"league_slug": task.league, "date": task.date, **reference} for reference in refs]
        return {"league_slug": task.league, "date": task.date, "status": "ok", "scoreboard_events": len(payload.get("events", [])), "reference_count": len(references), "references": references}
    except (OSError, ValueError, RuntimeError, requests.RequestException) as error:
        LOGGER.warning("Discovery ESPN fallida league=%s date=%s: %s", task.league, task.date, error)
        return {"league_slug": task.league, "date": task.date, "status": "failed", "scoreboard_events": 0, "reference_count": 0, "references": [], "error_type": type(error).__name__}


def _reference_key(reference: dict[str, Any]) -> tuple[str, str, str]:
    """Construye la clave estable que identifica una referencia ESPN."""

    return (str(reference["league_slug"]), str(reference["provider_match_id"]), str(reference["competition_id"]))


def _merge_references(discovered: list[dict[str, Any]], replace: bool) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fusiona lo descubierto con el archivo acumulado sin perder ligas previas.

    Un descubrimiento incremental sólo consulta las ligas solicitadas. Escribir
    su resultado tal cual borraría las ligas descubiertas antes y rompería el
    gate ``_documented_leagues`` de Fase 53, que valida contra este archivo.
    """

    target = OUTPUT / "references.json"
    previous: list[dict[str, Any]] = []
    if target.exists() and not replace:
        loaded = _load(target)
        previous = [row for row in loaded if isinstance(row, dict) and row.get("league_slug")] if isinstance(loaded, list) else []
    merged = {_reference_key(row): row for row in previous}
    retained = len(merged)
    for reference in discovered: merged[_reference_key(reference)] = reference
    rows = [merged[key] for key in sorted(merged)]
    return rows, {"mode": "replace" if replace else "merge", "previous_reference_count": retained, "discovered_reference_count": len(discovered), "merged_reference_count": len(rows), "previous_leagues": sorted({str(row["league_slug"]) for row in previous}), "merged_leagues": sorted({str(row["league_slug"]) for row in rows})}


def _write(result: dict[str, Any]) -> None:
    """Publica discovery, cobertura, reporte y hashes."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / "references.json"
    if target.exists() and result["merge"]["mode"] == "merge": (OUTPUT / "references_previous.json").write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
    for name in ("config", "coverage", "date_results", "references", "audit", "merge"):
        (OUTPUT / f"{name}.json").write_text(json.dumps(result[name], indent=2, sort_keys=True, default=str), encoding="utf-8")
    report = ["# Fase 36 — descubrimiento multi-liga ESPN", "", f"**Clasificación:** `{result['classification']}`", "", f"- ligas consultadas: `{result['coverage']['league_count']}`", f"- fechas consultadas: `{result['coverage']['date_count']}`", f"- referencias descubiertas en esta corrida: `{result['coverage']['unique_reference_count']}`", f"- ligas con partidos: `{result['coverage']['leagues_with_references']}`", f"- modo de escritura: `{result['merge']['mode']}`", f"- referencias acumuladas: `{result['merge']['merged_reference_count']}`", f"- ligas acumuladas: `{len(result['merge']['merged_leagues'])}`", "- PostgreSQL escrito: `False`", "- eventos/play-by-play descargados: `False`"]
    (OUTPUT / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(OUTPUT.iterdir())
        if path.is_file() and path.name != "hashes.json"
    }
    (OUTPUT / "hashes.json").write_text(json.dumps(hashes, indent=2, sort_keys=True), encoding="utf-8")


def discover(start: str, end: str, leagues: list[str], workers: int = 8, sleep_seconds: float = 0.15, replace_references: bool = False) -> dict[str, Any]:
    """Descubre referencias en todas las ligas solicitadas sin persistencia."""

    tasks = [DiscoveryTask(league, value) for league in leagues for value in _dates(start, end)]
    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_probe, task, sleep_seconds) for task in tasks]
        for future in as_completed(futures): results.append(future.result())
    results.sort(key=lambda row: (row["league_slug"], row["date"])); unique = {}
    for result in results:
        for reference in result["references"]: unique.setdefault((reference["league_slug"], reference["provider_match_id"], reference["competition_id"]), reference)
    references = list(unique.values()); leagues_with_refs = sorted({row["league_slug"] for row in references}); coverage = {"league_count": len(leagues), "date_count": len(_dates(start, end)), "task_count": len(tasks), "failed_tasks": sum(row["status"] == "failed" for row in results), "unique_reference_count": len(references), "leagues_with_references": leagues_with_refs, "references_by_league": {league: sum(row["league_slug"] == league for row in references) for league in leagues}}
    raw_reference_count = sum(len(row["references"]) for row in results)
    audit = {"select_only": True, "postgres_write_statements": 0, "event_payloads_downloaded": False, "duplicate_references_removed": raw_reference_count - len(references), "raw_reference_count": raw_reference_count, "league_catalog_hash": hashlib.sha256(CATALOG.read_bytes()).hexdigest()}
    merged, merge_audit = _merge_references(references, replace_references)
    result = {"classification": "references_discovered" if references else "no_references_discovered", "config": {"version": "phase_36_multileague_discovery_v1", "start_date": start, "end_date": end, "leagues": leagues, "workers": workers, "sleep_seconds": sleep_seconds, "replace_references": replace_references, "scoreboard_endpoint": "site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard"}, "coverage": coverage, "date_results": results, "references": merged, "merge": merge_audit, "audit": audit}
    _write(result); LOGGER.info("Discovery multi-liga: %s descubiertas=%d acumuladas=%d", result["classification"], len(references), len(merged)); return result


def main() -> int:
    """Ejecuta discovery multi-liga según argumentos CLI."""

    parser = argparse.ArgumentParser(description="Discovery ESPN multi-liga read-only"); parser.add_argument("--start-date", default="20250101"); parser.add_argument("--end-date", default="20251231"); parser.add_argument("--leagues", help="slugs separados por coma"); parser.add_argument("--workers", type=int, default=8); parser.add_argument("--sleep-seconds", type=float, default=0.15); parser.add_argument("--replace-references", action="store_true", help="Reconstruye references.json desde cero; por defecto se fusiona."); args = parser.parse_args()
    if args.workers < 1 or args.sleep_seconds < 0: raise ValueError("invalid_workers_or_sleep")
    requested = [item.strip() for item in args.leagues.split(",")] if args.leagues else None
    discover(args.start_date, args.end_date, _leagues(CATALOG, requested), args.workers, args.sleep_seconds, args.replace_references)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0
# Created: 2026-07-26
