"""Pruebas sintéticas del Markov contrafactual pre-match."""

from __future__ import annotations

import math

import pytest

from src.markov_counterfactual import (
    CounterfactualEstimator,
    actual_outcome,
    categorical_metrics,
    poisson_first_goal,
)


def _rows(block: str = "development") -> list[dict]:
    """Genera soporte sintético sin mezclar partidos entre bloques."""

    outcomes = ["home_early", "away_middle", "home_late", "no_goal"]
    rows = []
    for index in range(36):
        first = outcomes[index % len(outcomes)]
        side = first.split("_", 1)[0] if first != "no_goal" else None
        minute = {"early": 10, "middle": 45, "late": 75}.get(first.split("_", 1)[-1]) if side else None
        rows.append({"match_id": index + 1, "block": block, "lambda_base_home": 0.8 + (index % 3) * 0.5,
                     "lambda_base_away": 1.8 - (index % 3) * 0.5,
                     "actual": {"first": first, "first_side": side, "first_minute": minute,
                                "second": "equalizer" if side else None,
                                "behavior": {"behind_shot_on_target": 2.0, "behind_corner": 1.0, "behind_goal": 1.0} if side else {}}})
    return rows


def test_poisson_first_goal_is_normalized_without_softmax() -> None:
    """La distribución base sale de intensidades y suma uno."""

    probabilities = poisson_first_goal(1.5, 1.0)
    assert abs(sum(probabilities.values()) - 1.0) < 1e-12
    assert probabilities["home_early"] > probabilities["away_early"]
    assert all(0.0 <= value <= 1.0 for value in probabilities.values())


def test_counterfactual_tree_is_normalized_and_deterministic() -> None:
    """Todas las ramas se derivan de frecuencias históricas reproducibles."""

    estimator = CounterfactualEstimator().fit(_rows())
    match = {"match_id": 100, "block": "validation", "lambda_base_home": 1.4, "lambda_base_away": 1.1}
    first = estimator.predict(match)
    second = estimator.predict(dict(match))
    assert first == second
    assert abs(first["branch_probability_sum"] - 1.0) < 1e-12
    assert first["official_output_modified"] is False and first["hawkes_used"] is False


def test_fit_rejects_validation_or_confirmation() -> None:
    """Impide seleccionar frecuencias usando bloques OOS."""

    with pytest.raises(ValueError, match="fit_requires_development_only"):
        CounterfactualEstimator().fit(_rows("confirmation"))


def test_prediction_does_not_consume_target_match_events() -> None:
    """La predicción anterior no cambia al alterar el resultado hipotético."""

    estimator = CounterfactualEstimator().fit(_rows())
    base = {"match_id": 100, "block": "validation", "lambda_base_home": 1.2, "lambda_base_away": 1.2}
    assert estimator.predict(base) == estimator.predict({**base, "actual": {"first": "away_late"}})


def test_event_target_is_chronological_and_preserves_unknown_team() -> None:
    """Etiqueta por secuencia y no imputa equipo a un gol sin team_id."""

    match = {"home_team_id": 1, "away_team_id": 2}
    events = [{"minute": 20, "second": 0, "team_id": None, "event_type": "goal", "valid": True}]
    assert actual_outcome(match, events)["first"] == "unknown"


def test_metrics_are_finite() -> None:
    """Log score y Brier se mantienen finitos para masa válida."""

    result = categorical_metrics("no_goal", poisson_first_goal(1.0, 1.0))
    assert all(math.isfinite(value) for value in result.values())


# Version: 1.0.0
# Created: 2026-07-16
