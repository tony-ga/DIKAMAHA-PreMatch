"""Entrada reproducible para Fase 7.11.

Version: 1.0.0
Created: 2026-07-16
"""

from __future__ import annotations

import logging

from src.collect_prospective_signals import main


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0
# Created: 2026-07-16
