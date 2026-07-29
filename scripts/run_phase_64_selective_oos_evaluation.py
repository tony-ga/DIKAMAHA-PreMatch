"""Evalúa la cohorte Markov congelada después de los partidos.

El archivo de ventanas observado se proporciona explícitamente después de los
kickoffs. Sin ese archivo el script no lee targets ni calcula pérdidas.

Requirements:
    - numpy

Version: 1.0.0
Created: 2026-07-28
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
PREDICTIONS = ROOT / "artifacts/phase_63_frozen_markov_candidate_v1/predictions.json"
COHORT = ROOT / "artifacts/phase_62_independent_cohort_lock_v1/cohort.json"
OUTPUT = ROOT / "artifacts/phase_64_selective_oos_evaluation_v1"
MINIMUM_MATCHES = 30
BOOTSTRAP_SAMPLES = 5000
LOGGER = logging.getLogger(__name__)


def _load(path: Path) -> Any:
    """Carga un artefacto JSON local."""

    return json.loads(path.read_text(encoding="utf-8"))


def _hash(path: Path) -> str:
    """Calcula SHA-256 de un archivo de entrada."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _time(value: str) -> datetime:
    """Normaliza timestamp ISO a UTC."""

    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def _targets(rows: list[dict[str, Any]], ids: set[int]) -> dict[int, bool]:
    """Deriva first_half_goal desde seis ventanas por equipo."""

    grouped: dict[int, list[dict[str, Any]]] = {match_id: [] for match_id in ids}
    for row in rows:
        match_id = int(row["match_id"])
        if match_id in grouped:
            grouped[match_id].append(row)
    output = {}
    for match_id, values in grouped.items():
        valid = len(values) == 12 and {int(row["window_index"]) for row in values} == set(range(6))
        if valid:
            output[match_id] = sum(float(row["goals"]) for row in values if int(row["window_index"]) < 3) > 0
    return output


def _loss(probability: float, actual: bool) -> tuple[float, float]:
    """Calcula log-loss y Brier para una probabilidad binaria."""

    value = min(max(float(probability), 1e-12), 1.0 - 1e-12)
    return -math.log(value if actual else 1.0 - value), (value - float(actual)) ** 2


def _bootstrap(values: list[float]) -> dict[str, Any]:
    """Calcula IC bootstrap agrupado por partido."""

    if not values:
        return {"mean": None, "ci_95": [None, None], "samples": 0, "strictly_positive": False}
    rng = np.random.default_rng(20260728)
    sample = rng.integers(0, len(values), size=(BOOTSTRAP_SAMPLES, len(values)))
    means = np.asarray(values, dtype=float)[sample].mean(axis=1)
    interval = np.quantile(means, [0.025, 0.975])
    return {"mean": float(np.mean(values)), "ci_95": [float(interval[0]), float(interval[1])], "samples": BOOTSTRAP_SAMPLES, "strictly_positive": bool(interval[0] > 0.0)}


def _score(predictions: list[dict[str, Any]], targets: dict[int, bool]) -> list[dict[str, Any]]:
    """Alinea candidato y baseline con el target post-match."""

    scored = []
    for prediction in predictions:
        match_id = int(prediction["fixture"]["match_id"])
        actual = targets[match_id]
        candidate = float(prediction["markov_residual"]["probability"])
        baseline = float(prediction["markov_residual"]["baseline_first_half_goal_no_state"])
        candidate_loss, candidate_brier = _loss(candidate, actual)
        baseline_loss, baseline_brier = _loss(baseline, actual)
        scored.append({"match_id": match_id, "actual_first_half_goal": actual, "markov_probability": candidate, "baseline_probability": baseline, "markov_log_loss": candidate_loss, "baseline_log_loss": baseline_loss, "markov_brier": candidate_brier, "baseline_brier": baseline_brier, "improvement_log_loss": baseline_loss - candidate_loss})
    return scored


def _publish(result: dict[str, Any]) -> None:
    """Publica resultado y reporte sin modificar snapshots ni router."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name, value in result.items():
        if name in {"classification", "observed_windows_path"}:
            continue
        (OUTPUT / f"{name}.json").write_text(json.dumps(value, indent=2, sort_keys=True, default=str), encoding="utf-8")
    report = ["# Fase 64 — evaluación OOS selectiva Markov", "", f"**Clasificación:** `{result['classification']}`", "", f"- predicciones: `{result['coverage']['predictions']}`", f"- targets completos: `{result['coverage']['targets']}`", f"- scoring ejecutado: `{result['coverage']['scoring_executed']}`", "- router oficial modificado: `False`", "- mercados promovidos: `False`"]
    (OUTPUT / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")


def run(observed_windows: Path | None = None) -> dict[str, Any]:
    """Evalúa sólo cuando existe una fuente post-match completa y explícita."""

    predictions = _load(PREDICTIONS)
    cohort = _load(COHORT)
    ids = {int(fixture["match_id"]) for fixture in cohort["fixtures"]}
    result = {"classification": "waiting_for_postmatch_targets", "config": {"market": "first_half_goal", "minimum_matches": MINIMUM_MATCHES, "bootstrap_samples": BOOTSTRAP_SAMPLES}, "coverage": {"predictions": len(predictions), "targets": 0, "scored_predictions": 0, "scoring_executed": False}, "metrics": {}, "scored_predictions": [], "audit": {"predictions_generated_before_targets": True, "target_outcomes_read": False, "target_outcomes_used_as_features": False, "losses_calculated": False, "official_router_modified": False, "markets_promoted": False, "cohort_hash": cohort["cohort_hash"], "prediction_hash": _hash(PREDICTIONS)}}
    if observed_windows is None or not observed_windows.exists():
        _publish(result)
        LOGGER.info("Fase 64 esperando ventanas post-match")
        return result
    rows = _load(observed_windows)
    targets = _targets(rows, ids)
    scored = _score(predictions, targets) if len(targets) == len(ids) else []
    deltas = [float(row["improvement_log_loss"]) for row in scored]
    complete = len(targets) == len(ids) and all(_time(fixture["kickoff_ts"]) < datetime.now(timezone.utc) for fixture in cohort["fixtures"])
    metrics = {"match_count": len(scored), "positive_count": sum(bool(row["actual_first_half_goal"]) for row in scored), "markov_log_loss": float(np.mean([row["markov_log_loss"] for row in scored])) if scored else None, "baseline_log_loss": float(np.mean([row["baseline_log_loss"] for row in scored])) if scored else None, "improvement_vs_baseline": _bootstrap(deltas)}
    classification = "selective_oos_evaluation_insufficient_support" if complete and len(scored) < MINIMUM_MATCHES else "selective_oos_evaluation_complete" if complete else "waiting_for_postmatch_targets"
    result.update({"classification": classification, "coverage": {"predictions": len(predictions), "targets": len(targets), "scored_predictions": len(scored), "scoring_executed": bool(scored)}, "metrics": metrics, "scored_predictions": scored, "audit": {**result["audit"], "target_outcomes_read": bool(scored), "losses_calculated": bool(scored), "observed_windows_hash": _hash(observed_windows) if observed_windows.exists() else None}})
    _publish(result)
    LOGGER.info("Fase 64 evaluación Markov: %s", classification)
    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--observed-windows", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    raise SystemExit(0 if run(args.observed_windows)["classification"].startswith(("waiting_", "selective_oos_evaluation_")) else 1)
