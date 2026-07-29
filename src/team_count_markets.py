"""Dominio y solver para mercados pre-match de conteos por equipo.

Requirements:
    numpy>=2.0
    scikit-learn>=1.5

Version: 1.1.0
Created: 2026-07-28
"""
from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import numpy as np
from sklearn.linear_model import PoissonRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


@dataclass(frozen=True, slots=True)
class CountMetricSpec:
    """Define un conteo y su valor seguro de arranque."""

    name: str
    source_field: str
    first_half_only: bool
    safe_default: float


class CountMarketSolver(ABC):
    """Puerto abstracto para estimadores pre-match de conteos."""

    @abstractmethod
    def fit(self, features: np.ndarray, targets: np.ndarray) -> None:
        """Ajusta el solver con observaciones históricas."""

    @abstractmethod
    def predict(self, features: np.ndarray) -> np.ndarray:
        """Predice intensidades positivas."""

    @abstractmethod
    def native_model(self) -> Any:
        """Devuelve el modelo serializable de infraestructura."""


class SklearnPoissonSolver(CountMarketSolver):
    """Adaptador de regresión Poisson regularizada."""

    def __init__(self, alpha: float) -> None:
        """Inicializa un pipeline determinista."""

        self._model = Pipeline([
            ("scale", StandardScaler()),
            ("poisson", PoissonRegressor(
                alpha=alpha, max_iter=500, tol=1e-8)),
        ])

    def fit(self, features: np.ndarray, targets: np.ndarray) -> None:
        """Ajusta el pipeline."""

        self._model.fit(features, targets)

    def predict(self, features: np.ndarray) -> np.ndarray:
        """Emite lambdas acotadas para estabilidad."""

        values = self._model.predict(features)
        return np.clip(values, 1e-6, 100.0)

    def native_model(self) -> Any:
        """Expone el pipeline para persistencia controlada."""

        return self._model


def poisson_over_probability(rate: float, integer_line: int) -> float:
    """Calcula P(X > línea entera) para una Poisson."""

    safe_rate = max(float(rate), 1e-12)
    term = math.exp(-safe_rate)
    cumulative = term
    for value in range(1, integer_line + 1):
        term *= safe_rate / value
        cumulative += term
    return min(max(1.0 - cumulative, 0.0), 1.0)


def negative_binomial_over_probability(
    rate: float, dispersion: float, integer_line: int,
) -> float:
    """Calcula P(X > línea) con varianza μ + φμ²."""

    mean = max(float(rate), 1e-12)
    phi = max(float(dispersion), 1e-8)
    shape = 1.0 / phi
    success = shape / (shape + mean)
    term = success**shape
    cumulative = term
    for value in range(1, integer_line + 1):
        term *= ((value - 1.0 + shape) / value) * (1.0 - success)
        cumulative += term
    return min(max(1.0 - cumulative, 0.0), 1.0)


def negative_binomial_distribution(
    rate: float, dispersion: float, maximum: int = 100,
) -> dict[int, float]:
    """Construye una PMF NB completa con cola residual controlada."""

    mean = max(float(rate), 1e-12)
    phi = max(float(dispersion), 1e-8)
    shape = 1.0 / phi
    success = shape / (shape + mean)
    probability = success**shape
    output = {0: probability}
    for count in range(1, maximum + 1):
        probability *= (
            (count - 1.0 + shape) / count) * (1.0 - success)
        output[count] = probability
        if count > mean and 1.0 - sum(output.values()) < 1e-12:
            break
    total = sum(output.values())
    return {count: value / total for count, value in output.items()}


def poisson_deviance(actual: float, predicted: float) -> float:
    """Calcula deviance Poisson individual."""

    expected = max(float(predicted), 1e-12)
    observed = max(float(actual), 0.0)
    ratio = observed * math.log(observed / expected) if observed > 0 else 0.0
    return 2.0 * (expected - observed + ratio)


# Version: 1.1.0
# Created: 2026-07-28
