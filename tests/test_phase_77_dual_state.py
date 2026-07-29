"""Pruebas de la representación factorial pre-match/in-play."""

import numpy as np

from scripts.run_phase_77_dual_state_reaudit import (
    _combine,
    _style_distribution,
    _style_score,
)


def _context() -> dict[str, object]:
    """Crea contexto pre-match mínimo con ambos perfiles."""

    values = [0.0] * 23
    values[7:11] = [4.0, 2.0, 1.0, 3.0]
    values[16:20] = [2.0, 1.0, 0.0, 1.0]
    return {"features": values}


def test_style_score_uses_only_frozen_context() -> None:
    """Agregar campos post-match no altera el estilo."""

    context = _context()
    changed = {**context, "goals": 9, "shots": 30}
    assert _style_score(context) == _style_score(changed)


def test_dual_state_combines_style_and_regime() -> None:
    """Los seis estados conservan ambos ejes."""

    rows = [{"match_id": 1, "team_id": 10},
            {"match_id": 2, "team_id": 20}]
    styles = {(1, 10): _context(), (2, 20): {"features": [0.0] * 23}}
    states = _combine(rows, np.asarray([0.9, 0.1]), styles, 1.0,
                      np.asarray([0.2, 0.8]))
    assert states.tolist() == [5, 0]


def test_style_distribution_has_no_mass_in_wrong_style() -> None:
    """El estilo conocido restringe sólo su bloque de tres regímenes."""

    probability = _style_distribution(
        {}, np.full(6, 1.0 / 6.0), style=1)
    assert np.allclose(probability[:3], 0.0)
    assert np.isclose(probability.sum(), 1.0)
