"""Pruebas de independencia de la cohorte confirmatoria."""

from __future__ import annotations

from src.phase_29_confirmatory_eligibility_audit import _audit


def test_phase_28_cohort_is_not_independent_of_phase20_calibration() -> None:
    """Bloquea la evaluación cuando existe reutilización de calibración."""

    result = _audit()
    assert result["classification"] == "ineligible_for_confirmatory_evaluation"
    assert result["cohort_match_count"] == 42
    assert len(result["overlaps"]["phase20_calibration"]) == 42
    assert result["metrics_calculated"] is False
    assert result["bootstrap_calculated"] is False


def test_router_and_markets_remain_unchanged() -> None:
    """La auditoría no altera el router ni habilita promoción."""

    result = _audit()
    assert result["router_modified"] is False
    assert result["markets_promoted"] is False

# Version: 1.0.0
# Created: 2026-07-26
