"""Ejecutor de la Fase 33 de materialización causal pre-match."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.phase_33_prematch_input_materialization import run


def main() -> int:
    """Ejecuta la materialización y devuelve código de estado."""

    result = run()
    return 0 if result["classification"] in {"prematch_inputs_ready", "waiting_for_independent_cohort"} else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0
# Created: 2026-07-26
