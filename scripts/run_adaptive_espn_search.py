"""Busca candidatos ESPN ampliando la ventana sólo cuando hace falta.

El proceso conserva la política de independencia: temporadas usadas por el
modelo se auditan, pero sus partidos no se convierten en evidencia nueva.

Version: 1.0.0
Created: 2026-07-26
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from src.espn_phase_7_15_r5 import run_range

LOGGER = logging.getLogger(__name__)
OUTPUT = ROOT / "artifacts/phase_30_adaptive_espn_search"


@dataclass(frozen=True, slots=True)
class SearchWindow:
    """Ventana temporal auditada por el buscador."""

    label: str
    start_date: str
    end_date: str


def _args() -> argparse.Namespace:
    """Define parámetros de búsqueda y persistencia."""

    parser = argparse.ArgumentParser(description="Búsqueda adaptativa ESPN por ventana y temporadas")
    parser.add_argument("--days-back", type=int, default=7)
    parser.add_argument("--fallback-years", default="2025,2024")
    parser.add_argument("--minimum-candidates", type=int, default=30)
    parser.add_argument("--league", default=os.getenv("DIKAMAHA_ESPN_LEAGUE", "esp.1"))
    parser.add_argument("--write-staging", action="store_true")
    parser.add_argument("--sleep-between-requests", type=float, default=0.25)
    return parser.parse_args()


def _years(value: str) -> list[int]:
    """Convierte años separados por coma y elimina duplicados."""

    output = []
    for item in value.split(","):
        year = int(item.strip())
        if year not in output: output.append(year)
    return output


def build_windows(today: date, days_back: int, fallback_years: list[int]) -> list[SearchWindow]:
    """Construye la ventana reciente y temporadas de respaldo."""

    recent = SearchWindow("recent_window", (today - timedelta(days=days_back)).strftime("%Y%m%d"), today.strftime("%Y%m%d"))
    unique_years = list(dict.fromkeys(fallback_years))
    seasons = [SearchWindow(f"season_{year}", f"{year}0101", f"{year}1231") for year in unique_years]
    return [recent, *seasons]


def _rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Extrae partidos fuente elegibles sin duplicar IDs."""

    block = result.get("eligible_matches") or {}
    return list(block.get("matches", [])) if isinstance(block, dict) else list(block)


def _hash(payload: Any) -> str:
    """Calcula un hash estable para el manifiesto."""

    raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _write(payload: dict[str, Any]) -> None:
    """Publica el resumen de búsqueda de forma determinista."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "search_result.json").write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    report = ["# Búsqueda adaptativa ESPN", "", f"**Clasificación:** `{payload['classification']}`", "", f"- ventanas consultadas: `{len(payload['windows'])}`", f"- candidatos fuente únicos: `{payload['candidate_count']}`", f"- mínimo solicitado: `{payload['minimum_candidates']}`", f"- staging write: `{payload['staging_write']}`", "- evaluación ejecutada: `False`", "- router modificado: `False`"]
    (OUTPUT / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(OUTPUT.iterdir()) if path.name != "hashes.json"}
    (OUTPUT / "hashes.json").write_text(json.dumps(hashes, indent=2, sort_keys=True), encoding="utf-8")


def run_search(
    windows: list[SearchWindow],
    minimum_candidates: int = 30,
    league: str = "esp.1",
    write_staging: bool = False,
    sleep_between_requests: float = 0.25,
) -> dict[str, Any]:
    """Ejecuta ventanas sucesivas y persiste sólo candidatos permitidos."""

    unique: dict[str, dict[str, Any]] = {}
    audits = []
    for window in windows:
        result = run_range(window.start_date, window.end_date, source_fetch=True, staging_write=False, dry_run=True, refresh_incomplete=True, max_concurrency=1, sleep_between_requests=sleep_between_requests, stop_on_error=False, league=league)
        rows = _rows(result)
        for row in rows: unique.setdefault(str(row.get("match_id")), row)
        audits.append({"window": asdict(window), "classification": result.get("classification"), "source_rows": len(rows), "source_fetch_ok": result.get("gates", {}).get("source_fetch_ok", False)})
        if len(unique) >= minimum_candidates: break
    write_result = []
    if write_staging and unique:
        for window in windows[:len(audits)]:
            write = run_range(window.start_date, window.end_date, source_fetch=True, staging_write=True, dry_run=False, refresh_incomplete=True, max_concurrency=1, sleep_between_requests=sleep_between_requests, stop_on_error=False, league=league)
            write_result.append({"window": asdict(window), "classification": write.get("classification"), "staging_write_ok": write.get("gates", {}).get("staging_write_ok", False)})
    classification = "source_candidates_found" if unique else "no_source_candidates_found"
    payload = {"classification": classification, "windows": audits, "candidate_count": len(unique), "candidate_match_ids": sorted(unique), "minimum_candidates": minimum_candidates, "staging_write": write_staging, "write_results": write_result, "evaluation_executed": False, "router_modified": False, "markets_promoted": False, "input_hash": _hash({"windows": [asdict(item) for item in windows], "league": league, "minimum_candidates": minimum_candidates})}
    _write(payload)
    LOGGER.info("Búsqueda ESPN: %s candidatos=%d", classification, len(unique))
    return payload


def main() -> int:
    """Ejecuta la búsqueda adaptativa configurada por CLI."""

    args = _args(); today = datetime.now(timezone.utc).date(); windows = build_windows(today, args.days_back, _years(args.fallback_years))
    run_search(windows, args.minimum_candidates, args.league, args.write_staging, args.sleep_between_requests)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0
# Created: 2026-07-26
