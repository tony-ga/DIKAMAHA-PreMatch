"""Pruebas del explorador ESPN de presentación."""
from __future__ import annotations

from src.espn_user_explorer import (
    _normal,
    _period_statistics,
    _play,
    _reconciled,
    _score_reconciled,
    _teams,
)


def _raw_play(
    play_type: str, period: int, team_id: int, text: str = "Evento",
) -> dict:
    """Construye un play Core mínimo."""

    return {
        "id": f"{play_type}-{period}-{team_id}",
        "type": {"type": play_type, "text": play_type},
        "clock": {"displayValue": "10'"},
        "period": {"number": period},
        "team": {"id": str(team_id)},
        "text": text,
    }


def test_play_normalization_preserves_period_clock_and_team() -> None:
    """Conserva identidad y temporalidad sin reinterpretar el evento."""

    row = _play(_raw_play("yellow-card", 1, 10))

    assert row is not None
    assert row["period"] == 1
    assert row["clock"] == "10'"
    assert row["team_id"] == "10"


def test_period_statistics_sum_exactly_to_total() -> None:
    """Exige total igual a la suma de ambas mitades."""

    plays = [
        _raw_play("shot-on-target", 1, 10),
        _raw_play("goal", 2, 10),
        _raw_play("corner-awarded", 2, 20),
    ]
    teams = {"home": {"id": "10"}, "away": {"id": "20"}}

    result = _period_statistics(plays, teams)

    assert result["home"]["total"]["shots"] == 2
    assert result["home"]["total"]["shots_on_target"] == 2
    assert result["away"]["second_half"]["corners"] == 1
    assert _reconciled(result)


def test_commercial_shots_include_goals_once() -> None:
    """Mantiene la semántica comercial de tiros definida en DEC-110."""

    result = _period_statistics(
        [_raw_play("goal", 1, 10)], {"home": {"id": "10"}, "away": {"id": "20"}})

    assert result["home"]["first_half"]["goals"] == 1
    assert result["home"]["first_half"]["shots"] == 1
    assert result["home"]["first_half"]["shots_on_target"] == 1


def test_header_goal_variants_reconcile_three_zero_score() -> None:
    """Cuenta goles de cabeza ESPN y reconcilia el 3-0 oficial."""

    plays = [
        _raw_play("goal---header", 1, 10),
        _raw_play("goal---header", 1, 10),
        _raw_play("goal", 1, 10),
    ]
    result = _period_statistics(
        plays, {"home": {"id": "10"}, "away": {"id": "20"}})

    assert result["home"]["total"]["goals"] == 3
    assert result["away"]["total"]["goals"] == 0
    assert _score_reconciled(result, {"home": 3, "away": 0})


def test_team_parser_keeps_provider_identity() -> None:
    """Extrae nombres e IDs exactos desde Site API."""

    payload = {"sports": [{"leagues": [{"teams": [{
        "team": {"id": "137", "displayName": "Cruz Azul"},
    }]}]}]}

    assert _teams(payload) == [{
        "id": "137", "name": "Cruz Azul", "abbreviation": "",
        "short_name": "", "location": "", "logo": None,
    }]


def test_team_search_normalization_ignores_accents() -> None:
    """La búsqueda manual encuentra América al escribir america."""

    assert _normal("América") == _normal("america")


# Version: 1.0.0
# Created: 2026-07-29
