"""Ejecuta un refresco incremental reciente reutilizable y fail-closed.

Por defecto inspecciona los últimos días y permanece en dry-run. La activación
requiere ``--activate`` y genera un snapshot nuevo, sin sobrescribir versiones.

Requirements:
    - requests
    - tenacity

Version: 1.0.0
Created: 2026-07-27
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_phase_53_multileague_post2025_refresh import run as refresh_run

LOGGER = logging.getLogger(__name__)


def _parser() -> argparse.ArgumentParser:
    """Define el intervalo incremental y delega el resto al refresco seguro."""

    parser = argparse.ArgumentParser(description="Refresco incremental multi-liga con dry-run predeterminado.")
    parser.add_argument("--lookback-days", type=int, default=7)
    parser.add_argument("--end-date", default=date.today().strftime("%Y%m%d"))
    parser.add_argument("--max-matches-per-league", type=int, default=10)
    parser.add_argument("--sleep-seconds", type=float, default=0.2)
    parser.add_argument("--snapshot-id", default=f"phase57_incremental_v1_{date.today().strftime('%Y%m%d')}")
    parser.add_argument("--activate", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _args(source: argparse.Namespace) -> argparse.Namespace:
    """Convierte la configuración incremental al contrato de Fase 53."""

    if source.lookback_days < 1 or source.lookback_days > 31:
        raise ValueError("lookback_days_out_of_range")
    end = date.fromisoformat(f"{source.end_date[:4]}-{source.end_date[4:6]}-{source.end_date[6:]}")
    start = end - timedelta(days=source.lookback_days - 1)
    return argparse.Namespace(league=None, start_date=start.strftime("%Y%m%d"), end_date=source.end_date, max_matches_per_league=source.max_matches_per_league, sleep_seconds=source.sleep_seconds, workers=1, snapshot_id=source.snapshot_id, activate=source.activate, dry_run=source.dry_run, output_dir=str(ROOT / "artifacts/phase_57_incremental_snapshot_refresh_v1"))


def run(source: argparse.Namespace) -> dict[str, object]:
    """Ejecuta el refresco incremental mediante el motor validado."""

    return refresh_run(_args(source))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    try:
        LOGGER.info("Fase 57: %s", run(_parser().parse_args())["classification"])
    except (OSError, RuntimeError, ValueError) as error:
        LOGGER.error("Refresco incremental rechazado: %s", error)
        raise SystemExit(2) from error

# Version: 1.0.0
# Created: 2026-07-27
