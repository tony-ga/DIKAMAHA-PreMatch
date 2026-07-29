"""Pruebas del baseline temporal direccional."""
from __future__ import annotations

import numpy as np

from src.directional_temporal_baseline import (
    CausalProfileBuilder,
    analytical_probabilities,
    probability_audit,
    target_class,
    temperature_scale,
)


def _row(team: int, home: bool, goals: int) -> dict[str, object]:
    """Crea una ventana mínima para probar historial causal."""

    return {"league_slug": "mex.1", "window_index": 0, "team_id": team,
            "is_home": home, "goals": goals, "shots": 2,
            "shots_on_target": 1, "corners": 1, "pressure": 3,
            "shots_conceded": 2, "corners_conceded": 1,
            "pressure_conceded": 3}


def test_target_classes_are_directional() -> None:
    """Comprueba las cuatro clases conjuntas."""

    assert [target_class(*pair) for pair in ((0, 0), (1, 0), (0, 1), (2, 1))] == [0, 1, 2, 3]


def test_target_match_is_not_in_initial_features() -> None:
    """Garantiza que el update ocurre después de emitir features."""

    builder = CausalProfileBuilder()
    home, away = _row(1, True, 4), _row(2, False, 0)
    before = builder.features(home, away)
    builder.update(home, away)
    after = builder.features(home, away)
    assert before["home_history"] == 0
    assert after["home_history"] == 1
    assert before["home_goals"] < after["home_goals"]


def test_probabilities_remain_valid_after_calibration() -> None:
    """Audita normalización analítica y temperatura."""

    builder = CausalProfileBuilder()
    values = analytical_probabilities(builder.features(_row(1, True, 0), _row(2, False, 0)))
    calibrated = temperature_scale(np.vstack([values]), 1.25)
    audit = probability_audit(calibrated)
    assert audit["finite"] and audit["within_bounds"]
    assert audit["max_sum_error"] < 1e-12


# Version: 1.0.0 - 2026-07-27
