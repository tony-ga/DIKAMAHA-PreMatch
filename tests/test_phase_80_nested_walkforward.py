"""Pruebas unitarias del motor determinista de Fase 80."""

from collections import Counter, defaultdict

import numpy as np

from scripts.run_phase_78_context_transitions import ContextTransitionModel
from scripts.run_phase_80_nested_walkforward import (
    _initial_probability,
    _probabilities,
    _preserve_joint,
    _residual_rates,
)


def test_directional_probabilities_are_normalized() -> None:
    """Las cuatro clases suman uno."""

    values = _probabilities(np.array([0.2, 0.4]), np.array([0.1, 0.3]))
    assert np.allclose(values.sum(axis=1), 1.0)
    assert np.all((values >= 0.0) & (values <= 1.0))


def test_initial_probability_respects_known_style() -> None:
    """El prior pre-match no asigna masa al estilo contrario."""

    model = {"global": Counter({0: 3, 1: 2, 2: 1}),
             "league": defaultdict(Counter)}
    values = _initial_probability(model, "missing.1", True, 1)
    assert np.isclose(values.sum(), 1.0)
    assert np.allclose(values[:3], 0.0)


def test_empty_transition_model_has_valid_backoff() -> None:
    """Un fold sin contexto aún produce distribución válida."""

    model = ContextTransitionModel(60.0)
    probability, baseline, _ = model.predict(
        __import__("scripts.run_phase_78_context_transitions",
                   fromlist=["Transition"]).Transition(
                       0, 0, "x", "target", True, 0, 0, 0, 0, 0))
    assert np.isclose(probability.sum(), 1.0)
    assert np.isclose(baseline.sum(), 1.0)


def test_residual_deformation_conserves_baseline_mass() -> None:
    """El residual cambia forma sin modificar intensidad."""

    baseline = np.asarray([0.2, 0.3, 0.4, 0.5, 0.3, 0.2])
    weights = np.linspace(0.5, 1.5, 18)
    result = _residual_rates(baseline, weights, 0.5)
    assert abs(result.sum() - baseline.sum()) < 1e-12
    assert not np.allclose(result, baseline)


def test_joint_projection_is_identity_for_same_margins() -> None:
    """La composición neutral conserva exactamente el tabular."""

    baseline = np.asarray([[0.62, 0.18, 0.15, 0.05]])
    home = -np.log(1 - baseline[:, [1, 3]].sum(axis=1))
    away = -np.log(1 - baseline[:, [2, 3]].sum(axis=1))
    projected = _preserve_joint(baseline, home, away)
    assert np.allclose(projected, baseline, atol=1e-10)
