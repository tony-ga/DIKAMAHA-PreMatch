"""Captura el tramo inicial de 2025-26 sin escritura canónica."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.phase_11_historical_extension_fetch import Phase11Config, run


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run(Phase11Config(version="current_season_backfill_fetch_v1", phase_label="Fase 18", start_date="20250815", end_date="20251025", output_dir="artifacts/phase_18_current_season_backfill"))
    raise SystemExit(0 if result["classification"] == "validated_extension_available" else 1)

# Version: 1.0.0
# Created: 2026-07-26
