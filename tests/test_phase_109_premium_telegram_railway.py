"""Gate de despliegue del bot premium Telegram en Railway."""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.run_phase_97_telegram_bot import _validate_premium_config
from src.telegram_bot import TelegramBotConfig
from src.telegram_channel_publisher import channel_prediction_messages

from tests.test_telegram_bot import FakeGateway, FakeTransport, _bot, _prediction, _update

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


def test_bot_uses_exact_channel_prediction_messages() -> None:
    """Comprueba paridad textual entre selección privada y canal."""

    transport, gateway = FakeTransport(), FakeGateway()
    _bot(transport, gateway).process_update(
        _update(1, "/partido esp.1 20300110 Real Madrid | Barcelona"))
    prediction = _prediction()
    fixture = {
        "league_slug": "esp.1", "match_id": 0, "competition_id": 0,
        "kickoff_ts": prediction["kickoff_ts"],
        "home_team_name": "Real Madrid", "away_team_name": "Barcelona",
    }

    expected = channel_prediction_messages(fixture, prediction)
    assert [text for _, text in transport.sent] == expected
    assert all(len(text) <= 3900 for text in expected)


def test_premium_image_is_minimal_and_non_root() -> None:
    """Impide empaquetar modelos y exige usuario sin privilegios."""

    dockerfile = (ROOT / "Dockerfile.telegram-bot").read_text(encoding="utf-8")
    assert "USER app" in dockerfile
    assert "run_phase_97_telegram_bot.py" in dockerfile
    assert "artifacts/" not in dockerfile
    assert "models.joblib" not in dockerfile


# Version: 1.0.0
# Created: 2026-07-30
