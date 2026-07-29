"""Pruebas de métricas y bootstrap de la suite OOS.

Version: 1.0.0
Created: 2026-07-26
"""
from __future__ import annotations

from src.evaluate_oos_suite_v1 import EvaluationConfig, _binary_log, _bootstrap


def test_binary_log_is_finite_at_edges() -> None:
    """El clipping evita pérdidas infinitas en probabilidades extremas."""
    assert _binary_log(0.0, True) > 0.0
    assert _binary_log(1.0, False) > 0.0


def test_bootstrap_reports_positive_constant_improvement() -> None:
    """Una mejora constante conserva un intervalo estrictamente positivo."""
    result = _bootstrap([0.1, 0.1, 0.1], EvaluationConfig(bootstrap_samples=20))
    assert result["improvement_confirmed"]
    assert result["ci_95"][0] > 0.0


# Version: 1.0.0
