"""Pruebas del adaptador Telegram shadow."""
from __future__ import annotations

from typing import Any

from src.telegram_bot import (
    LongPollingRunner,
    PredictionGateway,
    TelegramBotConfig,
    TelegramPredictionBot,
    TelegramTransport,
    _split_message,
)


class FakeTransport(TelegramTransport):
    """Transporte determinista sin red."""

    def __init__(self, updates: list[dict[str, Any]] | None = None) -> None:
        """Inicializa bandeja y mensajes enviados."""

        self.updates = updates or []
        self.sent: list[tuple[int, str]] = []
        self.offsets: list[int | None] = []

    def get_updates(
        self, offset: int | None, timeout_seconds: int,
    ) -> list[dict[str, Any]]:
        """Devuelve el lote configurado."""

        self.offsets.append(offset)
        return self.updates

    def send_message(self, chat_id: int, text: str) -> None:
        """Conserva la respuesta para assertions."""

        self.sent.append((chat_id, text))


class FakeGateway(PredictionGateway):
    """Gateway que registra payloads exactos."""

    def __init__(self) -> None:
        """Inicializa llamadas vacías."""

        self.fixture_payloads: list[dict[str, Any]] = []
        self.upcoming_payloads: list[dict[str, Any]] = []

    def predict_fixture(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Devuelve una predicción completa de ejemplo."""

        self.fixture_payloads.append(payload)
        return _prediction()

    def predict_upcoming(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Devuelve una predicción completa de ejemplo."""

        self.upcoming_payloads.append(payload)
        return _prediction()

    def readiness(self) -> dict[str, Any]:
        """Simula servicio listo."""

        return {"ready": True, "contract_version": "test-v1"}


def _prediction() -> dict[str, Any]:
    """Construye una respuesta mínima representativa."""

    return {
        "league_slug": "esp.1", "home_team_id": 1, "away_team_id": 2,
        "kickoff_ts": "2030-01-10T20:00:00+00:00",
        "probability_home": 0.5, "probability_draw": 0.25,
        "probability_away": 0.25, "probability_over_2_5": 0.55,
        "probability_btts": 0.52,
        "fixture": {
            "home_team_name": "Real Madrid", "away_team_name": "Barcelona"},
        "experimental_team_markets": {"user_market_view": [{
            "metric": "corners", "team_side": "home",
            "period": "full_match", "line": 4.5, "probability": 0.6,
            "baseline_probability": 0.5, "source_model": "phase84a",
        }]},
    }


def _update(update_id: int, text: str, user_id: int = 7) -> dict[str, Any]:
    """Construye un mensaje privado."""

    return {
        "update_id": update_id,
        "message": {
            "chat": {"id": 70, "type": "private"},
            "from": {"id": user_id}, "text": text,
        },
    }


def _bot(
    transport: FakeTransport, gateway: FakeGateway,
    allowed: frozenset[int] = frozenset({7}),
) -> TelegramPredictionBot:
    """Construye el bot con dependencias falsas."""

    config = TelegramBotConfig("secret", allowed)
    return TelegramPredictionBot(config, transport, gateway)


def test_whoami_does_not_require_authorization() -> None:
    """Permite descubrir el ID sin abrir predicciones."""

    transport, gateway = FakeTransport(), FakeGateway()
    _bot(transport, gateway, frozenset()).process_update(
        _update(1, "/whoami"))

    assert "7" in transport.sent[0][1]
    assert not gateway.fixture_payloads


def test_unauthorized_user_cannot_predict() -> None:
    """Bloquea inferencia fuera de la allowlist."""

    transport, gateway = FakeTransport(), FakeGateway()
    _bot(transport, gateway, frozenset()).process_update(
        _update(1, "/partido esp.1 20300110 Local | Visitante"))

    assert "prueba privada" in transport.sent[0][1]
    assert not gateway.fixture_payloads


def test_fixture_command_preserves_gateway_payload() -> None:
    """Delega nombres, liga y fecha sin reinterpretarlos."""

    transport, gateway = FakeTransport(), FakeGateway()
    _bot(transport, gateway).process_update(
        _update(1, "/partido esp.1 20300110 Real Madrid | Barcelona"))

    assert gateway.fixture_payloads == [{
        "league_slug": "esp.1", "kickoff_date": "20300110",
        "home_team_name": "Real Madrid", "away_team_name": "Barcelona",
    }]
    assert "Mercados adicionales" in transport.sent[0][1]
    assert "experimental" not in transport.sent[0][1].lower()
    assert "Real Madrid" in transport.sent[0][1]
    assert "Barcelona" in transport.sent[0][1]
    assert "local" not in transport.sent[0][1].casefold()
    assert "visitante" not in transport.sent[0][1].casefold()
    assert "antes del inicio" in transport.sent[0][1]


def test_upcoming_command_preserves_ids_and_kickoff() -> None:
    """Delega el contrato avanzado por IDs."""

    transport, gateway = FakeTransport(), FakeGateway()
    _bot(transport, gateway).process_update(
        _update(1, "/predict esp.1 94 86 2030-01-10T20:00:00+00:00 99"))

    assert gateway.upcoming_payloads[0] == {
        "league_slug": "esp.1", "home_team_id": 94,
        "away_team_id": 86, "kickoff_ts": "2030-01-10T20:00:00+00:00",
        "match_id": 99,
    }


def test_long_polling_ignores_duplicate_update() -> None:
    """Confirma cada update mediante offset monotónico."""

    updates = [_update(4, "/estado"), _update(4, "/estado")]
    transport, gateway = FakeTransport(updates), FakeGateway()
    runner = LongPollingRunner(_bot(transport, gateway), transport, 1)

    assert runner.poll_once() == 1
    assert len(transport.sent) == 1


def test_messages_are_split_below_telegram_limit() -> None:
    """Divide mensajes sin exceder el límite conservador."""

    parts = _split_message(("línea\n" * 1000).strip())

    assert len(parts) > 1
    assert all(len(part) <= 3900 for part in parts)


# Version: 1.0.0
# Created: 2026-07-29
