"""Emisiones causales invariantes al dominio para estados Markov.

Requirements:
    numpy>=2.0

Version: 1.0.0
Created: 2026-07-28
"""
from __future__ import annotations

import numpy as np


def domain_invariant_features(features: np.ndarray) -> np.ndarray:
    """Transforma conteos en actividad, contraste, cuotas y eficiencia."""

    base = features[:, [1, 2, 3, 4, 5, 8, 9, 10, 11, 12]]
    contrasts = features[:, [1, 3, 4]] - features[:, [8, 9, 10]]
    shares = _shares(features[:, [1, 3, 4]], features[:, [8, 9, 10]])
    rates = np.column_stack((
        (features[:, 2] + 0.5) / (features[:, 1] + 1.0),
        (features[:, 4] + 0.5) / (features[:, 1] + 1.0),
        (features[:, 3] + 0.5) / (features[:, 1] + 1.0),
    ))
    return np.column_stack((base, contrasts, shares, rates))


def rolling_domain_features(
    features: np.ndarray,
    sequence_ids: np.ndarray,
) -> np.ndarray:
    """Añade memoria causal de 10 y 15 minutos sin cruzar partidos."""

    current = domain_invariant_features(features)
    rolling_two = _rolling_mean(current, sequence_ids, 2)
    rolling_three = _rolling_mean(current, sequence_ids, 3)
    return np.column_stack((current, rolling_two, rolling_three))


def _shares(own: np.ndarray, rival: np.ndarray) -> np.ndarray:
    """Calcula cuotas suavizadas de actividad direccional."""

    return (own + 1.0) / (own + rival + 2.0)


def _rolling_mean(
    values: np.ndarray,
    sequence_ids: np.ndarray,
    width: int,
) -> np.ndarray:
    """Calcula medias móviles incluyendo sólo el presente y su pasado."""

    output = np.zeros_like(values)
    start = 0
    for index in range(len(values)):
        if index == 0 or sequence_ids[index] != sequence_ids[index - 1]:
            start = index
        left = max(start, index - width + 1)
        output[index] = values[left:index + 1].mean(axis=0)
    return output


def feature_names() -> list[str]:
    """Devuelve nombres estables de las 57 emisiones resultantes."""

    current = [
        "shots", "shots_on_target", "corners", "pressure", "fouls",
        "shots_conceded", "corners_conceded", "pressure_conceded",
        "match_progress", "is_home", "shot_balance", "corner_balance",
        "pressure_balance", "shot_share", "corner_share", "pressure_share",
        "shot_accuracy", "pressure_per_shot", "corners_per_shot",
    ]
    return current + [f"{name}_mean_10m" for name in current] + [
        f"{name}_mean_15m" for name in current
    ]

# Version: 1.0.0 - 2026-07-28
