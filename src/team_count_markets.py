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


FULL_MATCH = "full_match"
FIRST_HALF = "first_half"
SECOND_HALF = "second_half"
# El corpus publica ventanas de 15 minutos: los tres primeros índices son la
# primera mitad. Mismo corte que ya usa `src/team_market_markov.py`.
FIRST_HALF_WINDOWS = 3


@dataclass(frozen=True, slots=True)
class CountMetricSpec:
    """Define un conteo, su periodo y su valor seguro de arranque.

    `period` sustituye al `first_half_only: bool` original. El booleano sólo
    podía expresar dos de los tres periodos que el corpus soporta, así que la
    segunda mitad no era representable y la escalera auditada nunca pudo
    cubrirla -de ahí que `METRIC_LADDERS` no tuviera ninguna entrada de 2T-.
    """

    name: str
    source_field: str
    period: str
    safe_default: float


def window_belongs_to(period: str, window_index: int) -> bool:
    """Indica si una ventana de 15 minutos pertenece a ese periodo."""

    if period == FIRST_HALF:
        return window_index < FIRST_HALF_WINDOWS
    if period == SECOND_HALF:
        return window_index >= FIRST_HALF_WINDOWS
    return True


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

        if not np.isfinite(alpha) or alpha < 0.0:
            raise ValueError("alpha debe ser finito y no negativo.")
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
        values = np.asarray(values, dtype=float)
        if not np.isfinite(values).all():
            raise FloatingPointError("El solver emitió tasas no finitas.")
        return np.clip(values, 1e-6, 100.0)

    def native_model(self) -> Any:
        """Expone el pipeline para persistencia controlada."""

        return self._model


def poisson_over_probability(rate: float, integer_line: int) -> float:
    """Calcula P(X > línea entera) para una Poisson."""

    safe_rate = _valid_rate(rate)
    integer_line = _valid_line(integer_line)
    term = math.exp(-safe_rate)
    cumulative = term
    for value in range(1, integer_line + 1):
        term *= safe_rate / value
        cumulative += term
    return min(max(1.0 - cumulative, 0.0), 1.0)


def combined_dispersion(
    dispersion: float, home_rate: float, away_rate: float,
    correlation: float = 0.0,
) -> float:
    """Dispersión equivalente al sumar los conteos de los dos equipos.

    Una NB con varianza `μ + φμ²` sumada a otra da, para el total:

        Var(T) = Var(H) + Var(A) + 2·ρ·σ_H·σ_A

    La versión original omitía el término de covarianza, es decir asumía
    independencia entre local y visitante. Medido sobre el corpus, esa
    suposición es falsa y en direcciones opuestas según la métrica:

    - **Córners `ρ = -0.31`** y tiros `ρ = -0.15`: son casi de suma cero, el
      equipo que domina el territorio deja al rival con menos. Asumir
      independencia **sobreestima** la varianza del total, ensancha la
      distribución y subestima las probabilidades -exactamente el sesgo que
      la auditoría de escalera encontró en los mercados `total`.
    - **Tarjetas `ρ = +0.19`**: un partido áspero reparte para ambos lados, de
      modo que la independencia subestima la varianza.

    `correlation = 0.0` reproduce exactamente el comportamiento anterior.
    """

    home = max(float(home_rate), 0.0)
    away = max(float(away_rate), 0.0)
    total = max(home + away, 1e-9)
    phi = max(float(dispersion), 0.0)
    rho = min(max(float(correlation), -1.0), 1.0)
    variance_home = home + phi * home * home
    variance_away = away + phi * away * away
    covariance = 2.0 * rho * math.sqrt(
        max(variance_home, 0.0) * max(variance_away, 0.0))
    # La varianza del total nunca puede caer por debajo de la de un Poisson:
    # una correlación muy negativa podría empujar el término φ a negativo, que
    # la NB no puede representar.
    excess = variance_home + variance_away + covariance - total
    return max(excess / (total * total), 1e-8)


def negative_binomial_over_probability(
    rate: float, dispersion: float, integer_line: int,
) -> float:
    """Calcula P(X > línea) con varianza μ + φμ²."""

    mean = _valid_rate(rate)
    phi = _valid_dispersion(dispersion)
    integer_line = _valid_line(integer_line)
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
    tail_tolerance: float = 1e-12, hard_maximum: int = 10_000,
) -> dict[int, float]:
    """Construye una PMF NB adaptativa sin truncar y ocultar masa material."""

    mean = _valid_rate(rate)
    phi = _valid_dispersion(dispersion)
    maximum = _valid_line(maximum)
    hard_maximum = _valid_line(hard_maximum)
    if hard_maximum < maximum:
        raise ValueError("hard_maximum debe ser mayor o igual que maximum.")
    if not math.isfinite(tail_tolerance) or not 0.0 < tail_tolerance < 1.0:
        raise ValueError("tail_tolerance debe estar entre cero y uno.")
    shape = 1.0 / phi
    success = shape / (shape + mean)
    probability = success**shape
    if not math.isfinite(probability) or probability < 0.0:
        raise FloatingPointError("No se pudo representar la masa inicial NB.")
    output = {0: probability}
    total = probability
    converged = mean == 0.0 and 1.0 - total <= tail_tolerance
    for count in range(1, hard_maximum + 1):
        probability *= (
            (count - 1.0 + shape) / count) * (1.0 - success)
        if not math.isfinite(probability) or probability < 0.0:
            raise FloatingPointError("La recurrencia NB produjo masa inválida.")
        output[count] = probability
        total += probability
        residual = max(0.0, 1.0 - total)
        if count >= maximum and count > mean and residual <= tail_tolerance:
            converged = True
            break
    if not converged:
        raise FloatingPointError("La cola NB no convergió dentro de hard_maximum.")
    if not math.isfinite(total) or total <= 0.0 or total > 1.0 + 1e-10:
        raise FloatingPointError("La masa NB acumulada es inválida.")
    return {count: value / total for count, value in output.items()}


def poisson_deviance(actual: float, predicted: float) -> float:
    """Calcula deviance Poisson individual."""

    observed = _valid_rate(actual)
    expected = _valid_rate(predicted)
    if expected == 0.0:
        return 0.0 if observed == 0.0 else math.inf
    expected = max(expected, 1e-12)
    ratio = observed * math.log(observed / expected) if observed > 0 else 0.0
    return 2.0 * (expected - observed + ratio)


def _valid_rate(value: float) -> float:
    """Valida una media de conteo."""

    rate = float(value)
    if not math.isfinite(rate) or rate < 0.0:
        raise ValueError("La tasa debe ser finita y no negativa.")
    return rate


def _valid_dispersion(value: float) -> float:
    """Valida una dispersión NB estrictamente positiva."""

    dispersion = float(value)
    if not math.isfinite(dispersion) or dispersion <= 0.0:
        raise ValueError("La dispersión debe ser finita y positiva.")
    return dispersion


def _valid_line(value: int) -> int:
    """Valida una línea discreta no negativa."""

    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise ValueError("La línea debe ser un entero no negativo.")
    line = int(value)
    if line < 0:
        raise ValueError("La línea debe ser un entero no negativo.")
    return line


# Version: 1.1.0
# Created: 2026-07-28
