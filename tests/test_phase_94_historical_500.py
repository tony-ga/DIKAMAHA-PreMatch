"""Pruebas del contrato histórico semi-oficial de Fase 94."""
from __future__ import annotations

from scripts.run_phase_94_historical_500_semiofficial import (
    _league_quality,
    _outcomes,
    _score_market,
)


def test_quality_rejects_timeline_taxonomically_empty() -> None:
    """Rechaza una liga con cronología sin eventos de mercado."""

    result = _league_quality([(0, 2)] * 20)
    assert result["prior_matches"] == 20
    assert result["eligible"] is False


def test_quality_accepts_feed_with_market_taxonomy() -> None:
    """Acepta un feed con soporte previo suficiente."""

    result = _league_quality([(10, 22)] * 20)
    assert result["eligible"] is True
    assert result["mean_total_corners"] == 10.0


def test_outcomes_and_scoring_use_strict_market_lines() -> None:
    """Liquida líneas .5 y puntúa el lado más probable."""

    counts = {
        "away_corners": 5, "away_shots": 10, "home_corners": 4,
        "home_shots": 11, "shots_on_target_total": 8,
        "away_shots_second_half": 6, "home_corners_second_half": 3,
        "home_shots_first_half": 5, "home_shots_second_half": 6,
    }
    outcomes = _outcomes(counts)
    score = _score_market(0.7, 0.4, outcomes["away_corners_over_4_5"])
    assert outcomes["away_corners_over_4_5"] is True
    assert outcomes["away_shots_over_10_5"] is False
    assert score["model_correct"] is True


# Version: 1.0.0
# Created: 2026-07-29
