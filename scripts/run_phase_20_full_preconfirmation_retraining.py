"""Ejecutor de Fase 20: evaluación con histórico completo pre-confirmación."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.phase_14_dynamic_markov_recalibration import Phase14Config
from src.phase_20_full_preconfirmation_retraining import run


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run(Phase14Config(version="full_preconfirmation_retraining_v1"))
    raise SystemExit(0 if result["audit"]["classification"] in {"promising_unconfirmed", "rejected_for_revision"} else 1)

# Version: 1.0.0
# Created: 2026-07-26
