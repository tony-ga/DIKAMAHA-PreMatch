"""Pruebas de emisiones robustas entre dominios.

Version: 1.0.0
Created: 2026-07-28
"""
import numpy as np

from src.domain_robust_states import (
    domain_invariant_features,
    feature_names,
    rolling_domain_features,
)


def _rows() -> np.ndarray:
    """Construye tres emisiones válidas de ejemplo."""

    values = np.zeros((3, 13), dtype=float)
    values[:, 1] = [1, 3, 5]
    values[:, 2] = [0, 1, 2]
    values[:, 8] = [2, 2, 2]
    return values


def test_domain_features_have_stable_contract() -> None:
    """Mantiene 19 variables presentes y 57 con memoria."""

    current = domain_invariant_features(_rows())
    rolling = rolling_domain_features(_rows(), np.array([1, 1, 1]))
    assert current.shape == (3, 19)
    assert rolling.shape == (3, 57)
    assert len(feature_names()) == 57


def test_rolling_features_never_cross_sequence_boundary() -> None:
    """Reinicia memoria al cambiar la identidad del partido/equipo."""

    values = rolling_domain_features(_rows(), np.array([1, 1, 2]))
    current = domain_invariant_features(_rows())
    assert np.allclose(values[2, 19:38], current[2])
    assert np.allclose(values[2, 38:57], current[2])

# Version: 1.0.0 - 2026-07-28
