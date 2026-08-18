"""Gate de despliegue del bot premium Telegram en Railway."""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.run_phase_97_telegram_bot import _validate_premium_config
from src.telegram_bot import TelegramBotConfig, telegram_config_from_env

ROOT = Path(__file__).resolve().parents[1]


def _premium_config(**changes: object) -> TelegramBotConfig:
    """Construye una configuración premium válida con cambios opcionales."""

    values: dict[str, object] = {
        "token": "secret",
        "allowed_user_ids": frozenset({7}),
        "dikamaha_base_url": "https://api.example.test",
        "dikamaha_api_key": "private-key",
    }
    values.update(changes)
    return TelegramBotConfig(**values)


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"allowed_user_ids": frozenset()}, "allowlist"),
        ({"dikamaha_api_key": None}, "api_key"),
        ({"dikamaha_base_url": "http://api"}, "https"),
    ],
)
def test_premium_configuration_fails_closed(
    changes: dict[str, object], error: str,
) -> None:
    """Rechaza configuraciones que podrían abrir o romper el servicio."""

    with pytest.raises(ValueError, match=error):
        _validate_premium_config(_premium_config(**changes))


def test_public_configuration_does_not_require_allowlist() -> None:
    """Permite apertura explícita conservando HTTPS y API key."""

    config = _premium_config(
        access_mode="public", allowed_user_ids=frozenset())

    _validate_premium_config(config)


def test_invalid_access_mode_is_rejected() -> None:
    """Evita que un error tipográfico abra o cierre el bot silenciosamente."""

    with pytest.raises(ValueError, match="access_mode"):
        _premium_config(access_mode="everyone")


def test_access_mode_defaults_private_and_reads_public_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Congela el valor seguro por defecto y el interruptor de Railway."""

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "secret")
    monkeypatch.delenv("TELEGRAM_ACCESS_MODE", raising=False)
    assert telegram_config_from_env().access_mode == "private"
    monkeypatch.setenv("TELEGRAM_ACCESS_MODE", "PUBLIC")
    assert telegram_config_from_env().access_mode == "public"


def test_premium_image_is_minimal_and_non_root() -> None:
    """Impide empaquetar modelos y exige usuario sin privilegios."""

    dockerfile = (ROOT / "Dockerfile.telegram-bot").read_text(encoding="utf-8")
    assert "USER app" in dockerfile
    assert "run_phase_97_telegram_bot.py" in dockerfile
    assert "artifacts/" not in dockerfile
    assert "models.joblib" not in dockerfile


# Version: 1.1.0
# Created: 2026-07-30
