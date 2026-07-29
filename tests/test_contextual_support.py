"""Pruebas del pooling contextual contrafactual."""

from __future__ import annotations

import pytest

from src.contextual_support import ContextPolicy, ContextualSupportEstimator


def _rows(block: str = "development") -> list[dict]:
    """Crea partidos sintéticos completos con contextos comparables."""

    return [{"block": block, "strength_bin": "balanced" if index < 12 else "home_stronger",
             "actual": {"first_side": "home", "first_minute": 20,
                        "second": "equalizer" if index % 2 else "conserve_advantage",
                        "behavior": {"behind_shot_on_target": 1.0, "behind_corner": 2.0}}}
            for index in range(18)]


def test_strategies_are_normalized_and_have_provenance() -> None:
    """Las tres estrategias generan masa válida e identifican soporte."""

    estimator = ContextualSupportEstimator(ContextPolicy(minimum_exact_support=10)).fit(_rows())
    for strategy in ("exact", "pooled_comparable", "global"):
        values, meta = estimator.distribution("home", "early", "balanced", strategy)
        assert abs(sum(values.values()) - 1.0) < 1e-12
        assert meta["strategy"] in {strategy, "global"}


def test_pooling_preserves_side_and_window_parent() -> None:
    """El pooling no mezcla reacciones de lado o ventana distinta."""

    estimator = ContextualSupportEstimator().fit(_rows())
    _, meta = estimator.distribution("home", "early", "balanced", "pooled_comparable")
    assert meta["source_context"] == "home|early"


def test_fit_rejects_oos_rows() -> None:
    """No permite que validación o confirmación entren al ajuste."""

    with pytest.raises(ValueError, match="development_only"):
        ContextualSupportEstimator().fit(_rows("confirmation"))


def test_low_support_is_visible() -> None:
    """El soporte escaso no se oculta al aplicar fallback."""

    estimator = ContextualSupportEstimator(ContextPolicy(minimum_reportable_support=30)).fit(_rows())
    assert any(row["low_evidence"] for row in estimator.support()["contexts"])


def test_behavior_pooling_preserves_role_key() -> None:
    """Las métricas de presión por rol no se descartan durante el pooling."""

    estimator = ContextualSupportEstimator().fit(_rows())
    behavior, _ = estimator.behavior_mean("home", "early", "balanced", "pooled_comparable")
    assert behavior["behind_shot_on_target"] > 0.0


# Version: 1.0.0
# Created: 2026-07-16
