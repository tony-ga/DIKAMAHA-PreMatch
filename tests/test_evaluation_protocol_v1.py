"""Pruebas del protocolo de evaluación y su bloqueo causal.

Version: 1.0.0
Created: 2026-07-26
"""
from __future__ import annotations

from src.evaluation_protocol_v1 import _missing_fields


def test_missing_fields_accepts_dixon_coles_aliases() -> None:
    """Los nombres de probabilidad heredados se reconocen explícitamente."""
    row = {"match_id": 1, "prob_1_dc": 0.3, "prob_x_dc": 0.3, "prob_2_dc": 0.4}
    assert _missing_fields([row]) == []


def test_missing_fields_rejects_reference_fixture() -> None:
    """Una salida sin match_id no se interpreta como predicción OOS."""
    assert "match_id" in _missing_fields([])


# Version: 1.0.0
# Created: 2026-07-26
