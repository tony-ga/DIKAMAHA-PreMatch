"""Pruebas del sistema visual compacto de Telegram."""
from __future__ import annotations

from src.telegram_bot import (
    MAX_MESSAGE_LENGTH,
    _format_market_period,
    _format_player,
    _format_prediction_summary,
    _format_readiness,
    _format_statistics,
    _plays_page,
)


def _prediction() -> dict:
    """Construye predicción mínima con mercados por periodo."""

    return {
        "fixture": {"home_team_name": "Cruz Azul", "away_team_name": "Pumas"},
        "kickoff_ts": "2026-08-01T01:00:00+00:00",
        "probability_home": 0.5, "probability_draw": 0.25,
        "probability_away": 0.25, "probability_over_2_5": 0.6,
        "probability_btts": 0.55,
        "experimental_team_markets": {
            "user_market_view": [{
                "metric": "shots", "team_side": "home",
                "period": "first_half", "line": 5.5,
                "probability": 0.62, "baseline_probability": 0.54,
                "source_model": "markov",
            }],
            "recommended_market_view": [{
                "metric": "corners", "team_side": "away",
                "period": "first_half", "line": 1.5,
                "direction": "over", "probability": 0.71,
                "baseline_probability": 0.64,
                "incremental_probability": 0.07,
            }],
        },
    }


def test_prediction_and_markets_use_monospace_tables() -> None:
    """Presenta probabilidades como tablas, no párrafos planos."""

    prediction = _format_prediction_summary(_prediction())
    markets = _format_market_period(_prediction(), "first_half")

    assert "<pre>" in prediction and "Mercado" in prediction
    assert "<pre>" in markets and "Prob." in markets
    assert "Modelo" not in markets and "Base" not in markets
    assert "Escenarios más probables" in markets
    assert "Pumas" in markets and "71.0%" in markets
    assert len(prediction) < MAX_MESSAGE_LENGTH


def test_statistics_compare_home_and_away_in_table() -> None:
    """Presenta estadísticas en columnas local/visitante."""

    fixture = {"home_team_name": "Local", "away_team_name": "Visita"}
    metrics = {"goals": 1, "shots": 9, "shots_on_target": 4, "corners": 3,
               "yellow_cards": 2, "red_cards": 0, "fouls": 8,
               "offsides": 1, "saves": 2, "substitutions": 3}
    payload = {
        "teams": {"home": {"name": "Local"}, "away": {"name": "Visita"}},
        "periods": {"home": {"first_half": metrics},
                    "away": {"first_half": metrics}},
        "boxscore": [],
    }

    message = _format_statistics(fixture, payload, "first_half")

    assert "<pre>" in message
    assert "Evento" in message and "L" in message and "V" in message
    assert len(message) < MAX_MESSAGE_LENGTH


def test_play_page_uses_visual_event_cards_and_pagination() -> None:
    """Evita bloques largos y conserva ocho eventos por página."""

    fixture = {"home_team_name": "Local", "away_team_name": "Visita"}
    rows = [{"type": "goal", "period": 1, "clock": "10'",
             "label": "Goal", "text": "Gol del equipo local"}] * 12

    message, keyboard = _plays_page(fixture, rows, "key", "g0", 0)

    assert message.count("⚽") == 8
    assert "PÁGINA 1/2" in message
    assert "▶" in str(keyboard)
    assert len(message) < MAX_MESSAGE_LENGTH


def test_player_profile_and_readiness_use_tables() -> None:
    """Aplica el formato tabular a perfiles y estado."""

    player = _format_player({
        "name": "Jugador", "position": "Forward", "age": 25,
        "height": "6' 0\"", "weight": "170 lbs", "citizenship": "Mexico",
        "active": True, "statistics": [
            {"name": "appearances", "value": "10"},
            {"name": "totalGoals", "value": "4"},
        ],
    })
    status = _format_readiness({"ready": True, "contract_version": "v1"})

    assert player.count("<pre>") == 2
    assert "<pre>" in status
    assert len(player) < MAX_MESSAGE_LENGTH


# Version: 1.0.0
# Created: 2026-07-29
