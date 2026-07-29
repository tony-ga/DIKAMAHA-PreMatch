"""Entrada reproducible para Fase 7.7.

Version: 1.0.0
Created: 2026-07-16
"""

from __future__ import annotations

import logging

from src.audit_event_label_coverage import main


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0
# Created: 2026-07-16
