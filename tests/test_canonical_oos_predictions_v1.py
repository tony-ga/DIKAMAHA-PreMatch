"""Pruebas del contrato canónico OOS.

Version: 1.0.0
Created: 2026-07-26
"""
from __future__ import annotations

from src.canonical_oos_predictions_v1 import _model_name, _normalized_1x2


def test_model_name_maps_required_baselines() -> None:
    """Los modelos heredados se mapean sin cambiar su semántica."""
    assert _model_name("poisson_simple") == "baseline_simple"
    assert _model_name("dixon_coles_v1") == "dixon_coles"
    assert _model_name("kalman_v1") == "dixon_coles_kalman"


def test_model_name_skips_unrequested_sources() -> None:
    """Un artefacto no homologado no entra silenciosamente a la suite."""
    assert _model_name("league_mean") is None


def test_normalized_1x2_preserves_relative_mass() -> None:
    """La normalización corrige masa sin alterar la proporción entre outcomes."""
    p1, px, p2, adjusted = _normalized_1x2({"prob_1": 0.4, "prob_x": 0.2, "prob_2": 0.2})
    assert adjusted and abs(p1 + px + p2 - 1.0) < 1e-12
    assert abs(p1 / px - 2.0) < 1e-12


# Version: 1.0.0
# Created: 2026-07-26
