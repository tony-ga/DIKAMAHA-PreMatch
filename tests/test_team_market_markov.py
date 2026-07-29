"""Pruebas del Markov pre-match para mercados por equipo.

Version: 1.0.0
Created: 2026-07-28
"""
from __future__ import annotations

from typing import Any

import pytest

from scripts.run_phase_88_team_market_markov import _commercial_row
from src.team_market_markov import (
    MARKET_LINES,
    METRICS,
    TeamMarketMarkov,
    market_name,
    state_for,
)


def _match(match_id: int, value: int = 0) -> dict[str, Any]:
    """Construye un partido mínimo de seis ventanas por equipo."""

    windows = [
        {"corners": value, "shots": value, "yellow_cards": value}
        for _ in range(6)
    ]
    return {
        "match_id": match_id,
        "league_slug": "test.1",
        "home_team_id": 10,
        "away_team_id": 20,
        "home": windows,
        "away": windows,
    }


def test_states_have_fixed_metric_semantics() -> None:
    """Valida límites de estado sin cuantiles retrospectivos."""

    assert [state_for("corners", value) for value in (0, 1, 2)] == [0, 1, 2]
    assert [state_for("yellow_cards", value) for value in (0, 1, 3)] == [0, 1, 2]
    assert [state_for("shots", value) for value in (1, 3, 4)] == [0, 1, 2]


def test_predictions_are_valid_before_any_history() -> None:
    """Comprueba cobertura y probabilidades válidas con priors seguros."""

    predictions = TeamMarketMarkov().predict_match(_match(1))
    assert set(predictions) == {"home", "away"}
    for side, trajectory in predictions.items():
        assert len(trajectory.probabilities) == len(METRICS) * 2
        assert all(0.0 <= value <= 1.0 for value in trajectory.probabilities.values())
        assert all(0.0 <= value <= 1.0 for value in trajectory.baselines.values())
        assert market_name("corners", side, "first_half") in trajectory.probabilities
        assert len(trajectory.distributions) == len(METRICS) * 3
        assert all(
            sum(distribution.values()) == pytest.approx(1.0)
            for distribution in trajectory.distributions.values())
        assert all(
            sum(distribution.values()) == pytest.approx(1.0)
            for distribution in trajectory.baseline_distributions.values())


def test_current_match_is_not_used_until_update() -> None:
    """Demuestra que predecir no modifica el estado causal."""

    model = TeamMarketMarkov()
    before = model.predict_match(_match(1, 4))
    repeated = model.predict_match(_match(1, 4))
    assert before == repeated
    model.update(_match(1, 4))
    after = model.predict_match(_match(2, 0))
    name = market_name("shots", "home", "first_half")
    assert after["home"].probabilities[name] > before["home"].probabilities[name]


@pytest.mark.parametrize("metric", METRICS)
def test_market_line_matches_public_identifier(metric: str) -> None:
    """Mantiene alineada la línea de scoring y el nombre público."""

    assert market_name(metric, "home", "second_half").endswith(
        f"over_{MARKET_LINES[metric]}_5"
    )


def test_commercial_shots_include_goals() -> None:
    """Alinea el target histórico con totalShots de ESPN."""

    assert _commercial_row({"shots": 3, "goals": 1})["shots"] == 4


# Version: 1.0.0
# Created: 2026-07-28
