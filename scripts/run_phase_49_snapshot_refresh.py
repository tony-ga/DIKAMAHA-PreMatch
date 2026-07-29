"""Refresca de forma explícita el staging prospectivo usado por DIKAMAHA.

El script no reemplaza el snapshot canónico ni ejecuta evaluación. Por defecto
consulta ESPN en modo dry-run; la escritura requiere ``--write-staging``.

Requirements:
    - requests
    - tenacity
    - psycopg2-binary (sólo con --write-staging)

Version: 1.0.0
Created: 2026-07-27
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.espn_phase_7_15_r5 import run_range

OUTPUT = ROOT / "artifacts/phase_49_fixture_resolver_snapshot_refresh_v1"
LOGGER = logging.getLogger(__name__)


def _write(name: str, payload: Any) -> None:
    """Publica un artefacto JSON con reemplazo atómico."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / name
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temporary.replace(target)


def _default_dates(days_back: int) -> tuple[str, str]:
    """Construye una ventana reciente UTC en formato ESPN."""

    if days_back < 0 or days_back > 366:
        raise ValueError("days_back_must_be_between_0_and_366")
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days_back)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def _parser() -> argparse.ArgumentParser:
    """Define la interfaz segura del refresco."""

    parser = argparse.ArgumentParser(description="Refresca staging prospectivo sin tocar el snapshot canónico.")
    parser.add_argument("--league", default=None, help="Slug ESPN; si falta usa DIKAMAHA_ESPN_LEAGUE.")
    parser.add_argument("--start-date", default=None, help="Inicio inclusivo YYYYMMDD.")
    parser.add_argument("--end-date", default=None, help="Fin inclusivo YYYYMMDD.")
    parser.add_argument("--days-back", type=int, default=7, help="Días hacia atrás cuando no se especifican fechas.")
    parser.add_argument("--write-staging", action="store_true", help="Autoriza escritura sólo en prospective_staging_v2.")
    parser.add_argument("--sleep-between-requests", type=float, default=0.25)
    parser.add_argument("--stop-on-error", action="store_true")
    return parser


def _dates(args: argparse.Namespace) -> tuple[str, str]:
    """Resuelve fechas explícitas o una ventana reciente."""

    if bool(args.start_date) != bool(args.end_date):
        raise ValueError("start_date_and_end_date_must_be_provided_together")
    return (args.start_date, args.end_date) if args.start_date else _default_dates(args.days_back)


def _classification(result: dict[str, Any], write_staging: bool) -> str:
    """Clasifica el refresco sin confundir fuente con snapshot materializado."""

    gates = result.get("gates", {})
    if gates.get("source_fetch_ok") and (not write_staging or gates.get("staging_write_ok")):
        return "refresh_staging_verified" if result.get("event_results") else "refresh_no_new_source"
    return "refresh_failed"


def _refresh(args: argparse.Namespace, start: str, end: str) -> dict[str, Any]:
    """Ejecuta el conector R5 con escritura y concurrencia explícitas."""

    saved_database_url = os.environ.pop("DATABASE_URL", None) if not args.write_staging else None
    try:
        return run_range(
            start, end, source_fetch=True, staging_write=args.write_staging,
            dry_run=not args.write_staging, refresh_incomplete=True,
            max_concurrency=1, sleep_between_requests=args.sleep_between_requests,
            stop_on_error=args.stop_on_error, league=args.league,
            prospective_cutoff_date=os.getenv("DIKAMAHA_PROSPECTIVE_CUTOFF_DATE"),
        )
    finally:
        if saved_database_url is not None:
            os.environ["DATABASE_URL"] = saved_database_url


def _config(args: argparse.Namespace, start: str, end: str) -> dict[str, Any]:
    """Construye la configuración pública del refresco."""

    return {
        "phase": "49",
        "start_date": start,
        "end_date": end,
        "league": args.league or os.getenv("DIKAMAHA_ESPN_LEAGUE", "esp.1"),
        "write_staging": args.write_staging,
        "dry_run": not args.write_staging,
        "canonical_snapshot_replaced": False,
        "evaluation_executed": False,
    }


def _audit(result: dict[str, Any], classification: str, write_staging: bool) -> dict[str, Any]:
    """Construye la auditoría causal y de alcance del refresco."""

    gates = result.get("gates", {})
    return {
        "classification": classification,
        "source_fetch_ok": gates.get("source_fetch_ok", False),
        "staging_write_ok": gates.get("staging_write_ok", False) if write_staging else False,
        "external_calls_are_explicit": True,
        "canonical_snapshot_replaced": False,
        "target_match_data_used": False,
        "evaluation_executed": False,
    }


def _report(config: dict[str, Any], result: dict[str, Any], classification: str) -> None:
    """Publica el reporte humano del refresco."""

    report = [
        "# Fase 49 — resolvedor de fixtures y refresco de snapshot", "",
        f"**Clasificación:** `{classification}`", "",
        f"- rango ESPN: `{config['start_date']}..{config['end_date']}`",
        f"- liga: `{config['league']}`",
        f"- escritura staging autorizada: `{config['write_staging']}`",
        f"- partidos normalizados: `{len(result.get('event_results', []))}`",
        "- snapshot canónico reemplazado: `False`",
        "- evaluación o entrenamiento ejecutados: `False`",
        "- siguiente paso: `materializar una versión de snapshot seleccionable por el servicio`",
    ]
    (OUTPUT / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")


def _hashes() -> None:
    """Publica hashes de los artefactos sin incluir el propio manifest."""

    payload = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"}
    _write("hashes.json", payload)


def run(args: argparse.Namespace) -> dict[str, Any]:
    """Ejecuta, audita y publica un refresco prospectivo explícito."""

    start, end = _dates(args)
    result = _refresh(args, start, end)
    classification = _classification(result, args.write_staging)
    config = _config(args, start, end)
    audit = _audit(result, classification, args.write_staging)
    _write("config.json", config)
    _write("source_result.json", result)
    _write("audit.json", audit)
    _report(config, result, classification)
    _hashes()
    return {"classification": classification, "config": config, "audit": audit}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    try:
        outcome = run(_parser().parse_args())
    except (ValueError, OSError, RuntimeError) as error:
        LOGGER.error("Refresco Fase 49 rechazado: %s", error)
        raise SystemExit(2) from error
    LOGGER.info("Fase 49: %s", outcome["classification"])

# Version: 1.0.0
# Created: 2026-07-27
