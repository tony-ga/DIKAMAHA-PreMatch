"""Calibradores binarios causales para mercados pre-match.

# Requirements:
# numpy>=2
# scikit-learn>=1.5

Version: 1.0.0
Created: 2026-07-29
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Self

import numpy as np
from sklearn.linear_model import LogisticRegression


class ProbabilityCalibrator(ABC):
    """Contrato abstracto para calibración probabilística binaria."""

    @abstractmethod
    def fit(
        self, probabilities: list[float], outcomes: list[bool],
    ) -> Self:
        """Ajusta el calibrador."""

    @abstractmethod
    def predict(self, probability: float) -> float:
        """Calibra una probabilidad."""


class PlattCalibrator(ProbabilityCalibrator):
    """Escalamiento logístico regularizado sobre el logit original."""

    def __init__(self, regularization: float = 1.0) -> None:
        """Inicializa un calibrador no ajustado."""

        self._model = LogisticRegression(
            C=regularization, solver="lbfgs", random_state=9500)
        self._fitted = False

    def fit(
        self, probabilities: list[float], outcomes: list[bool],
    ) -> Self:
        """Ajusta con observaciones históricas de ambas clases."""

        if len(probabilities) != len(outcomes):
            raise ValueError("probability_outcome_length_mismatch")
        if len(set(outcomes)) != 2:
            raise ValueError("platt_requires_two_classes")
        self._model.fit(_features(probabilities), np.asarray(outcomes, dtype=int))
        self._fitted = True
        return self

    def predict(self, probability: float) -> float:
        """Devuelve la probabilidad calibrada."""

        if not self._fitted:
            raise RuntimeError("platt_calibrator_not_fitted")
        value = self._model.predict_proba(_features([probability]))[0, 1]
        return float(value)

    def parameters(self) -> dict[str, float]:
        """Exporta parámetros suficientes para inferencia sin reentrenar."""

        if not self._fitted:
            raise RuntimeError("platt_calibrator_not_fitted")
        return {
            "coefficient": float(self._model.coef_[0, 0]),
            "intercept": float(self._model.intercept_[0]),
        }


def _features(probabilities: list[float]) -> np.ndarray:
    """Convierte probabilidades a logits acotados."""

    values = np.clip(np.asarray(probabilities, dtype=float), 1e-6, 1 - 1e-6)
    return np.log(values / (1.0 - values)).reshape(-1, 1)


if __name__ == "__main__":
    CALIBRATOR = PlattCalibrator().fit(
        [0.1, 0.2, 0.8, 0.9], [False, False, True, True])
    assert CALIBRATOR.predict(0.8) > CALIBRATOR.predict(0.2)
    assert 0.0 < CALIBRATOR.predict(0.5) < 1.0


# Version: 1.0.0
# Created: 2026-07-29
