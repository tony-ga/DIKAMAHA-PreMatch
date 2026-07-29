"""Sincroniza ESPN con el staging prospectivo de forma operativa.

La escritura requiere ``--write-staging`` y sólo alcanza
``prospective_staging_v2``. El proceso nunca evalúa ni modifica el router.

Version: 1.0.0
Created: 2026-07-26
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dependencia fijada en el entorno
    load_dotenv = None

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if load_dotenv:
    load_dotenv(ROOT / ".env")

from src.espn_phase_7_15_r5 import run_range

LOGGER = logging.getLogger(__name__)


def _date(value: str) -> str:
    """Valida y normaliza una fecha operativa YYYYMMDD."""

    return datetime.strptime(value, "%Y%m%d").strftime("%Y%m%d")


def _defaults(days_back: int) -> tuple[str, str]:
    """Construye una ventana UTC corta para refresco diario."""

    today = datetime.now(timezone.utc).date()
    return (today - timedelta(days=days_back)).strftime("%Y%m%d"), today.strftime("%Y%m%d")


def _args() -> argparse.Namespace:
    """Define el contrato de ejecución operativa."""

    parser = argparse.ArgumentParser(description="Sincronización operativa ESPN → prospective_staging_v2")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--days-back", type=int, default=7)
    parser.add_argument("--league", default=os.getenv("DIKAMAHA_ESPN_LEAGUE", "esp.1"))
    parser.add_argument("--write-staging", action="store_true")
    parser.add_argument("--sleep-between-requests", type=float, default=0.5)
    parser.add_argument("--stop-on-error", action="store_true")
    return parser.parse_args()


def main() -> int:
    """Ejecuta la ventana operativa y reporta su clasificación."""

    args = _args()
    default_start, default_end = _defaults(args.days_back)
    start, end = _date(args.start_date or default_start), _date(args.end_date or default_end)
    result = run_range(start, end, source_fetch=True, staging_write=args.write_staging,
                       dry_run=not args.write_staging, refresh_incomplete=True,
                       max_concurrency=1, sleep_between_requests=args.sleep_between_requests,
                       stop_on_error=args.stop_on_error, league=args.league)
    LOGGER.info("Sincronización ESPN: %s", result["classification"])
    return 0 if result["classification"] != "range_ingestion_rejected_for_revision" else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0
# Created: 2026-07-26
