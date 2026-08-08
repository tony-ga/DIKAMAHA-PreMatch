"""Pruebas del sidecar de mercados de conteo."""

import numpy as np
import pytest

from scripts.run_phase_84a_team_count_markets import _commercial_count
from src.team_count_markets import (
    SklearnPoissonSolver,
    negative_binomial_distribution,
    negative_binomial_over_probability,
    poisson_deviance,
    poisson_over_probability,
)


def test_poisson_probability_is_valid_and_monotonic() -> None:
    """Una tasa mayor aumenta la probabilidad de superar la línea."""

    low = poisson_over_probability(2.0, 4)
    high = poisson_over_probability(7.0, 4)
    assert 0.0 <= low < high <= 1.0


def test_poisson_deviance_is_zero_at_observation() -> None:
    """La deviance se anula en la predicción exacta."""

    assert poisson_deviance(3.0, 3.0) == 0.0


def test_poisson_deviance_preserves_impossible_zero_mean() -> None:
    """Una media cero no puede ocultar una observación positiva."""

    assert poisson_deviance(0.0, 0.0) == 0.0
    assert poisson_deviance(1.0, 0.0) == float("inf")


def test_solver_emits_positive_rates() -> None:
    """El adaptador mantiene lambdas positivas."""

    features = np.asarray([[0.0], [1.0], [2.0], [3.0]])
    targets = np.asarray([1.0, 2.0, 3.0, 4.0])
    solver = SklearnPoissonSolver(1.0)
    solver.fit(features, targets)
    assert np.all(solver.predict(features) > 0.0)


def test_negative_binomial_probability_is_valid() -> None:
    """La distribución sobredispersa conserva una probabilidad válida."""

    value = negative_binomial_over_probability(8.0, 0.2, 7)
    assert 0.0 < value < 1.0


def test_negative_binomial_distribution_is_normalized() -> None:
    """La PMF conserva masa, media aproximada y soporte discreto."""

    distribution = negative_binomial_distribution(8.0, 0.2)
    assert sum(distribution.values()) == pytest.approx(1.0)
    assert all(isinstance(count, int) and count >= 0 for count in distribution)
    mean = sum(count * value for count, value in distribution.items())
    assert mean == pytest.approx(8.0, rel=1e-6)


def test_negative_binomial_distribution_extends_past_initial_maximum() -> None:
    """No renormaliza una cola material dentro del soporte inicial."""

    distribution = negative_binomial_distribution(
        24.0, 0.475, maximum=30)
    assert max(distribution) > 30
    mean = sum(count * value for count, value in distribution.items())
    assert mean == pytest.approx(24.0, rel=1e-8)


@pytest.mark.parametrize(
    ("function", "args"),
    [
        (poisson_over_probability, (float("nan"), 2)),
        (poisson_over_probability, (-1.0, 2)),
        (negative_binomial_over_probability, (2.0, 0.0, 2)),
        (negative_binomial_distribution, (float("inf"), 0.2)),
    ],
)
def test_count_probabilities_reject_invalid_parameters(
    function: object, args: tuple[object, ...],
) -> None:
    """NaN, tasas negativas y dispersiones nulas fallan explícitamente."""

    with pytest.raises(ValueError):
        function(*args)


def test_commercial_shots_and_on_target_include_goals() -> None:
    """Mantiene paridad con totalShots y shotsOnTarget de ESPN."""

    row = {"shots": 3, "shots_on_target": 1, "goals": 1}
    assert _commercial_count(row, "shots", "shots") == 4.0
    assert _commercial_count(
        row, "shots_on_target", "shots_on_target") == 2.0
