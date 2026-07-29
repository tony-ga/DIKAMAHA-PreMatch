"""Pruebas de duración y semántica latente."""
from __future__ import annotations

import numpy as np

from src.latent_state_discovery import (
    duration_nll,
    duration_probabilities,
    geometric_probabilities,
    next_goal_risk,
    runs,
)


def test_runs_do_not_cross_match_boundaries() -> None:
    """Impide unir duraciones entre partidos distintos."""

    match_ids = np.array([1, 1, 2, 2])
    states = np.array([0, 0, 0, 1])
    assert runs(match_ids, states) == {0: [2, 1], 1: [1]}


def test_explicit_duration_rewards_observed_runs() -> None:
    """Comprueba que la distribución empírica modela duración no geométrica."""

    match_ids = np.repeat(np.arange(30), 4)
    states = np.tile(np.array([0, 0, 1, 1]), 30)
    explicit = duration_probabilities(match_ids, states, 2, maximum=4, alpha=0.1)
    geometric = geometric_probabilities(match_ids, states, 2, maximum=4)
    assert duration_nll(explicit, match_ids, states) < duration_nll(geometric, match_ids, states)


def test_next_goal_is_only_an_evaluation_label() -> None:
    """Calcula separación de riesgo sin alterar estados."""

    states = np.array([0, 0, 1, 1])
    goals = np.array([0.0, 0.0, 1.0, 1.0])
    risks, support = next_goal_risk(states, goals, 2)
    assert risks.tolist() == [0.0, 1.0]
    assert support.tolist() == [2, 2]


# Version: 1.0.0 - 2026-07-27
