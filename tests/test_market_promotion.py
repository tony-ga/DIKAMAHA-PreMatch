"""Pruebas del gate probabilístico por mercado.

Version: 1.0.0
Created: 2026-07-29
"""
from __future__ import annotations

from src.market_promotion import evaluate_markets


def _rows(model: float, baseline: float) -> list[dict[str, object]]:
    """Construye tres ligas elegibles con outcomes balanceados."""

    rows = []
    for league in ("a", "b", "c"):
        for index in range(40):
            actual = index % 2 == 0
            rows.append({
                "league_slug": league,
                "probabilities": {"market": model if actual else 1.0 - model},
                "baseline_probabilities": {
                    "market": baseline if actual else 1.0 - baseline},
                "outcomes": {"market": actual},
            })
    return rows


def test_gate_approves_clear_probabilistic_improvement() -> None:
    """Aprueba mejora fuerte, calibrada y estable."""

    result = evaluate_markets(_rows(0.8, 0.6), replicates=500)
    assert result["approved_markets"] == ["market"]
    assert result["markets"]["market"]["league_nonnegative_rate"] == 1.0


def test_gate_rejects_model_worse_than_baseline() -> None:
    """Rechaza una línea con pérdida probabilística superior."""

    result = evaluate_markets(_rows(0.55, 0.75), replicates=500)
    assert result["approved_markets"] == []


# Version: 1.0.0
# Created: 2026-07-29
