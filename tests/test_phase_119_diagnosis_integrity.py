"""Invariantes aritméticos del diagnóstico de calibración de Fase 119."""

from __future__ import annotations

import math

from scripts.run_phase_119_bias_diagnosis_500 import (
    _diagnose_market,
    _eligible_for_correction,
    _reliability_bins,
    _served_row,
)
from scripts.run_phase_105_historical_1000_complete import MARKETS


def _row(match_id: int, btts_actual: bool, corners_actual: bool) -> dict:
    def market(actual: bool, probability: float, baseline: float) -> dict:
        return {
            "probability": probability, "baseline_probability": baseline,
            "actual": actual, "predicted": probability >= 0.5,
            "correct": (probability >= 0.5) == actual,
            "log_loss": 0.1, "brier": 0.1, "normalized_brier": 0.1,
            "baseline_correct": True, "baseline_log_loss": 0.1,
            "baseline_brier": 0.1, "baseline_normalized_brier": 0.1,
            "model": "test",
        }
    markets = {name: market(True, 0.5, 0.5) for name in MARKETS if name != "1x2"}
    markets["btts"] = market(btts_actual, 0.9, 0.9)
    markets["home_corners_second_half_over_2_5"] = market(corners_actual, 0.9, 0.2)
    markets["1x2"] = {
        "probabilities": {"home": 0.6, "draw": 0.25, "away": 0.15},
        "actual": "home", "predicted": "home", "correct": True,
        "confidence": 0.6, "log_loss": 0.1, "brier": 0.1,
        "normalized_brier": 0.1, "baseline_predicted": "home",
        "baseline_correct": True, "baseline_log_loss": 0.1,
        "baseline_brier": 0.1, "baseline_normalized_brier": 0.1,
        "baseline_probability": 0.5, "model": "test",
    }
    return {
        "match_id": match_id, "match_date": "2026-01-01",
        "league_slug": "esp.1", "markets": markets,
    }


def test_served_row_overrides_btts_with_the_calibrated_probability() -> None:
    """El veredicto servido usa la probabilidad recalibrada, no la cruda."""

    row = _row(1, btts_actual=True, corners_actual=True)

    served = _served_row(row, btts_probability=0.42)

    assert served["markets"]["btts"]["probability"] == 0.42
    assert served["markets"]["btts"]["predicted"] is False
    assert served["markets"]["btts"]["correct"] is False


def test_served_row_overrides_markov_fallback_with_its_baseline() -> None:
    """El mercado en MARKOV_BASELINE_FALLBACKS sirve exactamente su baseline."""

    row = _row(1, btts_actual=True, corners_actual=False)

    served = _served_row(row, btts_probability=0.5)
    corners = served["markets"]["home_corners_second_half_over_2_5"]

    assert corners["probability"] == corners["baseline_probability"] == 0.2
    assert corners["predicted"] is False
    assert corners["correct"] is True


def test_reliability_bins_sum_to_total_predictions() -> None:
    """Los 10 bins cubren exactamente todas las predicciones sin perder ninguna."""

    rows = [
        {"match_id": i, "probability": (i % 10) / 10.0 + 0.01, "actual": i % 3 == 0}
        for i in range(120)
    ]

    bins = _reliability_bins(rows)

    assert len(bins) == 10
    assert sum(entry["n"] for entry in bins) == len(rows)


def test_diagnose_market_ece_is_bounded() -> None:
    """El ECE de cualquier mercado queda siempre en [0, 1]."""

    rows = [
        _served_row(_row(i, i % 2 == 0, i % 3 == 0), btts_probability=0.3 + 0.001 * i)
        for i in range(220)
    ]

    for name in MARKETS:
        diagnosis = _diagnose_market(name, rows)
        assert 0.0 <= diagnosis["ece"] <= 1.0
        assert diagnosis["predictions"] == len(rows)


def test_entry_gate_excludes_multiclass_and_small_or_skewed_samples() -> None:
    """1X2 nunca entra; muestras chicas o casi degeneradas tampoco."""

    base = {"predictions": 500, "positive_rate": 0.5, "ece": 0.2, "is_multiclass": False}

    assert _eligible_for_correction(base) is True
    assert _eligible_for_correction({**base, "is_multiclass": True}) is False
    assert _eligible_for_correction({**base, "predictions": 50}) is False
    assert _eligible_for_correction({**base, "positive_rate": 0.98}) is False
    assert _eligible_for_correction({**base, "ece": 0.02}) is False


# Version: 1.0.0
# Created: 2026-08-10
