"""Pruebas de dependencia y límites shadow."""
from __future__ import annotations

from src.market_exposure_policy import ExposurePolicy, dependency_components


def test_components_join_only_positive_dependency() -> None:
    """No trata correlación negativa como concentración positiva."""

    names = ["a", "b", "c"]
    matrix = [[1.0, 0.5, -0.6], [0.5, 1.0, 0.0], [-0.6, 0.0, 1.0]]

    assert dependency_components(names, matrix, 0.3) == (("a", "b"), ("c",))


def test_policy_limits_component_and_match() -> None:
    """Aplica ambos límites sin introducir stakes."""

    policy = ExposurePolicy(2, 1, 0.3, (("a", "b"), ("c",), ("d",)))

    assert policy.validate(["a", "c"])
    assert not policy.validate(["a", "b"])
    assert not policy.validate(["a", "c", "d"])


# Version: 1.0.0
# Created: 2026-07-29
