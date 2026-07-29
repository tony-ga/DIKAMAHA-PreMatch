"""Pruebas de arquetipos pre-match."""

import numpy as np

from scripts.run_phase_80t_prematch_archetype import (
    ArchetypeModel,
    _activity,
)


def _model(taxonomy: str) -> ArchetypeModel:
    """Construye cortes mínimos."""

    return ArchetypeModel(taxonomy, (10.0, 20.0), 5.0, 5.0, 0.0,
                          (1.0, 2.0), 10.0)


def test_six_state_taxonomy_stays_in_bounds() -> None:
    """La taxonomía 3×2 produce índices válidos."""

    values = [_model("tempo3_dominance2").assign(
        np.asarray([home, away, 1.0]))
        for home, away in ((2, 2), (8, 4), (14, 10))]
    assert all(0 <= value < 6 for value in values)


def test_activity_uses_only_named_prematch_fields() -> None:
    """El score no necesita outcomes."""

    values = {"home_shots": 2.0, "home_shots_on_target": 1.0,
              "home_pressure": 4.0, "home_corners": 1.0}
    assert np.isclose(_activity(values, "home"), 5.1)

