"""Materializa ventanas del tramo inicial de la temporada 2025-26."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.phase_12_extension_windows_targets import Phase12Config, run


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run(Phase12Config(version="current_season_backfill_windows_v2", expected_extension_matches=95, cohort="phase18_current_season_candidate", source_dir="artifacts/phase_18_current_season_backfill", output_dir="artifacts/phase_19_current_season_windows", phase_label="Fase 19"))
    raise SystemExit(0 if result["audit"]["classification"] == "validated_for_target_revision" else 1)

# Version: 1.0.0
# Created: 2026-07-26
