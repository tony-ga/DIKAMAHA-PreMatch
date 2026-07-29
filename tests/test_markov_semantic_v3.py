"""Pruebas de causalidad y normalización para estados semánticos v3.

Version: 1.0.0
Created: 2026-07-27
"""
from __future__ import annotations

import numpy as np

from src.markov_semantic_v3 import (
    SemanticConfig,
    SemanticStateLabeler,
    TempoTransitionModel,
    danger_score,
    initial_distribution,
)


def _row(team: int, home: bool, shots: int, target: int, goals: int = 0) -> dict[str, object]:
    """Crea una fila mínima de ventana para pruebas."""

    return {
        "match_id": 1, "window_index": 0, "match_date": "2025-01-01T00:00:00+00:00",
        "league_slug": "test.1", "team_id": team, "opponent_team_id": 3 - team,
        "is_home": home, "shots": shots, "shots_on_target": target,
        "shots_blocked": 0, "corners": 0, "goals": goals,
    }


def test_danger_score_does_not_use_goals() -> None:
    """Los goles no pueden alterar el estado semántico."""

    base = _row(1, True, 3, 1, goals=0)
    changed = {**base, "goals": 4}
    assert danger_score(base) == danger_score(changed)


def test_control_states_are_reciprocal() -> None:
    """Control y presión deben conservar orientación local/visitante."""

    rows = [
        {"tempo": 0.0, "control_margin": 0.0},
        {"tempo": 4.0, "control_margin": 3.0},
        {"tempo": 8.0, "control_margin": -3.0},
        {"tempo": 12.0, "control_margin": 0.0},
    ]
    labeler = SemanticStateLabeler().fit(rows)
    labeled = labeler.transform(rows)
    assert labeled[1]["home_control_state"] == "control"
    assert labeled[1]["away_control_state"] == "bajo_presion"
    assert labeled[2]["home_control_state"] == "bajo_presion"


def test_transition_matrix_is_normalized() -> None:
    """Cada fila de transición debe sumar uno aun sin soporte."""

    matrix = TempoTransitionModel().matrix("unknown.1", (10, 20), 0)
    assert matrix.shape == (4, 4)
    assert np.allclose(matrix.sum(axis=1), np.ones(4))
    assert np.all(matrix > 0.0)


def test_initial_distribution_uses_explicit_prior() -> None:
    """Sin historia, state_0 debe coincidir exactamente con el prior."""

    prior = np.asarray([0.1, 0.2, 0.3, 0.4])
    probability, support = initial_distribution({}, prior, (1, 2), SemanticConfig())
    assert support == 0
    assert np.allclose(probability, prior)


# Version: 1.0.0
# Created: 2026-07-27
