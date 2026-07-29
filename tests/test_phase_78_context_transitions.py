"""Pruebas de transición contextual dual."""

import numpy as np

from scripts.run_phase_78_context_transitions import (
    ContextTransitionModel,
    Transition,
    _probability,
)


def _row(opponent: int, target: int) -> Transition:
    """Construye una transición mínima."""

    return Transition(1, 10, "test.1", "fit", True, 0, 1, 0,
                      opponent, target)


def test_probability_is_normalized_without_support() -> None:
    """El backoff produce distribución válida sin observaciones."""

    values = _probability({}, np.asarray([0.2, 0.3, 0.5]), 20.0)
    assert np.allclose(values, [0.2, 0.3, 0.5])
    assert np.isclose(values.sum(), 1.0)


def test_opponent_regime_changes_contextual_transition() -> None:
    """El régimen rival selecciona conteos diferentes."""

    model = ContextTransitionModel(5.0)
    model.fit([_row(0, 0)] * 20 + [_row(2, 2)] * 20)
    low, _, _ = model.predict(_row(0, 0))
    high, _, _ = model.predict(_row(2, 2))
    assert low.argmax() == 0
    assert high.argmax() == 2


def test_style_does_not_transition() -> None:
    """El target del modelo contiene sólo régimen, no estilo futuro."""

    row = _row(1, 2)
    assert row.style == 1
    assert row.next_regime == 2
