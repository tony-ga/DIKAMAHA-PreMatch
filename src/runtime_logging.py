"""Configuración de logs JSON para runtimes desplegados.

# Requirements:
#   python-json-logger>=2

Version: 1.0.0
Created: 2026-07-29
"""

from __future__ import annotations

import logging
import json
import os
from datetime import datetime, timezone


class RuntimeJsonFormatter(logging.Formatter):
    """Serializa metadatos operativos seguros como una línea JSON."""

    def format(self, record: logging.LogRecord) -> str:
        """Convierte un registro sin adjuntar variables de entorno."""

        payload = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

def configure_runtime_logging() -> None:
    """Configura stdout JSON sin incluir cuerpos ni secretos."""

    handler = logging.StreamHandler()
    handler.setFormatter(RuntimeJsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())


# Version: 1.0.0
# Created: 2026-07-29
