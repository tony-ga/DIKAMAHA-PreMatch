"""Pruebas del adaptador Telegram shadow."""
from __future__ import annotations

from typing import Any

from src.telegram_bot import (
    LongPollingRunner,
    PredictionGateway,
    TelegramBotConfig,
    TelegramHttpTransport,
    TelegramPredictionBot,
    TelegramTransport,
    _split_message,
)
from src.telegram_mobile_layout import mobile_layout_issues


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
        self.live_payloads: list[dict[str, Any]] = []

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

    def list_live(
        self, limit: int = 12, leagues: str | None = None,
    ) -> dict[str, Any]:
        """Devuelve un partido activo navegable."""

        return {"fixtures": [{
            "league_slug": "esp.1", "match_id": 900001,
            "home_team_id": 1, "away_team_id": 2,
            "home_team_name": "Real Madrid",
            "away_team_name": "Barcelona",
            "home_score": 1, "away_score": 0, "display_clock": "32'",
        }]}

    def predict_live_fixture(
        self, payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Registra selección y devuelve las tres capas live."""

        self.live_payloads.append(payload)
        return _live_prediction()

    def models(self) -> dict[str, Any]:
        """Expone inventario oficial y shadow."""

        return {"models": [
            {"name": "Dixon-Coles + Kalman", "mode": "official"},
            {"name": "Motor probabilístico Live v1", "mode": "official"},
            {"name": "Markov Live v1", "mode": "compatibility_fallback"},
            {"name": "Hawkes Live v2 residual", "mode": "official_component"},
        ], "hawkes_policy": {
            "allowed_league_count": 17, "rho_goal": 1.0,
            "rho_next_event": 0.0,
        }}


class _Response:
    """Respuesta mínima para probar el transporte sin red."""

    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, Any]:
        """Devuelve el cuerpo Telegram configurado."""

        return self._payload


class _Session:
    """Sesión que rechaza HTML y acepta el reintento plano."""

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def post(
        self, _url: str, json: dict[str, Any], timeout: tuple[int, int],
    ) -> _Response:
        """Registra payloads y simula dos respuestas consecutivas."""

        self.payloads.append(json)
        if len(self.payloads) == 1:
            return _Response(400, {
                "ok": False, "description": "can't parse entities"})
        return _Response(200, {"ok": True, "result": {}})


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


def _live_prediction() -> dict[str, Any]:
    """Construye una inferencia live complementaria representativa."""

    markets = {
        "probability_home": 0.62, "probability_draw": 0.24,
        "probability_away": 0.14, "probability_over_2_5": 0.51,
        "probability_btts": 0.44,
    }
    next_event = {
        "horizon_minutes": 5.0,
        "probabilities": {"home:goal": 0.12, "away:goal": 0.06},
        "probability_no_event": 0.82,
    }
    return {
        "status": "live_predicted",
        "official_source": "live_probability_engine_v1",
        "fixture": {
            "league_slug": "esp.1", "match_id": 900001,
            "home_team_name": "Real Madrid", "away_team_name": "Barcelona",
            "score_home": 1, "score_away": 0,
            "match_clock_seconds": 1920.0,
        },
        "prior": {"status": "reconstructed_causal_prematch_prior"},
        "official_live_prediction": {
            "status": "official", "markets": markets,
            "remaining_intensities": {"home": 1.2, "away": 0.5},
            "next_event": next_event,
            "fallback": {"applied": False},
        },
        "live_probability_engine": {
            "audit": {"passed": True},
            "ctmc": {"dominant_regime": "home_pressure"},
            "hazard": {"multipliers": {"home": 1.1, "away": 0.9}},
            "dynamic_elo": {"multipliers": {"home": 1.03, "away": 0.97}},
            "hawkes_residual": {"status": "official_component_residual"},
            "monte_carlo_diagnostic": {"status": "scheduled", "simulations": 20000},
        },
        "experimental_markov_live": {
            "status": "experimental_shadow_not_promoted",
            "markets": markets, "next_event": next_event,
            "state": {"dominant": "home_pressure"},
            "lambda_remaining_home": 1.2, "lambda_remaining_away": 0.5,
        },
        "experimental_hawkes_residual": {
            "status": "experimental_shadow_not_promoted",
            "stability": {"subcritical": True},
        },
        "experimental_combined_live": {
            "status": "experimental_shadow_not_promoted",
            "markets": markets, "next_event": next_event,
            "fallback_exact_markov_live": False,
        },
        "hawkes_league_admission": {
            "admitted": True, "fallback_exact_markov_live": False,
        },
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


def _callback(update_id: int, data: str, user_id: int = 7) -> dict[str, Any]:
    """Construye un callback privado."""

    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"cb-{update_id}", "data": data, "from": {"id": user_id},
            "message": {"chat": {"id": 70, "type": "private"}},
        },
    }


def _bot(
    transport: FakeTransport, gateway: FakeGateway,
    allowed: frozenset[int] = frozenset({7}),
    access_mode: str = "private",
    rate_limit: int = 10,
) -> TelegramPredictionBot:
    """Construye el bot con dependencias falsas."""

    config = TelegramBotConfig(
        "secret", allowed, access_mode=access_mode,
        rate_limit_requests=rate_limit)
    return TelegramPredictionBot(config, transport, gateway)


def test_whoami_does_not_require_authorization() -> None:
    """Permite descubrir el ID sin abrir predicciones."""

    transport, gateway = FakeTransport(), FakeGateway()
    _bot(transport, gateway, frozenset()).process_update(
        _update(1, "/whoami"))

    assert "7" in transport.sent[0][1]
    assert not gateway.fixture_payloads


def test_help_is_available_and_does_not_require_authorization() -> None:
    """Entrega ayuda completa a cualquier chat privado."""

    transport, gateway = FakeTransport(), FakeGateway()
    _bot(transport, gateway, frozenset()).process_update(_update(1, "/help"))

    assert len(transport.sent) == 1
    assert "COMANDOS PRINCIPALES" in transport.sent[0][1]
    assert "Play-by-play" in transport.sent[0][1]
    assert "TELEGRAM_BOT_TOKEN" not in transport.sent[0][1]
    assert "CONFIGURACIÓN (env vars)" not in transport.sent[0][1]
    assert not gateway.fixture_payloads


def test_start_shows_welcome_instead_of_full_help() -> None:
    """El inicio abre el menú y deja la ayuda bajo demanda."""

    transport, gateway = FakeTransport(), FakeGateway()
    _bot(transport, gateway, frozenset()).process_update(_update(1, "/start"))

    assert "Bienvenido" in transport.sent[0][1]
    assert "COMANDOS PRINCIPALES" not in transport.sent[0][1]
    assert not gateway.fixture_payloads


def test_transport_retries_rejected_html_as_plain_text() -> None:
    """Evita que un HTML rechazado deje bloqueado el polling."""

    session = _Session()
    config = TelegramBotConfig("secret", frozenset({7}))
    TelegramHttpTransport(config, session).send_message(
        70, "<b>Ayuda</b> &amp; opciones", {"inline_keyboard": []})

    assert len(session.payloads) == 2
    assert session.payloads[0]["parse_mode"] == "HTML"
    assert "parse_mode" not in session.payloads[1]
    assert session.payloads[1]["text"] == "Ayuda & opciones"


def test_unauthorized_user_cannot_predict() -> None:
    """Bloquea inferencia fuera de la allowlist."""

    transport, gateway = FakeTransport(), FakeGateway()
    _bot(transport, gateway, frozenset()).process_update(
        _update(1, "/partido esp.1 20300110 Local | Visitante"))

    assert "ACCESO PREMIUM REQUERIDO" in transport.sent[0][1]
    assert not gateway.fixture_payloads


def test_public_mode_allows_any_private_user() -> None:
    """Abre consultas privadas sin requerir una allowlist."""

    transport, gateway = FakeTransport(), FakeGateway()
    _bot(
        transport, gateway, frozenset(), access_mode="public",
    ).process_update(
        _update(1, "/partido esp.1 20300110 Real Madrid | Barcelona", 999))

    assert len(gateway.fixture_payloads) == 1
    assert "PRONÓSTICO DIKAMAHA" in transport.sent[0][1]


def test_public_mode_keeps_per_user_rate_limit() -> None:
    """Impide que la apertura pública retire el control de ráfagas."""

    transport, gateway = FakeTransport(), FakeGateway()
    bot = _bot(
        transport, gateway, frozenset(), access_mode="public", rate_limit=1)
    command = "/partido esp.1 20300110 Real Madrid | Barcelona"
    bot.process_update(_update(1, command, 999))
    bot.process_update(_update(2, command, 999))

    assert len(gateway.fixture_payloads) == 1
    assert "Demasiadas solicitudes" in transport.sent[-1][1]


def test_public_mode_limits_callback_navigation() -> None:
    """Aplica el mismo control a botones y no sólo a comandos escritos."""

    transport, gateway = FakeTransport(), FakeGateway()
    bot = _bot(
        transport, gateway, frozenset(), access_mode="public", rate_limit=1)
    bot.process_update(_callback(1, "menu:upcoming", 999))
    bot.process_update(_callback(2, "menu:upcoming", 999))

    assert "PRÓXIMOS Y PREDICCIONES" in transport.sent[0][1]
    assert "Demasiadas solicitudes" in transport.sent[-1][1]


def test_public_mode_ignores_group_messages() -> None:
    """Mantiene el bot público limitado a conversaciones privadas."""

    update = _update(1, "/partidos", 999)
    update["message"]["chat"]["type"] = "group"
    transport, gateway = FakeTransport(), FakeGateway()
    _bot(
        transport, gateway, frozenset(), access_mode="public",
    ).process_update(update)

    assert not transport.sent


def test_fixture_command_preserves_gateway_payload() -> None:
    """Delega nombres, liga y fecha sin reinterpretarlos."""

    transport, gateway = FakeTransport(), FakeGateway()
    _bot(transport, gateway).process_update(
        _update(1, "/partido esp.1 20300110 Real Madrid | Barcelona"))

    assert gateway.fixture_payloads == [{
        "league_slug": "esp.1", "kickoff_date": "20300110",
        "home_team_name": "Real Madrid", "away_team_name": "Barcelona",
    }]
    assert "PRONÓSTICO DIKAMAHA" in transport.sent[0][1]
    assert "MERCADOS DISPONIBLES" in transport.sent[1][1]
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


def test_live_command_exposes_official_composed_engine() -> None:
    """Hace visible el motor oficial y mantiene transparentes sus capas."""

    transport, gateway = FakeTransport(), FakeGateway()
    bot = _bot(transport, gateway)
    bot.process_update(_update(1, "/en_vivo"))
    bot.process_update(_callback(2, "live:l0"))

    assert gateway.live_payloads == [{
        "league_slug": "esp.1", "match_id": 900001,
    }]
    message = transport.sent[-1][1]
    assert "Motor probabilístico Live v1" in message
    assert "Poisson dinámico" in message
    assert "CTMC" in message
    assert "Hawkes" in message
    assert "OFICIAL" in message
    assert "Auditoría matemática" in message
    assert not mobile_layout_issues(message)
    assert len(message) <= 3900


def test_models_command_exposes_official_and_shadow_models() -> None:
    """No oculta modelos operativos por conservar estado shadow."""

    transport, gateway = FakeTransport(), FakeGateway()
    _bot(transport, gateway).process_update(_update(1, "/modelos"))

    message = transport.sent[-1][1]
    assert "Dixon-Coles + Kalman" in message
    assert "Motor probabilístico Live v1" in message
    assert "Markov Live v1" in message
    assert "Hawkes Live v2 residual" in message
    assert "Shadow" in message
    assert not mobile_layout_issues(message)


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


# Version: 1.1.0
# Created: 2026-07-29
