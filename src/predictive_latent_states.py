"""Estados latentes predictivos ordenados por riesgo causal.

Requirements:
    numpy>=2.0
    scikit-learn>=1.5

Version: 2.0.0
Created: 2026-07-27
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler


@dataclass(slots=True)
class PredictiveStateModel:
    """Modelo regularizado que convierte riesgo continuo en estados."""

    state_count: int
    c_value: float
    quantiles: tuple[float, ...] | None = None
    scaler: StandardScaler | None = None
    classifier: LogisticRegression | None = None
    boundaries: np.ndarray | None = None

    def fit(self, features: np.ndarray, targets: np.ndarray) -> None:
        """Ajusta transformación, riesgo y límites usando desarrollo."""

        self.scaler = StandardScaler().fit(features)
        transformed = self.scaler.transform(features)
        self.classifier = LogisticRegression(
            C=self.c_value, max_iter=400, solver="lbfgs", random_state=27
        ).fit(transformed, targets)
        risk = self.classifier.predict_proba(transformed)[:, 1]
        quantiles = self.quantiles or tuple(
            np.linspace(0.0, 1.0, self.state_count + 1)[1:-1]
        )
        if len(quantiles) != self.state_count - 1:
            raise ValueError("invalid_state_quantile_count")
        if any(left >= right for left, right in zip(quantiles, quantiles[1:])):
            raise ValueError("state_quantiles_not_strictly_increasing")
        self.boundaries = np.quantile(risk, quantiles)

    def risk(self, features: np.ndarray) -> np.ndarray:
        """Predice riesgo continuo desde emisiones contemporáneas."""

        if self.scaler is None or self.classifier is None:
            raise RuntimeError("predictive_state_model_not_fitted")
        transformed = self.scaler.transform(features)
        return self.classifier.predict_proba(transformed)[:, 1]

    def states(self, features: np.ndarray) -> np.ndarray:
        """Asigna regímenes ordenados mediante límites congelados."""

        if self.boundaries is None:
            raise RuntimeError("predictive_state_boundaries_not_fitted")
        return np.digitize(self.risk(features), self.boundaries)

    def coefficients(self) -> np.ndarray:
        """Devuelve coeficientes en el espacio normalizado."""

        if self.classifier is None:
            raise RuntimeError("predictive_state_model_not_fitted")
        return self.classifier.coef_[0].copy()


def permutation_spreads(
    states: np.ndarray,
    targets: np.ndarray,
    state_count: int,
    repetitions: int,
    seed: int,
) -> np.ndarray:
    """Genera distribución nula del spread preservando estados."""

    generator = np.random.default_rng(seed)
    valid = np.isfinite(targets)
    output = np.zeros(repetitions)
    for index in range(repetitions):
        shuffled = targets.copy()
        shuffled[valid] = generator.permutation(targets[valid])
        output[index] = _spread(states, shuffled, state_count)
    return output


def _spread(states: np.ndarray, targets: np.ndarray, state_count: int) -> float:
    """Calcula spread de medias futuras por estado."""

    values = []
    for state in range(state_count):
        mask = (states == state) & np.isfinite(targets)
        values.append(float(targets[mask].mean()) if mask.any() else 0.0)
    return float(np.ptp(values))


# Version: 2.0.0 - 2026-07-27
