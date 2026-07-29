"""Baseline temporal causal para targets direccionales conjuntos.

Requirements:
    numpy>=2.0
    scikit-learn>=1.5

Version: 1.0.0
Created: 2026-07-27
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np

CLASS_NAMES = ("neither", "home_only", "away_only", "both")
METRICS = ("goals", "shots", "shots_on_target", "corners", "pressure")


@dataclass(slots=True)
class RunningProfile:
    """Acumula estadísticas observadas en partidos anteriores."""

    count: int = 0
    totals: dict[str, float] = field(
        default_factory=lambda: defaultdict(float)
    )

    def mean(self, name: str, prior: float, strength: float = 5.0) -> float:
        """Devuelve una media suavizada hacia el prior causal."""

        return (self.totals[name] + strength * prior) / (self.count + strength)

    def update(self, row: dict[str, Any]) -> None:
        """Incorpora una ventana después de emitir sus features."""

        self.count += 1
        for name in METRICS:
            self.totals[name] += float(row[name])
            self.totals[f"{name}_conceded"] += float(
                row.get(f"{name}_conceded", 0.0)
            )


class CausalProfileBuilder:
    """Construye perfiles rolling sin leer eventos del partido objetivo."""

    def __init__(self) -> None:
        """Inicializa historiales de equipo y liga."""

        self._teams: dict[tuple[int, int], RunningProfile] = defaultdict(
            RunningProfile
        )
        self._leagues: dict[tuple[str, int], RunningProfile] = defaultdict(
            RunningProfile
        )

    def features(
        self,
        home: dict[str, Any],
        away: dict[str, Any],
    ) -> dict[str, float]:
        """Emite features previas al kickoff para un intervalo."""

        league = str(home["league_slug"])
        window = int(home["window_index"])
        parent = self._leagues[(league, window)]
        result = {"window_index": float(window), "home_history": 0.0,
                  "away_history": 0.0}
        self._role_features(result, "home", int(home["team_id"]), window, parent)
        self._role_features(result, "away", int(away["team_id"]), window, parent)
        return result

    def _role_features(
        self,
        output: dict[str, float],
        role: str,
        team_id: int,
        window: int,
        parent: RunningProfile,
    ) -> None:
        """Añade medias suavizadas de un equipo y su soporte."""

        profile = self._teams[(team_id, window)]
        output[f"{role}_history"] = float(profile.count)
        for name in METRICS:
            prior = parent.mean(name, _safe_prior(name))
            output[f"{role}_{name}"] = profile.mean(name, prior)
            conceded = parent.mean(f"{name}_conceded", _safe_prior(name))
            output[f"{role}_{name}_conceded"] = profile.mean(
                f"{name}_conceded", conceded
            )

    def update(self, home: dict[str, Any], away: dict[str, Any]) -> None:
        """Actualiza historiales sólo después de congelar el partido."""

        league, window = str(home["league_slug"]), int(home["window_index"])
        for row, rival in ((home, away), (away, home)):
            observed = {**row, **{
                f"{name}_conceded": float(rival[name]) for name in METRICS
            }}
            self._teams[(int(row["team_id"]), window)].update(observed)
            self._leagues[(league, window)].update(observed)


def _safe_prior(name: str) -> float:
    """Devuelve un prior conservador por ventana de 15 minutos."""

    return {"goals": 0.22, "shots": 1.9, "shots_on_target": 0.65,
            "corners": 0.85, "pressure": 2.75}[name]


def target_class(home_goals: int, away_goals: int) -> int:
    """Codifica el resultado direccional conjunto."""

    if home_goals > 0 and away_goals > 0:
        return 3
    if home_goals > 0:
        return 1
    return 2 if away_goals > 0 else 0


def analytical_probabilities(features: dict[str, float]) -> np.ndarray:
    """Calcula probabilidades conjuntas desde intensidades same-data."""

    home_rate = 0.5 * (
        features["home_goals"] + features["away_goals_conceded"]
    )
    away_rate = 0.5 * (
        features["away_goals"] + features["home_goals_conceded"]
    )
    home = 1.0 - math.exp(-max(home_rate, 1e-6))
    away = 1.0 - math.exp(-max(away_rate, 1e-6))
    return np.array([
        (1.0 - home) * (1.0 - away), home * (1.0 - away),
        (1.0 - home) * away, home * away,
    ])


def temperature_scale(probabilities: np.ndarray, temperature: float) -> np.ndarray:
    """Aplica calibración de temperatura preservando normalización."""

    logits = np.log(np.clip(probabilities, 1e-12, 1.0)) / temperature
    logits -= logits.max(axis=1, keepdims=True)
    values = np.exp(logits)
    return values / values.sum(axis=1, keepdims=True)


def multiclass_log_loss(probabilities: np.ndarray, targets: np.ndarray) -> float:
    """Calcula log-loss medio multiclase."""

    chosen = probabilities[np.arange(len(targets)), targets]
    return float(-np.log(np.clip(chosen, 1e-12, 1.0)).mean())


def multiclass_brier(probabilities: np.ndarray, targets: np.ndarray) -> float:
    """Calcula Brier multiclase medio."""

    truth = np.eye(probabilities.shape[1])[targets]
    return float(np.square(probabilities - truth).sum(axis=1).mean())


def expected_calibration_error(
    probabilities: np.ndarray,
    targets: np.ndarray,
    bins: int = 10,
) -> float:
    """Calcula ECE por confianza máxima."""

    confidence, prediction = probabilities.max(axis=1), probabilities.argmax(axis=1)
    error = 0.0
    for lower in np.linspace(0.0, 1.0, bins, endpoint=False):
        mask = (confidence >= lower) & (confidence < lower + 1.0 / bins)
        if mask.any():
            error += float(mask.mean()) * abs(
                float((prediction[mask] == targets[mask]).mean())
                - float(confidence[mask].mean())
            )
    return error


def probability_audit(probabilities: np.ndarray) -> dict[str, Any]:
    """Audita finitud, límites y suma de probabilidades."""

    return {
        "finite": bool(np.isfinite(probabilities).all()),
        "within_bounds": bool(((probabilities >= 0.0) & (probabilities <= 1.0)).all()),
        "max_sum_error": float(np.abs(probabilities.sum(axis=1) - 1.0).max()),
    }


def binary_log_loss(probabilities: np.ndarray, targets: np.ndarray) -> float:
    """Calcula log-loss binario con clipping estable."""

    values = np.clip(probabilities, 1e-12, 1.0 - 1e-12)
    return float(-(targets * np.log(values) + (1 - targets) * np.log(1 - values)).mean())


def projected_metrics(
    probabilities: np.ndarray,
    targets: np.ndarray,
) -> dict[str, float]:
    """Evalúa proyecciones binarias de las cuatro clases."""

    mappings = {
        "any_goal": (probabilities[:, 1:].sum(axis=1), targets != 0),
        "home_scores": (probabilities[:, [1, 3]].sum(axis=1),
                        np.isin(targets, [1, 3])),
        "away_scores": (probabilities[:, [2, 3]].sum(axis=1),
                        np.isin(targets, [2, 3])),
    }
    return {name: binary_log_loss(values, truth.astype(int))
            for name, (values, truth) in mappings.items()}


def feature_names(rows: Iterable[dict[str, float]]) -> list[str]:
    """Obtiene orden estable de columnas numéricas."""

    first = next(iter(rows))
    return sorted(first)


# Version: 1.0.0 - 2026-07-27
