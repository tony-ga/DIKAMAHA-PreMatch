"""Configuración centralizada del proyecto."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


class SettingsError(ValueError):
    """Error de configuración cuando faltan variables obligatorias."""


@dataclass(slots=True)
class Settings:
    """Contenedor de configuración cargado desde variables de entorno."""

    DATABASE_URL: str
    CACHE_DIR: str = "cache"
    LOG_LEVEL: str = "INFO"
    USER_AGENT: str = "futbol-predictor/0.1"

    @classmethod
    def load(cls) -> "Settings":
        """Carga y valida la configuración desde el entorno.

        Raises:
            SettingsError: Si falta DATABASE_URL.

        Returns:
            Settings: Instancia validada de configuración.
        """

        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            raise SettingsError("DATABASE_URL es obligatoria y no está definida en .env o en el entorno.")
        cache_dir = os.getenv("CACHE_DIR", "cache")
        log_level = os.getenv("LOG_LEVEL", "INFO")
        user_agent = os.getenv("USER_AGENT", "futbol-predictor/0.1")
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        return cls(
            DATABASE_URL=database_url,
            CACHE_DIR=cache_dir,
            LOG_LEVEL=log_level,
            USER_AGENT=user_agent,
        )


settings = Settings.load()

