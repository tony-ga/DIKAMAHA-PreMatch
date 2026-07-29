"""Pruebas de la cohorte prospectiva de mercados agregados."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from scripts.run_phase_86_count_market_collection import (
    _audit,
    _classification,
)
from src.count_market_prospective import (
    CountCohortBase,
    FrozenCountPrediction,
    SqlAlchemyCountPredictionRepository,
)

MARKETS = {
    "home_corners_over_4_5": 0.5,
    "away_corners_over_4_5": 0.4,
    "away_shots_over_10_5": 0.3,
    "first_half_cards_over_1_5": 0.6,
}


def _prediction(match_id: int = 1) -> FrozenCountPrediction:
    """Construye una predicción causal mínima."""

    captured = datetime.now(timezone.utc)
    return FrozenCountPrediction(
        "esp.1", match_id, captured + timedelta(days=1), captured,
        10, 20, MARKETS, MARKETS, "a" * 64, "b" * 64,
        "experimental_shadow_not_promoted")


def test_repository_is_append_only_and_idempotent() -> None:
    """No sobrescribe una predicción ya congelada."""

    engine = create_engine("sqlite+pysqlite:///:memory:")
    CountCohortBase.metadata.create_all(engine)
    repository = SqlAlchemyCountPredictionRepository(
        sessionmaker(bind=engine, expire_on_commit=False))
    assert repository.add_if_absent(_prediction()) is True
    assert repository.add_if_absent(_prediction()) is False
    assert len(repository.all()) == 1


def test_audit_keeps_outcomes_sealed() -> None:
    """Valida causalidad, mercados y ausencia de outcomes."""

    audit = _audit([_prediction()])
    assert audit["all_predictions_before_kickoff"] is True
    assert audit["four_markets_present"] is True
    assert audit["outcomes_read"] is False


def test_gate_requires_500_matches_and_10_leagues() -> None:
    """Impide evaluar una cohorte prospectiva incompleta."""

    audit = _audit([_prediction()])
    insufficient = {"matches": 499, "leagues": 10}
    complete = {"matches": 500, "leagues": 10}
    assert _classification(insufficient, audit) == "insufficient_coverage"
    assert _classification(complete, audit) == "ready_for_next_phase"


# Version: 1.0.0
# Created: 2026-07-28
