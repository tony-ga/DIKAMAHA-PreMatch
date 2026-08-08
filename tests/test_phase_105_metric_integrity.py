"""Pruebas de comparabilidad para métricas agregadas de Fase 105."""
from __future__ import annotations

import pytest

from scripts.run_phase_105_historical_1000_complete import _aggregate_metrics


def test_mixed_binary_and_multiclass_brier_is_only_reported_normalized() -> None:
    """No promedia escalas Brier incompatibles bajo una etiqueta común."""

    row = {"markets": {
        "1x2": {
            "correct": True, "confidence": 0.6, "log_loss": 0.5,
            "brier": 0.4, "normalized_brier": 0.2,
            "baseline_correct": False, "baseline_log_loss": 0.8,
            "baseline_brier": 0.6, "baseline_normalized_brier": 0.3,
        },
        "binary": {
            "correct": True, "probability": 0.7, "predicted": True,
            "log_loss": 0.4, "brier": 0.1, "normalized_brier": 0.1,
            "baseline_correct": True, "baseline_log_loss": 0.5,
            "baseline_brier": 0.2, "baseline_normalized_brier": 0.2,
        },
    }}
    metrics = _aggregate_metrics([row], ("1x2", "binary"))
    assert metrics["brier"] is None
    assert metrics["baseline_brier"] is None
    assert metrics["normalized_brier"] == pytest.approx(0.15)
    assert metrics["baseline_normalized_brier"] == pytest.approx(0.25)
