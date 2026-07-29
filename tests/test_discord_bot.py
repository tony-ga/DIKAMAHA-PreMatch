"""Pruebas sin red del adaptador Discord."""
from __future__ import annotations

import pytest

pytest.importorskip("discord")

from src.discord_bot import (  # noqa: E402
    DiscordBotConfig,
    _authorized,
    _fixture_label,
    _fixture_payload,
    _historical_label,
    _market_text,
    _player_text,
    _plays_page_text,
    _prediction_embed,
    _statistics_text,
)


def _config() -> DiscordBotConfig:
    """Construye configuración privada mínima."""

    return DiscordBotConfig(
        token="test", application_id=1, guild_id=10,
        allowed_user_ids=frozenset({20}), allowed_guild_ids=frozenset({10}))


def test_allowlists_require_matching_user_and_guild() -> None:
    """Restringe menús a la identidad configurada."""

    config = _config()

    assert _authorized(config, 20, 10)
    assert not _authorized(config, 21, 10)
    assert not _authorized(config, 20, 11)


def test_fixture_contract_preserves_names_and_ids() -> None:
    """No reinterpreta el fixture al enviarlo a DIKAMAHA."""

    row = {
        "league_slug": "arg.1", "home_team_id": 17702,
        "away_team_id": 5, "home_team_name": "Deportivo Riestra",
        "away_team_name": "Boca Juniors",
        "kickoff_ts": "2026-07-26T19:30:00+00:00", "match_id": 401841447,
    }

    assert "Deportivo Riestra vs Boca Juniors" in _fixture_label(row)
    assert _fixture_payload(row)["match_id"] == 401841447


def test_prediction_embed_uses_real_team_names() -> None:
    """Muestra equipos en lugar de local/visitante."""

    payload = {
        "fixture": {
            "home_team_name": "Deportivo Riestra",
            "away_team_name": "Boca Juniors"},
        "probability_home": 0.4, "probability_draw": 0.3,
        "probability_away": 0.3,
    }
    embed = _prediction_embed(payload)

    assert embed.title == "Deportivo Riestra vs Boca Juniors"
    assert {field.name for field in embed.fields} >= {
        "Deportivo Riestra", "Boca Juniors", "Empate"}


def test_statistics_text_keeps_score_reconciled_names() -> None:
    """Resume goles con identidades reales."""

    payload = {
        "teams": {
            "home": {"name": "Deportivo Riestra"},
            "away": {"name": "Boca Juniors"}},
        "periods": {
            "home": {"total": {"goals": 3}},
            "away": {"total": {"goals": 0}}},
    }
    text = _statistics_text(payload)

    assert "Deportivo Riestra vs Boca Juniors" in text
    assert "Goles" in text and "3" in text


def test_market_text_uses_team_name_and_period() -> None:
    """Mantiene equipos reales y etiqueta shadow."""

    payload = {
        "fixture": {
            "home_team_name": "Deportivo Riestra",
            "away_team_name": "Boca Juniors"},
        "experimental_team_markets": {
            "user_market_view": [{
                "metric": "corners", "team_side": "home",
                "period": "first_half", "line": 2.5,
                "probability": 0.6, "baseline_probability": 0.5,
            }],
            "recommended_market_view": [{
                "metric": "shots", "team_side": "away",
                "period": "first_half", "line": 3.5,
                "direction": "over", "probability": 0.72,
                "baseline_probability": 0.65,
                "incremental_probability": 0.07,
            }],
        },
    }
    text = _market_text(payload, "first_half")

    assert "Deportivo Riestra" in text
    assert "Primer tiempo" in text
    assert "60.0%" in text
    assert "Boca Juniors" in text and "Δequipo +7.0pp" in text


def test_historical_fixture_and_play_page_preserve_score() -> None:
    """Muestra marcador y pagina eventos sin perder identidad."""

    fixture = {
        "home_team_name": "Deportivo Riestra",
        "away_team_name": "Boca Juniors", "home_score": 3,
        "away_score": 0, "kickoff_ts": "2026-07-26T19:30:00+00:00",
    }
    rows = [{"clock": "7'", "label": "Gol", "text": "Gol de cabeza"}]

    assert "3-0" in _historical_label(fixture)
    text = _plays_page_text(fixture, rows, "key", 0)
    assert "Deportivo Riestra vs Boca Juniors" in text
    assert "Gol de cabeza" in text


def test_player_profile_formats_available_statistics() -> None:
    """Presenta perfil y acumulados individuales."""

    payload = {
        "name": "Jugador Uno", "position": "Delantero", "age": 24,
        "statistics": [{"displayName": "Goles", "value": 8}],
    }
    text = _player_text(payload)

    assert "Jugador Uno" in text
    assert "Delantero" in text
    assert "Goles: 8" in text


# Version: 1.1.0
# Created: 2026-07-29
