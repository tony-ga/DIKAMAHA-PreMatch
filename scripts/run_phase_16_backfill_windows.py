"""Materializa ventanas para la temporada histórica adicional."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.phase_12_extension_windows_targets import Phase12Config, run


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run(Phase12Config(version="historical_backfill_windows_v2", expected_extension_matches=380, cohort="phase15_backfill_candidate", source_dir="artifacts/phase_15_historical_backfill_fetch", output_dir="artifacts/phase_16_backfill_windows", phase_label="Fase 16"))
    raise SystemExit(0 if result["audit"]["classification"] == "validated_for_target_revision" else 1)

# Version: 1.0.0
# Created: 2026-07-26
