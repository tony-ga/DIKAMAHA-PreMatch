"""Ejecutor de la evaluación confirmatoria independiente."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.phase_35_confirmatory_evaluation import run


def main() -> int:
    """Ejecuta scoring post-match sin promoción automática."""

    result = run()
    return 0 if result["classification"] in {"confirmatory_evaluation_complete", "confirmatory_evaluation_insufficient_support", "waiting_for_prematch_predictions"} else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0
# Created: 2026-07-26
