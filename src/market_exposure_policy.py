"""Política shadow de concentración entre mercados correlacionados.

Version: 1.0.0
Created: 2026-07-29
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExposurePolicy:
    """Límites informativos para selecciones del mismo partido."""

    max_markets_per_match: int
    max_markets_per_component: int
    correlation_threshold: float
    components: tuple[tuple[str, ...], ...]

    def validate(self, markets: list[str]) -> bool:
        """Comprueba una selección sin calcular stakes."""

        if len(markets) > self.max_markets_per_match:
            return False
        return all(
            sum(name in component for name in markets)
            <= self.max_markets_per_component
            for component in self.components
        )


def dependency_components(
    names: list[str], matrix: list[list[float]], threshold: float,
) -> tuple[tuple[str, ...], ...]:
    """Construye componentes conexos por correlación positiva."""

    parent = list(range(len(names)))
    for left in range(len(names)):
        for right in range(left + 1, len(names)):
            if matrix[left][right] >= threshold:
                _union(parent, left, right)
    groups: dict[int, list[str]] = {}
    for index, name in enumerate(names):
        groups.setdefault(_root(parent, index), []).append(name)
    return tuple(
        tuple(sorted(group)) for group in sorted(
            groups.values(), key=lambda value: (value[0], len(value)))
    )


def _root(parent: list[int], index: int) -> int:
    """Obtiene y comprime la raíz de un nodo."""

    while parent[index] != index:
        parent[index] = parent[parent[index]]
        index = parent[index]
    return index


def _union(parent: list[int], left: int, right: int) -> None:
    """Une dos componentes."""

    left_root, right_root = _root(parent, left), _root(parent, right)
    if left_root != right_root:
        parent[right_root] = left_root


if __name__ == "__main__":
    COMPONENTS = dependency_components(
        ["a", "b", "c"], [[1.0, 0.4, 0.0], [0.4, 1.0, 0.0],
                          [0.0, 0.0, 1.0]], 0.3)
    POLICY = ExposurePolicy(2, 1, 0.3, COMPONENTS)
    assert POLICY.validate(["a", "c"])
    assert not POLICY.validate(["a", "b"])


# Version: 1.0.0
# Created: 2026-07-29
