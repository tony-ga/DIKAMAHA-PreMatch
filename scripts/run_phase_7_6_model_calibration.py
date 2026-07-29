"""Entrada reproducible para Fase 7.6.

Requirements:
    - numpy
    - pandas
    - SQLAlchemy==2.0.41
    - psycopg2-binary==2.9.10

Version: 1.0.0
Created: 2026-07-16
"""

from __future__ import annotations

import logging

from src.calibrate_inplay_models import main


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0
# Created: 2026-07-16
