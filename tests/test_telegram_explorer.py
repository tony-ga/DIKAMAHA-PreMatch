"""Pruebas de navegación compacta del explorador Telegram."""
from __future__ import annotations

from typing import Any

from src.telegram_bot import (
    PredictionGateway,
    TelegramBotConfig,
    TelegramPredictionBot,
    TelegramTransport,
    _format_market_period,
)
from src.telegram_mobile_layout import (
    keyboard_layout_issues,
    mobile_layout_issues,
)


class MenuTransport(TelegramTransport):
    """Transporte falso que conserva teclados."""

    def __init__(self) -> None:
        """Inicializa capturas vacías."""

        self.sent: list[tuple[int, str, dict[str, Any] | None]] = []
        self.answered: list[str] = []

    def get_updates(
        self, offset: int | None, timeout_seconds: int,
    ) -> list[dict[str, Any]]:
        """No entrega actualizaciones automáticas."""

        return []

    def send_message(
        self, chat_id: int, text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> None:
        """Conserva mensaje y teclado."""

        self.sent.append((chat_id, text, reply_markup))

    def answer_callback_query(self, callback_id: str) -> None:
        """Registra confirmación."""

        self.answered.append(callback_id)


class MenuGateway(PredictionGateway):
    """Gateway determinista para menús."""

    def __init__(self) -> None:
        """Inicializa filtros de próximos observados."""

        self.upcoming_calls: list[dict[str, Any]] = []

    def predict_fixture(self, payload: dict[str, Any]) -> dict[str, Any]:
        """No usado."""

        return {}

    def predict_upcoming(self, payload: dict[str, Any]) -> dict[str, Any]:
        """No usado."""

        return {}

    def readiness(self) -> dict[str, Any]:
        """Simula servicio listo."""

        return {"ready": True, "contract_version": "test"}

    def explorer_leagues(self) -> dict[str, Any]:
        """Devuelve dos ligas."""

        return {"leagues": [
            {"slug": "mex.1", "name": "Liga MX"},
            {"slug": "esp.1", "name": "LaLiga"},
        ]}

    def explorer_dates(self, mode: str = "past") -> dict[str, Any]:
        """Devuelve una fecha."""

        return {"dates": [{"date": "20260726", "label": "26/07"}]}

    def explorer_teams(
        self, league: str, query: str = "",
    ) -> dict[str, Any]:
        """Devuelve coincidencia de búsqueda."""

        rows = [{"id": "137", "name": "Cruz Azul"}] if "cruz" in query.casefold() else []
        return {"teams": rows}

    def list_upcoming(
        self, limit: int = 8, leagues: str | None = None,
        date: str | None = None,
    ) -> dict[str, Any]:
        """Devuelve fixtures de ligas distintas y registra filtros."""

        self.upcoming_calls.append({
            "limit": limit, "leagues": leagues, "date": date})
        return {"fixtures": [
            {"league_slug": "mex.1", "match_id": 1, "competition_id": "1",
             "home_team_id": 10, "away_team_id": 20,
             "home_team_name": "Cruz Azul", "away_team_name": "Pumas",
             "kickoff_ts": "2026-08-01T01:00:00+00:00"},
            {"league_slug": "esp.1", "match_id": 2, "competition_id": "2",
             "home_team_id": 30, "away_team_id": 40,
             "home_team_name": "Barcelona", "away_team_name": "Valencia",
             "kickoff_ts": "2026-08-01T20:00:00+00:00"},
        ]}


def _callback(data: str) -> dict[str, Any]:
    """Construye callback privado autorizado."""

    return {
        "update_id": 1,
        "callback_query": {
            "id": "cb-1", "data": data, "from": {"id": 7},
            "message": {"chat": {"id": 70, "type": "private"}},
        },
    }


def _message(text: str) -> dict[str, Any]:
    """Construye mensaje privado."""

    return {
        "update_id": 2,
        "message": {
            "chat": {"id": 70, "type": "private"},
            "from": {"id": 7}, "text": text,
        },
    }


def _bot() -> tuple[TelegramPredictionBot, MenuTransport]:
    """Construye bot y transporte falsos."""

    transport = MenuTransport()
    config = TelegramBotConfig("secret", frozenset({7}))
    return TelegramPredictionBot(config, transport, MenuGateway()), transport


def test_stats_navigation_reaches_league_and_date_buttons() -> None:
    """Navega módulo→liga→fecha sin pedir IDs."""

    bot, transport = _bot()
    bot.process_update(_callback("menu:stats"))
    assert "Liga MX" in str(transport.sent[-1][2])

    bot.process_update(_callback("league:stats:mex.1"))
    assert "26/07" in str(transport.sent[-1][2])
    assert transport.answered == ["cb-1", "cb-1"]


def test_upcoming_root_offers_all_league_and_date_routes() -> None:
    """Evita abrir directamente un catálogo dominado por una liga."""

    bot, transport = _bot()
    bot.process_update(_callback("menu:upcoming"))

    keyboard = str(transport.sent[-1][2])
    assert "Todos los próximos" in keyboard
    assert "Buscar por liga" in keyboard
    assert "Buscar por fecha" in keyboard


def test_all_upcoming_includes_multiple_leagues() -> None:
    """Consulta todas las ligas y muestra fixtures mezclados."""

    bot, transport = _bot()
    bot.process_update(_callback("upcoming:all"))

    keyboard = str(transport.sent[-1][2])
    assert "Cruz Azul" in keyboard
    assert "Barcelona" in keyboard


def test_upcoming_league_and_future_date_submenus() -> None:
    """Expone ligas y calendario futuro antes de consultar fixtures."""

    bot, transport = _bot()
    bot.process_update(_callback("upcoming:leagues"))
    assert "Liga MX" in str(transport.sent[-1][2])
    assert "LaLiga" in str(transport.sent[-1][2])

    bot.process_update(_callback("upcoming:dates"))
    assert "26/07" in str(transport.sent[-1][2])


def test_team_search_returns_only_matching_button() -> None:
    """La búsqueda por texto devuelve coincidencias navegables."""

    bot, transport = _bot()
    bot.process_update(_callback("teamsearch:mex.1"))
    bot.process_update(_message("Cruz A"))

    assert "Cruz Azul" in str(transport.sent[-1][2])
    assert "Coincidencias" in transport.sent[-1][1]


def test_market_period_uses_team_names_and_separates_halves() -> None:
    """Usa equipos reales y no mezcla líneas de 1T y 2T."""

    payload = {
        "fixture": {
            "home_team_name": "Cruz Azul", "away_team_name": "Pumas"},
        "experimental_team_markets": {"user_market_view": [
        {"metric": "shots", "team_side": "home", "period": "first_half",
         "line": 5.5, "probability": 0.6, "baseline_probability": 0.5,
         "source_model": "markov"},
        {"metric": "shots", "team_side": "home", "period": "second_half",
         "line": 5.5, "probability": 0.7, "baseline_probability": 0.6,
         "source_model": "markov"},
    ]}}

    first = _format_market_period(payload, "first_half")
    second = _format_market_period(payload, "second_half")

    assert "Primer tiempo" in first and "60.0%" in first
    assert "Cruz Azul" in first
    assert "local" not in first.casefold()
    assert "visitante" not in first.casefold()
    assert "70.0%" not in first
    assert "Segundo tiempo" in second and "70.0%" in second


def test_navigation_windows_and_buttons_are_mobile_safe() -> None:
    """Recorre los menús principales y audita texto y botones móviles."""

    bot, transport = _bot()
    for callback in (
        "menu:upcoming", "upcoming:all", "upcoming:leagues",
        "upcoming:dates", "menu:stats", "league:stats:mex.1",
        "menu:players", "league:players:mex.1",
    ):
        bot.process_update(_callback(callback))

    assert all(not mobile_layout_issues(text) for _, text, _ in transport.sent)
    assert all(
        not keyboard_layout_issues(keyboard)
        for _, _, keyboard in transport.sent)


# Version: 1.1.0
# Created: 2026-07-29
