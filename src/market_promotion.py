"""Evaluación probabilística por mercado completo.

Requirements:
    numpy>=2

Version: 1.0.0
Created: 2026-07-29
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

import numpy as np


def binary_log_loss(probability: float, actual: bool) -> float:
    """Calcula log-loss binario acotado."""

    value = float(probability)
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError("probability_out_of_range")
    if not isinstance(actual, (bool, np.bool_)):
        raise ValueError("binary_outcome_required")
    value = min(max(value, 1e-12), 1.0 - 1e-12)
    return -math.log(value if actual else 1.0 - value)


def evaluate_markets(
    rows: list[dict[str, Any]], replicates: int = 10_000,
    seed: int = 9201,
) -> dict[str, Any]:
    """Evalúa y clasifica cada línea con bootstrap por partido."""

    if not rows:
        raise ValueError("promotion_rows_empty")
    if isinstance(replicates, bool) or not isinstance(
            replicates, (int, np.integer)) or replicates < 2:
        raise ValueError("bootstrap_replicates_invalid")
    if isinstance(seed, bool) or not isinstance(seed, (int, np.integer)):
        raise ValueError("bootstrap_seed_invalid")
    _validate_rows(rows)
    markets = sorted(rows[0]["outcomes"])
    metrics = {
        name: _market_metrics(rows, name, replicates, seed + index)
        for index, name in enumerate(markets)}
    approved = sorted(
        name for name, values in metrics.items()
        if _passes(values))
    return {"markets": metrics, "approved_markets": approved}


def _validate_rows(rows: list[dict[str, Any]]) -> None:
    """Valida IID, esquema de mercados y dominios antes del gate."""

    match_ids = [row.get("match_id") for row in rows]
    if any(isinstance(value, bool) or not isinstance(value, (int, np.integer))
           for value in match_ids):
        raise ValueError("match_id_required")
    if len(set(int(value) for value in match_ids)) != len(match_ids):
        raise ValueError("duplicate_match_id")
    expected = set(rows[0].get("outcomes", {}))
    if not expected:
        raise ValueError("markets_empty")
    for row in rows:
        if not str(row.get("league_slug", "")).strip():
            raise ValueError("league_slug_required")
        blocks = (
            row.get("outcomes"), row.get("probabilities"),
            row.get("baseline_probabilities"),
        )
        if any(not isinstance(block, dict) or set(block) != expected
               for block in blocks):
            raise ValueError("market_keys_mismatch")
        for outcome in blocks[0].values():
            if not isinstance(outcome, (bool, np.bool_)):
                raise ValueError("binary_outcome_required")
        for block in blocks[1:]:
            for value in block.values():
                if isinstance(value, bool) or not isinstance(
                        value, (int, float, np.integer, np.floating)):
                    raise ValueError("probability_must_be_numeric")
                probability = float(value)
                if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
                    raise ValueError("probability_out_of_range")


def _market_metrics(
    rows: list[dict[str, Any]], market: str,
    replicates: int, seed: int,
) -> dict[str, Any]:
    """Agrega métricas y estabilidad de una línea."""

    model, baseline, actual = _vectors(rows, market)
    improvements = baseline["loss"] - model["loss"]
    return {
        "matches": len(rows),
        "model_log_loss": float(np.mean(model["loss"])),
        "baseline_log_loss": float(np.mean(baseline["loss"])),
        "model_brier": float(np.mean(model["brier"])),
        "baseline_brier": float(np.mean(baseline["brier"])),
        "model_accuracy": float(np.mean(
            (model["probability"] >= 0.5) == actual)),
        "baseline_accuracy": float(np.mean(
            (baseline["probability"] >= 0.5) == actual)),
        "improvement_ci95": _bootstrap(
            improvements, replicates, seed),
        "league_nonnegative_rate": _league_rate(
            rows, market),
    }


def _vectors(
    rows: list[dict[str, Any]], market: str,
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], np.ndarray]:
    """Construye vectores modelo, baseline y outcome."""

    actual = np.asarray(
        [bool(row["outcomes"][market]) for row in rows], dtype=bool)
    model_probability = np.asarray(
        [float(row["probabilities"][market]) for row in rows])
    baseline_probability = np.asarray(
        [float(row["baseline_probabilities"][market]) for row in rows])
    model = _scores(model_probability, actual)
    baseline = _scores(baseline_probability, actual)
    return model, baseline, actual


def _scores(
    probabilities: np.ndarray, actual: np.ndarray,
) -> dict[str, np.ndarray]:
    """Calcula vectores de pérdida y Brier."""

    losses = np.asarray([
        binary_log_loss(value, bool(outcome))
        for value, outcome in zip(probabilities, actual)])
    return {
        "probability": probabilities, "loss": losses,
        "brier": (probabilities - actual.astype(float)) ** 2}


def _bootstrap(
    values: np.ndarray, replicates: int, seed: int,
) -> list[float]:
    """Remuestrea partidos completos de forma determinista."""

    generator = np.random.default_rng(seed)
    indices = generator.integers(
        0, len(values), size=(replicates, len(values)))
    means = values[indices].mean(axis=1)
    return [float(value) for value in np.quantile(means, [0.025, 0.975])]


def _league_rate(rows: list[dict[str, Any]], market: str) -> float:
    """Calcula estabilidad en ligas con al menos 30 partidos."""

    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        actual = bool(row["outcomes"][market])
        improvement = binary_log_loss(
            row["baseline_probabilities"][market], actual)
        improvement -= binary_log_loss(row["probabilities"][market], actual)
        grouped[str(row["league_slug"])].append(improvement)
    eligible = [values for values in grouped.values() if len(values) >= 30]
    if not eligible:
        return 0.0
    return float(np.mean([np.mean(values) >= 0.0 for values in eligible]))


def _passes(values: dict[str, Any]) -> bool:
    """Aplica el gate congelado por línea."""

    return (
        float(values["improvement_ci95"][0]) > 0.0
        and float(values["model_brier"]) <= float(values["baseline_brier"])
        and float(values["league_nonnegative_rate"]) >= 0.70)


# Version: 1.0.0
# Created: 2026-07-29
