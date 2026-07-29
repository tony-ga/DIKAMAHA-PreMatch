"""Pooling histórico explícito para ramas Markov contrafactuales.

El pooling conserva lado del primer gol y ventana temporal. La fuerza relativa
se suaviza hacia el contexto padre sólo con evidencia de desarrollo; nunca se
mezclan validación ni confirmación para estimar parámetros.

Version: 1.0.0
Created: 2026-07-16
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from src.markov_counterfactual import BEHAVIOR_TYPES, SECOND_OUTCOMES, _normalize


@dataclass(frozen=True, slots=True)
class ContextPolicy:
    """Política congelada para soporte y fallback contrafactual."""

    version: str = "contextual_support_v1"
    minimum_exact_support: int = 10
    minimum_reportable_support: int = 30
    development_only: bool = True
    pooling_formula: str = "(n_exact*p_exact + sqrt(n_parent)*p_parent)/(n_exact+sqrt(n_parent))"
    fallback_order: tuple[str, ...] = ("exact", "pooled_comparable", "global")


def context_key(side: str, window: str, strength: str) -> str:
    """Forma clave exacta preservando marcador, lado, ventana y fuerza."""

    return f"{side}|{window}|{strength}"


def parent_key(side: str, window: str) -> str:
    """Forma contexto comparable que no mezcla lado ni ventana."""

    return f"{side}|{window}"


class ContextualSupportEstimator:
    """Estimador de transición y comportamiento bajo tres estrategias fijas."""

    def __init__(self, policy: ContextPolicy | None = None) -> None:
        """Inicializa acumuladores sin observar datos OOS."""

        self.policy = policy or ContextPolicy()
        self.transitions: dict[str, Counter[str]] = defaultdict(Counter)
        self.behavior: dict[str, Counter[str]] = defaultdict(Counter)
        self.context_matches: Counter[str] = Counter()

    def fit(self, rows: list[dict[str, Any]]) -> "ContextualSupportEstimator":
        """Ajusta soporte exclusivamente con partidos completos development."""

        if any(row["block"] != "development" for row in rows):
            raise ValueError("context_support_fit_requires_development_only")
        for row in rows:
            actual = row["actual"]
            if actual["first_side"] is None or actual["second"] is None:
                continue
            side, window, strength = actual["first_side"], _window(actual["first_minute"]), row["strength_bin"]
            exact, parent = context_key(side, window, strength), parent_key(side, window)
            for key in (exact, parent, "global"):
                self.transitions[key][actual["second"]] += 1
                self.behavior[key].update(actual["behavior"])
                self.context_matches[key] += 1
        return self

    def distribution(self, side: str, window: str, strength: str, strategy: str) -> tuple[dict[str, float], dict[str, Any]]:
        """Devuelve distribución MLE, pooling parcial o global con provenance."""

        exact, parent = context_key(side, window, strength), parent_key(side, window)
        if strategy == "exact":
            return self._exact_or_global(exact)
        if strategy == "pooled_comparable":
            return self._pooled(exact, parent)
        if strategy == "global":
            return self._global()
        raise ValueError("unknown_context_strategy")

    def behavior_mean(self, side: str, window: str, strength: str, strategy: str) -> tuple[dict[str, float], dict[str, Any]]:
        """Devuelve comportamiento por rama con la misma política de pooling."""

        exact, parent = context_key(side, window, strength), parent_key(side, window)
        if strategy == "exact" and self.context_matches[exact] >= self.policy.minimum_exact_support:
            return self._mean(exact), self._meta("exact", exact)
        if strategy == "pooled_comparable":
            return self._pooled_mean(exact, parent)
        return self._mean("global"), self._meta("global", "global")

    def _exact_or_global(self, exact: str) -> tuple[dict[str, float], dict[str, Any]]:
        """Conserva exactitud; si falta soporte cae explícitamente a global."""

        if self.context_matches[exact] >= self.policy.minimum_exact_support:
            return self._probabilities(exact), self._meta("exact", exact)
        probabilities, meta = self._global()
        return probabilities, {**meta, "fallback_from": exact, "warning": "low_exact_support"}

    def _pooled(self, exact: str, parent: str) -> tuple[dict[str, float], dict[str, Any]]:
        """Aplica shrinkage data-driven hacia contexto comparable del mismo lado/ventana."""

        n_exact, n_parent = self.context_matches[exact], self.context_matches[parent]
        if n_parent == 0:
            probabilities, meta = self._global()
            return probabilities, {**meta, "fallback_from": parent, "warning": "missing_comparable_support"}
        prior = math.sqrt(n_parent)
        parent_probs = self._probabilities(parent)
        exact_probs = self._probabilities(exact) if n_exact else parent_probs
        values = {key: (n_exact * exact_probs[key] + prior * parent_probs[key]) / (n_exact + prior) for key in SECOND_OUTCOMES}
        return _normalize(values), self._meta("pooled_comparable", parent, exact_support=n_exact, prior_weight=prior)

    def _global(self) -> tuple[dict[str, float], dict[str, Any]]:
        """Devuelve fallback global identificable y con soporte explícito."""

        return self._probabilities("global"), self._meta("global", "global")

    def _probabilities(self, key: str) -> dict[str, float]:
        """Calcula MLE normalizado de transiciones observadas."""

        return _normalize({outcome: float(self.transitions[key][outcome]) for outcome in SECOND_OUTCOMES})

    def _mean(self, key: str) -> dict[str, float]:
        """Calcula medias de comportamiento por partido contextual."""

        support = self.context_matches[key]
        return {event: value / support for event, value in self.behavior[key].items()} if support else {}

    def _pooled_mean(self, exact: str, parent: str) -> tuple[dict[str, float], dict[str, Any]]:
        """Suaviza medias conductuales con la misma exposición histórica."""

        n_exact, n_parent = self.context_matches[exact], self.context_matches[parent]
        if n_parent == 0:
            return self._mean("global"), self._meta("global", "global", warning="missing_comparable_support")
        prior, exact_mean, parent_mean = math.sqrt(n_parent), self._mean(exact), self._mean(parent)
        keys = set(exact_mean) | set(parent_mean)
        values = {key: (n_exact * exact_mean.get(key, 0.0) + prior * parent_mean.get(key, 0.0)) / (n_exact + prior) for key in keys}
        return values, self._meta("pooled_comparable", parent, exact_support=n_exact, prior_weight=prior)

    def _meta(self, strategy: str, source: str, **extra: Any) -> dict[str, Any]:
        """Registra support y advertencias sin ocultar evidencia escasa."""

        support = self.context_matches[source]
        return {"strategy": strategy, "source_context": source, "support": support,
                "low_evidence": support < self.policy.minimum_reportable_support, **extra}

    def support(self) -> dict[str, Any]:
        """Exporta soporte por contexto y celdas de baja evidencia."""

        rows = [{"context": key, "matches": self.context_matches[key], "events": sum(self.behavior[key].values()),
                 "branches": sum(self.transitions[key].values()), "low_evidence": self.context_matches[key] < self.policy.minimum_reportable_support}
                for key in sorted(self.context_matches)]
        return {"contexts": rows, "minimum_exact_support": self.policy.minimum_exact_support,
                "minimum_reportable_support": self.policy.minimum_reportable_support}


def _window(minute: int) -> str:
    """Evita dependencia circular con la implementación de ramas base."""

    return "early" if minute < 30 else "middle" if minute < 60 else "late"


# Version: 1.0.0
# Created: 2026-07-16
