"""Pruebas del reporte de sistema completo Fase 80W."""

from scripts.run_phase_80w_complete_system_report import (
    _score_binary,
    _score_1x2,
)


def test_binary_score_uses_frozen_threshold() -> None:
    """El umbral 0.5 produce una decisión y score coherentes."""

    score = _score_binary(0.8, True)
    assert score["correct"] is True
    assert score["actual_probability"] == 0.8
    assert round(score["brier"], 6) == 0.04


def test_1x2_score_uses_argmax() -> None:
    """1X2 selecciona la clase de máxima probabilidad."""

    row = {"prob_1": 0.6, "prob_x": 0.25, "prob_2": 0.15}
    score = _score_1x2(row, "1")
    assert score["predicted"] == "1"
    assert score["correct"] is True
