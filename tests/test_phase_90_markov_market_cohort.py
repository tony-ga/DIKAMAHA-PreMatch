"""Pruebas de la cohorte prospectiva Markov por mercado.

Version: 1.0.0
Created: 2026-07-29
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scripts.run_phase_90_markov_market_cohort import (
    _audit,
    _classification,
    _select,
)
from src.count_market_prospective import FrozenCountPrediction
from src.team_count_market_runtime import MARKOV_APPROVED_MARKETS


def _prediction(match_id: int = 1) -> FrozenCountPrediction:
    """Construye una predicción prospectiva mínima."""

    captured = datetime.now(timezone.utc)
    probabilities = {name: 0.5 for name in MARKOV_APPROVED_MARKETS}
    return FrozenCountPrediction(
        "esp.1", match_id, captured + timedelta(days=1), captured,
        10, 20, probabilities, probabilities, "a" * 64, "b" * 64,
        "experimental_shadow_not_promoted")


def test_select_keeps_exact_markov_contract() -> None:
    """Descarta líneas ajenas y exige todas las aprobadas."""

    values = {
        **{name: 0.5 for name in MARKOV_APPROVED_MARKETS},
        "home_corners_over_4_5": 0.4,
    }
    assert set(_select(values)) == MARKOV_APPROVED_MARKETS


def test_audit_is_causal_and_outcome_blind() -> None:
    """Verifica mercados, cutoff y ausencia de outcomes."""

    audit = _audit([_prediction()])
    assert audit["all_predictions_before_kickoff"] is True
    assert audit["approved_markets_present"] is True
    assert audit["outcomes_read"] is False


def test_gate_requires_500_matches_and_10_leagues() -> None:
    """Mantiene el gate independiente congelado."""

    audit = _audit([_prediction()])
    assert _classification({"matches": 499, "leagues": 10}, audit) == (
        "insufficient_coverage")
    assert _classification({"matches": 500, "leagues": 10}, audit) == (
        "ready_for_next_phase")


# Version: 1.0.0
# Created: 2026-07-29
