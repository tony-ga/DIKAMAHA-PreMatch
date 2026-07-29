"""Pruebas del likelihood de trayectoria completa."""

import numpy as np

from scripts.run_phase_80r_trajectory_likelihood import (
    _emission,
    _scores,
)


def test_residual_emission_is_normalized() -> None:
    """Carrier por residuo conserva simplex."""

    base = np.asarray([0.6, 0.2, 0.15, 0.05])
    ratio = np.asarray([0.8, 1.2, 1.1, 0.9])
    result = _emission(base, ratio)
    assert np.isclose(result.sum(), 1.0)
    assert np.all(result >= 0.0)


def test_match_score_uses_complete_match_unit() -> None:
    """Las seis ventanas se agregan antes del bootstrap."""

    probabilities = np.full((12, 4), 0.25)
    targets = np.zeros(12, dtype=int)
    matches = np.repeat([1, 2], 6)
    result = _scores(probabilities, targets, matches)
    assert len(result["match_losses"]) == 2
    assert np.isclose(result["log_loss"], -np.log(0.25))

