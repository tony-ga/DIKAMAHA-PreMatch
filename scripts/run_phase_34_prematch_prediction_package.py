"""Ejecutor del paquete de predicciones prospectivas pre-match."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.phase_34_prematch_prediction_package import run


def main() -> int:
    """Ejecuta Fase 34 y devuelve estado operativo."""

    result = run()
    return 0 if result["classification"] in {"prematch_predictions_ready", "waiting_for_independent_cohort"} else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0
# Created: 2026-07-26
