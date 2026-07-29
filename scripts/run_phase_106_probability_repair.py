"""Repara causalmente BTTS y sella su calibrador de producción.

# Requirements:
#   numpy>=2
#   scikit-learn>=1.5

Version: 1.0.0
Created: 2026-07-29
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / (
    "artifacts/phase_105_historical_1000_complete/"
    "ranked_1000_predictions.json")
OUTPUT = ROOT / "artifacts/phase_106_probability_repair"
WARMUP = 200
BOOTSTRAP = 10_000
SHRINKAGE_GRID = (20, 50, 100, 200, 500)
PRIOR_RATE = 0.50
LOGGER = logging.getLogger(__name__)


def _read() -> list[dict[str, Any]]:
    """Carga la cohorte en orden estrictamente temporal."""

    rows = json.loads(SOURCE.read_text(encoding="utf-8"))
    return sorted(rows, key=lambda row: (
        str(row["match_date"]), int(row["match_id"])))


def _loss(probability: float, outcome: bool) -> float:
    """Calcula log-loss binario acotado."""

    selected = probability if outcome else 1.0 - probability
    return -math.log(max(selected, 1e-12))


def _prequential(
    rows: list[dict[str, Any]], shrinkage: int,
) -> list[dict[str, Any]]:
    """Estima BTTS por liga usando únicamente resultados anteriores."""

    positives: dict[str, int] = defaultdict(int)
    totals: dict[str, int] = defaultdict(int)
    output: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        market = row["markets"]["btts"]
        raw, actual = float(market["probability"]), bool(market["actual"])
        league = str(row["league_slug"])
        calibrated = (
            positives[league] + shrinkage * PRIOR_RATE
        ) / (totals[league] + shrinkage)
        if index >= WARMUP:
            output.append(_prediction(row, raw, calibrated, actual))
        positives[league] += int(actual)
        totals[league] += 1
    return output


def _select_shrinkage(rows: list[dict[str, Any]]) -> int:
    """Selecciona pooling sólo dentro del warm-up inicial."""

    scores = {
        value: _warmup_score(rows[:WARMUP], value)
        for value in SHRINKAGE_GRID}
    return min(scores, key=scores.get)


def _warmup_score(rows: list[dict[str, Any]], shrinkage: int) -> float:
    """Puntúa pooling causal dentro del bloque de selección."""

    positives: dict[str, int] = defaultdict(int)
    totals: dict[str, int] = defaultdict(int)
    losses = []
    for index, row in enumerate(rows):
        league = str(row["league_slug"])
        outcome = bool(row["markets"]["btts"]["actual"])
        probability = (
            positives[league] + shrinkage * PRIOR_RATE
        ) / (totals[league] + shrinkage)
        if index >= 20:
            losses.append(_loss(probability, outcome))
        positives[league] += int(outcome)
        totals[league] += 1
    return float(np.mean(losses))


def _prediction(
    row: dict[str, Any], raw: float, calibrated: float, actual: bool,
) -> dict[str, Any]:
    """Materializa comparación raw/calibrada por partido."""

    return {
        "match_id": int(row["match_id"]),
        "match_date": str(row["match_date"]),
        "league_slug": str(row["league_slug"]),
        "raw_probability": raw, "calibrated_probability": calibrated,
        "actual": actual, "raw_log_loss": _loss(raw, actual),
        "calibrated_log_loss": _loss(calibrated, actual),
        "raw_brier": (raw - float(actual)) ** 2,
        "calibrated_brier": (calibrated - float(actual)) ** 2,
    }


def _ece(rows: list[dict[str, Any]], key: str) -> float:
    """Calcula ECE con diez bins fijos."""

    total, error = len(rows), 0.0
    for lower in np.linspace(0.0, 0.9, 10):
        selected = _bin(rows, key, lower, lower + 0.1)
        if selected:
            predicted = float(np.mean([row[key] for row in selected]))
            observed = float(np.mean([row["actual"] for row in selected]))
            error += len(selected) / total * abs(predicted - observed)
    return error


def _bin(
    rows: list[dict[str, Any]], key: str, lower: float, upper: float,
) -> list[dict[str, Any]]:
    """Selecciona un bin cerrado únicamente en el extremo final."""

    return [
        row for row in rows
        if lower <= float(row[key]) < upper
        or upper >= 1.0 and float(row[key]) == 1.0]


def _bootstrap(rows: list[dict[str, Any]]) -> list[float]:
    """Calcula IC95% pareado por partido."""

    deltas = np.asarray([
        row["raw_log_loss"] - row["calibrated_log_loss"]
        for row in rows], dtype=float)
    rng = np.random.default_rng(20260729)
    indexes = rng.integers(
        0, len(rows), size=(BOOTSTRAP, len(rows)))
    means = deltas[indexes].mean(axis=1)
    return [float(value) for value in np.quantile(means, [0.025, 0.975])]


def _stability(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Mide no degradación media de log-loss por liga."""

    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        grouped[str(row["league_slug"])].append(
            row["raw_log_loss"] - row["calibrated_log_loss"])
    passed = {
        league: float(np.mean(values)) >= 0.0
        for league, values in sorted(grouped.items())}
    return {
        "eligible_leagues": len(passed),
        "non_degraded_leagues": sum(passed.values()),
        "non_degradation_rate": sum(passed.values()) / max(len(passed), 1),
        "by_league": passed,
    }


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Agrega métricas y aplica el gate congelado."""

    stability = _stability(rows)
    metrics = {
        "predictions": len(rows),
        "raw_log_loss": _mean(rows, "raw_log_loss"),
        "calibrated_log_loss": _mean(rows, "calibrated_log_loss"),
        "raw_brier": _mean(rows, "raw_brier"),
        "calibrated_brier": _mean(rows, "calibrated_brier"),
        "raw_ece": _ece(rows, "raw_probability"),
        "calibrated_ece": _ece(rows, "calibrated_probability"),
        "bootstrap_improvement_ci95": _bootstrap(rows),
        "stability": stability,
    }
    metrics["passed"] = _passed(metrics)
    return metrics


def _mean(rows: list[dict[str, Any]], key: str) -> float:
    """Calcula una media numérica."""

    return float(np.mean([row[key] for row in rows]))


def _passed(metrics: dict[str, Any]) -> bool:
    """Evalúa todos los gates de promoción del calibrador."""

    return bool(
        metrics["calibrated_log_loss"] < metrics["raw_log_loss"]
        and metrics["calibrated_brier"] <= metrics["raw_brier"]
        and metrics["calibrated_ece"] <= metrics["raw_ece"]
        and metrics["bootstrap_improvement_ci95"][0] > 0
        and metrics["stability"]["non_degradation_rate"] >= 0.70)


def _final_parameters(
    rows: list[dict[str, Any]], shrinkage: int,
) -> dict[str, Any]:
    """Sella el estimador conservador después de cerrar el gate."""

    return {
        "version": "btts_league_rate_v1", "market": "btts",
        "training_matches": len(rows), "prior_rate": PRIOR_RATE,
        "league_shrinkage": shrinkage,
        "model": "causal_league_btts_rate",
    }


def _write(name: str, payload: Any) -> None:
    """Escribe un artefacto JSON estable."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / name).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")


def _sha(path: Path) -> str:
    """Calcula el SHA-256 de un artefacto."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def run() -> dict[str, Any]:
    """Ejecuta Fase 106 y publica el calibrador sólo si pasa."""

    source = _read()
    shrinkage = _select_shrinkage(source)
    predictions = _prequential(source, shrinkage)
    metrics = _metrics(predictions)
    if not metrics["passed"]:
        raise RuntimeError("phase106_btts_calibration_gate_failed")
    calibrator = _final_parameters(source, shrinkage)
    repaired = _post_repair_summary(source, shrinkage)
    _write("config.json", {
        "warmup_matches": WARMUP, "bootstrap_samples": BOOTSTRAP,
        "fallback_markets": ["home_corners_second_half_over_2_5"]})
    _write("metrics.json", metrics)
    _write("calibrator.json", calibrator)
    _write("post_repair_1000.json", repaired)
    _write("predictions.json", predictions)
    _write("audit.json", {
        "classification": "ready_for_selective_integration",
        "strictly_prior_fit": True, "target_revealed_after_prediction": True,
        "router_modified": False, "markov_fallback_ready": True})
    _report(metrics, calibrator, repaired)
    _write("hashes.json", {
        path.name: _sha(path) for path in sorted(OUTPUT.glob("*"))
        if path.is_file() and path.name != "hashes.json"})
    return metrics


def _post_repair_summary(
    rows: list[dict[str, Any]], shrinkage: int,
) -> dict[str, Any]:
    """Recalcula los 1,000 partidos con las dos reparaciones exactas."""

    positives: dict[str, int] = defaultdict(int)
    totals: dict[str, int] = defaultdict(int)
    losses, briers, correct, per_match = [], [], [], []
    for row in rows:
        values = []
        for name, market in row["markets"].items():
            probability = _repaired_probability(
                name, market, row, positives, totals, shrinkage)
            actual = bool(market["actual"]) if name != "1x2" else None
            values.append(_repaired_score(name, market, probability, actual))
        outcome = bool(row["markets"]["btts"]["actual"])
        league = str(row["league_slug"])
        positives[league] += int(outcome)
        totals[league] += 1
        per_match.append(sum(value[2] for value in values))
        losses.extend(value[0] for value in values)
        briers.extend(value[1] for value in values)
        correct.extend(value[2] for value in values)
    return {
        "matches": len(rows), "decisions": len(correct),
        "accuracy": float(np.mean(correct)),
        "log_loss": float(np.mean(losses)),
        "brier": float(np.mean(briers)),
        "mean_correct_markets": float(np.mean(per_match)),
        "perfect_12_of_12": sum(value == 12 for value in per_match),
        "zero_0_of_12": sum(value == 0 for value in per_match),
    }


def _repaired_probability(
    name: str, market: dict[str, Any], row: dict[str, Any],
    positives: dict[str, int], totals: dict[str, int], shrinkage: int,
) -> float:
    """Selecciona la probabilidad reparada de una línea."""

    if name == "btts":
        league = str(row["league_slug"])
        return (positives[league] + shrinkage * PRIOR_RATE) / (
            totals[league] + shrinkage)
    if name == "home_corners_second_half_over_2_5":
        return float(market["baseline_probability"])
    return float(market.get("probability", market.get("confidence", 0.0)))


def _repaired_score(
    name: str, market: dict[str, Any], probability: float,
    actual: bool | None,
) -> tuple[float, float, bool]:
    """Puntúa una decisión reparada o conserva 1X2 multiclase."""

    if name == "1x2":
        return (
            float(market["log_loss"]), float(market["brier"]),
            bool(market["correct"]))
    outcome = bool(actual)
    return (
        _loss(probability, outcome),
        (probability - float(outcome)) ** 2,
        (probability >= 0.5) == outcome,
    )


def _report(
    metrics: dict[str, Any], calibrator: dict[str, Any],
    repaired: dict[str, Any],
) -> None:
    """Genera el reporte ejecutivo de Fase 106."""

    lines = [
        "# Fase 106 — reparación probabilística", "",
        "**Estado:** `ready_for_selective_integration`", "",
        f"- predicciones BTTS: `{metrics['predictions']}`",
        f"- log-loss raw/calibrado: `{metrics['raw_log_loss']:.6f}` / "
        f"`{metrics['calibrated_log_loss']:.6f}`",
        f"- Brier raw/calibrado: `{metrics['raw_brier']:.6f}` / "
        f"`{metrics['calibrated_brier']:.6f}`",
        f"- ECE raw/calibrado: `{metrics['raw_ece']:.6f}` / "
        f"`{metrics['calibrated_ece']:.6f}`",
        f"- IC95%: `{metrics['bootstrap_improvement_ci95']}`",
        f"- ligas no degradadas: "
        f"`{metrics['stability']['non_degradation_rate']:.2%}`",
        f"- shrinkage liga: `{calibrator['league_shrinkage']}`",
        f"- prior BTTS: `{calibrator['prior_rate']:.2f}`",
        "- fallback Markov: `home_corners_second_half_over_2_5`",
        f"- replay 1,000 accuracy: `{repaired['accuracy']:.2%}`",
        f"- replay 1,000 log-loss: `{repaired['log_loss']:.6f}`",
        f"- replay 1,000 Brier: `{repaired['brier']:.6f}`",
    ]
    (OUTPUT / "final_report.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run()
    LOGGER.info("Fase 106 passed=%s", result["passed"])
    assert result["predictions"] == 800
    assert result["passed"] is True

# Version: 1.0.0
# Created: 2026-07-29
