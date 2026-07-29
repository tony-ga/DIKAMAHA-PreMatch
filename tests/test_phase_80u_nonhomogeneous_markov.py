"""Pruebas del Markov no homogéneo."""

import numpy as np

from scripts.run_phase_80u_nonhomogeneous_markov import _context


def _row() -> dict:
    """Construye contexto mínimo."""

    return {"window_index": 2, "features": {"a": 1.0, "b": 2.0}}


def test_previous_state_adds_interaction_features() -> None:
    """El kernel Markov incluye estado e interacciones."""

    summary = np.asarray([3.0, 2.0, 1.0])
    static = _context(_row(), summary, None, False)
    markov = _context(_row(), summary, 2, True)
    assert len(markov) == len(static) + 4 + 12


def test_context_is_deterministic() -> None:
    """Misma entrada pre-match produce el mismo vector."""

    summary = np.asarray([3.0, 2.0, 1.0])
    first = _context(_row(), summary, 1, True)
    second = _context(_row(), summary, 1, True)
    assert np.array_equal(first, second)

