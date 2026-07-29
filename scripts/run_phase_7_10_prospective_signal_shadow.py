"""Entrada reproducible para Fase 7.10.

Version: 1.0.0
Created: 2026-07-16
"""

from __future__ import annotations

import logging

from src.run_prospective_signal_shadow import main


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0
# Created: 2026-07-16
