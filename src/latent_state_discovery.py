"""Utilidades auditables para descubrir estados latentes con duración.

Requirements:
    numpy>=2.0
    scipy>=1.14
    scikit-learn>=1.5

Version: 1.0.0
Created: 2026-07-27
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any, Iterable

import numpy as np
from scipy.stats import spearmanr

FEATURE_METRICS = (
    "goals", "shots", "shots_on_target", "corners", "pressure",
    "fouls", "yellow_cards", "red_cards",
)


def joint_features(home: dict[str, Any], away: dict[str, Any]) -> list[float]:
    """Construye emisiones conjuntas sin incluir información futura."""

    totals = [float(home[name]) + float(away[name]) for name in FEATURE_METRICS]
    differences = [
        float(home[name]) - float(away[name]) for name in FEATURE_METRICS
    ]
    return totals + differences


def runs(match_ids: np.ndarray, states: np.ndarray) -> dict[int, list[int]]:
    """Extrae duraciones completas por estado y partido."""

    output: dict[int, list[int]] = defaultdict(list)
    start = 0
    while start < len(states):
        end = start + 1
        while (end < len(states) and match_ids[end] == match_ids[start]
               and states[end] == states[start]):
            end += 1
        output[int(states[start])].append(end - start)
        start = end
    return output


def duration_probabilities(
    match_ids: np.ndarray,
    states: np.ndarray,
    state_count: int,
    maximum: int = 18,
    alpha: float = 1.0,
) -> np.ndarray:
    """Estima distribución discreta suavizada de duración."""

    values = np.full((state_count, maximum), alpha, dtype=float)
    for state, durations in runs(match_ids, states).items():
        for duration in durations:
            values[state, min(duration, maximum) - 1] += 1.0
    return values / values.sum(axis=1, keepdims=True)


def duration_nll(
    probabilities: np.ndarray,
    match_ids: np.ndarray,
    states: np.ndarray,
) -> float:
    """Calcula NLL medio por run para duración discreta."""

    losses = []
    for state, durations in runs(match_ids, states).items():
        for duration in durations:
            value = probabilities[state, min(duration, probabilities.shape[1]) - 1]
            losses.append(-math.log(max(float(value), 1e-12)))
    return float(np.mean(losses))


def geometric_probabilities(
    match_ids: np.ndarray,
    states: np.ndarray,
    state_count: int,
    maximum: int = 18,
) -> np.ndarray:
    """Construye duración geométrica truncada desde persistencia media."""

    output = np.zeros((state_count, maximum), dtype=float)
    observed = runs(match_ids, states)
    for state in range(state_count):
        mean = float(np.mean(observed.get(state, [1])))
        exit_probability = min(max(1.0 / mean, 1e-4), 1.0)
        values = np.array([
            exit_probability * (1.0 - exit_probability) ** index
            for index in range(maximum)
        ])
        output[state] = values / values.sum()
    return output


def next_goal_risk(
    states: np.ndarray,
    next_goals: np.ndarray,
    state_count: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Calcula riesgo futuro y soporte por estado."""

    risks, support = np.zeros(state_count), np.zeros(state_count, dtype=int)
    for state in range(state_count):
        mask = (states == state) & np.isfinite(next_goals)
        support[state] = int(mask.sum())
        risks[state] = float(next_goals[mask].mean()) if mask.any() else 0.0
    return risks, support


def league_order_stability(
    leagues: np.ndarray,
    states: np.ndarray,
    next_goals: np.ndarray,
    global_risk: np.ndarray,
    minimum: int = 20,
) -> dict[str, Any]:
    """Mide concordancia del orden de riesgo en ligas con soporte."""

    stable, admitted, details = 0, 0, {}
    for league in sorted(set(leagues.tolist())):
        mask = leagues == league
        risks, support = next_goal_risk(states[mask], next_goals[mask], len(global_risk))
        if int(support.min()) < minimum:
            continue
        correlation = float(spearmanr(global_risk, risks).statistic)
        admitted += 1
        stable += int(np.isfinite(correlation) and correlation > 0.0)
        details[str(league)] = {"spearman": correlation,
                                "minimum_state_support": int(support.min())}
    return {"admitted": admitted, "stable": stable,
            "rate": stable / admitted if admitted else 0.0, "details": details}


def occupancy(states: Iterable[int], state_count: int) -> dict[int, float]:
    """Calcula ocupación relativa por estado."""

    values = list(states)
    counts = Counter(values)
    return {state: counts[state] / max(len(values), 1) for state in range(state_count)}


def emission_parameter_count(state_count: int, feature_count: int) -> int:
    """Cuenta parámetros de mezcla gaussiana diagonal."""

    return state_count * feature_count * 2 + state_count - 1


def bic_per_observation(
    log_likelihood: float,
    observations: int,
    parameters: int,
) -> float:
    """Calcula BIC normalizado para comparar candidatos."""

    return (-2.0 * log_likelihood + parameters * math.log(observations)) / observations


# Version: 1.0.0 - 2026-07-27
