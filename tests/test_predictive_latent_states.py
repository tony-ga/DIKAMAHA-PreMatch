"""Pruebas de estados predictivos ordenados."""
from __future__ import annotations

import numpy as np

from src.predictive_latent_states import (
    PredictiveStateModel,
    permutation_spreads,
)


def test_states_are_balanced_and_ordered() -> None:
    """Comprueba cuantiles aprendidos sin reglas manuales."""

    x = np.arange(200, dtype=float).reshape(-1, 1)
    y = (x[:, 0] > 100).astype(int)
    model = PredictiveStateModel(4, 0.1)
    model.fit(x, y)
    states = model.states(x)
    assert set(states.tolist()) == {0, 1, 2, 3}
    assert np.all(np.diff(model.boundaries) >= 0.0)


def test_future_target_is_not_required_for_inference() -> None:
    """Exige que inferencia use sólo matriz observable."""

    x = np.array([[0.0], [1.0], [2.0], [3.0]])
    model = PredictiveStateModel(2, 0.1)
    model.fit(x, np.array([0, 0, 1, 1]))
    assert model.states(np.array([[1.5]])).shape == (1,)


def test_tail_quantiles_preserve_minimum_state_support() -> None:
    """Ajusta límites no uniformes sólo con riesgo de desarrollo."""

    x = np.arange(200, dtype=float).reshape(-1, 1)
    y = (x[:, 0] > 100).astype(int)
    model = PredictiveStateModel(4, 0.1, quantiles=(0.1, 0.5, 0.9))
    model.fit(x, y)
    counts = np.bincount(model.states(x), minlength=4)
    assert counts.min() >= 19
    assert len(model.boundaries) == 3


def test_permutation_null_is_reproducible() -> None:
    """Comprueba null determinista por partido/estado."""

    states = np.repeat(np.arange(4), 20)
    targets = np.tile([0.0, 1.0], 40)
    first = permutation_spreads(states, targets, 4, 10, 27)
    second = permutation_spreads(states, targets, 4, 10, 27)
    assert np.array_equal(first, second)


# Version: 1.0.0 - 2026-07-27
