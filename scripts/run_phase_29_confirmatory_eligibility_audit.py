"""Ejecutor reproducible de la auditoría de elegibilidad de Fase 29."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.phase_29_confirmatory_eligibility_audit import run


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()

# Version: 1.0.0
# Created: 2026-07-26
