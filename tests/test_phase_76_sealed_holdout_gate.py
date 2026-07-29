"""Pruebas del gate sellado de Fase 76.

Version: 1.0.0
Created: 2026-07-28
"""
from scripts.evaluate_phase_76_independent_cohort import _classification


def _metrics() -> dict:
    """Construye métricas mínimas aprobatorias."""

    return {
        "spread": 0.05,
        "occupancy": {"0": 0.5, "1": 0.5},
        "league_order": {"admitted": 4, "stable": 3},
        "duration": {"improvement": 0.01},
    }


def test_gate_approves_only_complete_evidence() -> None:
    """Aprueba cuando todos los criterios congelados se cumplen."""

    assert _classification(_metrics(), 200, 10, []) == "ready_for_phase_77"


def test_gate_rejects_low_semantic_spread() -> None:
    """Rechaza separación predictiva inferior al umbral."""

    metrics = _metrics()
    metrics["spread"] = 0.049
    assert _classification(metrics, 200, 10, []) == "rejected_for_revision"


def test_gate_rejects_material_timeline_corruption() -> None:
    """Rechaza una cohorte con más de dos por ciento de discrepancias."""

    rejected = list(range(5))
    assert _classification(_metrics(), 195, 10, rejected) == "rejected_for_revision"

# Version: 1.0.0 - 2026-07-28
