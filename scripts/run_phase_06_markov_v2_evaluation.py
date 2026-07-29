"""Ejecutor de la evaluación OOS independiente de Markov v2.

Version: 1.0.0
Created: 2026-07-26
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.evaluate_markov_v2_oos import run


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(0 if run()["classification"] in {"validated", "rejected_for_revision"} else 1)

# Version: 1.0.0
# Created: 2026-07-26
