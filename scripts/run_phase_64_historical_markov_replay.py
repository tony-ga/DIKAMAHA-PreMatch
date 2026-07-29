"""Reproduce Markov walk-forward sobre partidos históricos recientes.

Cada objetivo se excluye antes de generar su predicción. El bloque es
diagnóstico, no confirmatorio, porque pertenece al periodo usado para auditar
la calibración de state_0.

Requirements:
    - joblib
    - numpy

Version: 1.0.0
Created: 2026-07-28
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

import joblib
import numpy as np

from scripts.run_phase_63_frozen_markov_candidate import (
    FEATURES,
    STATES,
    _entropy,
    _feature_row,
    _markov_probability,
    _probabilities,
    _state_rows,
    _transition_index,
    _time,
)
from scripts.run_phase_63_initial_state_calibration import _matrix, _records, _with_profiles

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = ROOT / "artifacts/phase_60_taxonomy_snapshot_candidate_v1/event_windows.json"
MODEL = ROOT / "artifacts/phase_63_initial_state_calibration_v1/state0_classifier.joblib"
TRANSITIONS = ROOT / "artifacts/phase_40_multileague_markov_calibration_v1/transition_matrices.json"
OUTPUT = ROOT / "artifacts/phase_64_historical_markov_replay_v1"
TARGET_COUNT = 30
LOGGER = logging.getLogger(__name__)


def _load(path: Path) -> Any:
    """Carga un JSON local."""

    return json.loads(path.read_text(encoding="utf-8"))


def _hash(path: Path) -> str:
    """Calcula SHA-256 de un artefacto."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _match_index(rows: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    """Agrupa ventanas por partido."""

    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[int(row["match_id"])].append(row)
    return grouped


def _development_cutoff(rows: list[dict[str, Any]]) -> str:
    """Obtiene el último kickoff usado para ajustar state_0."""

    dates = sorted({_time(row["match_date"]) for row in rows})
    return dates[int(len(dates) * 0.60) - 1].isoformat()


def _fixtures(rows: list[dict[str, Any]], cutoff: str) -> list[dict[str, Any]]:
    """Selecciona objetivos recientes posteriores al desarrollo."""

    grouped = _match_index(rows)
    candidates = []
    for match_id, values in grouped.items():
        if len(values) != 12 or {int(row["window_index"]) for row in values} != set(range(6)) or _time(values[0]["match_date"]) <= _time(cutoff):
            continue
        home = next((row for row in values if bool(row["is_home"])), None)
        away = next((row for row in values if not bool(row["is_home"])), None)
        if home and away:
            candidates.append({"match_id": match_id, "league_slug": str(home["league_slug"]), "kickoff_ts": str(home["match_date"]), "home_team_id": int(home["team_id"]), "away_team_id": int(away["team_id"]), "provider_status": "post"})
    return sorted(candidates, key=lambda row: (_time(row["kickoff_ts"]), int(row["match_id"]))) [-TARGET_COUNT:]


def _actual(rows: list[dict[str, Any]]) -> bool:
    """Deriva first_half_goal después de congelar la predicción."""

    return sum(float(row["goals"]) for row in rows if int(row["window_index"]) < 3) > 0


def _loss(probability: float, actual: bool) -> tuple[float, float]:
    """Calcula log-loss y Brier binario."""

    value = min(max(float(probability), 1e-12), 1.0 - 1e-12)
    return -math.log(value if actual else 1.0 - value), (value - float(actual)) ** 2


def _bootstrap(values: list[float]) -> dict[str, Any]:
    """Calcula intervalo bootstrap agrupado por partido."""

    rng = np.random.default_rng(20260728)
    indices = rng.integers(0, len(values), size=(5000, len(values)))
    means = np.asarray(values, dtype=float)[indices].mean(axis=1)
    interval = np.quantile(means, [0.025, 0.975])
    return {"mean": float(np.mean(values)), "ci_95": [float(interval[0]), float(interval[1])], "strictly_positive": bool(interval[0] > 0.0), "samples": len(means)}


def _blend_probability(row: dict[str, Any], alpha: float) -> float:
    """Combina Markov con baseline sin cambiar la masa temporal."""

    baseline = float(row["baseline_probability"])
    markov = float(row["markov_probability"])
    return (1.0 - alpha) * baseline + alpha * markov


def _select_alpha(rows: list[dict[str, Any]]) -> tuple[float, list[dict[str, float]]]:
    """Selecciona shrinkage sólo en el bloque de validación."""

    candidates = (0.0, 0.1, 0.2, 0.3, 0.5, 0.7, 1.0)
    grid = []
    for alpha in candidates:
        losses = [_loss(_blend_probability(row, alpha), bool(row["actual_first_half_goal"]))[0] for row in rows]
        grid.append({"alpha": alpha, "log_loss": float(np.mean(losses))})
    selected = min(grid, key=lambda item: (item["log_loss"], item["alpha"]))
    return float(selected["alpha"]), grid


def _fusion_metrics(rows: list[dict[str, Any]], alpha: float) -> dict[str, Any]:
    """Resume la fusión en un bloque no usado para seleccionar alpha."""

    losses, briers, deltas = [], [], []
    for row in rows:
        actual = bool(row["actual_first_half_goal"])
        loss, brier = _loss(_blend_probability(row, alpha), actual)
        losses.append(loss)
        briers.append(brier)
        deltas.append(float(row["baseline_log_loss"]) - loss)
    return {"match_count": len(rows), "alpha": alpha, "log_loss": float(np.mean(losses)), "brier": float(np.mean(briers)), "improvement_vs_baseline": _bootstrap(deltas)}


def _predict(fixture: dict[str, Any], rows: list[dict[str, Any]], model: Any, transitions: dict[str, dict[tuple[Any, ...], dict[str, Any]]]) -> dict[str, Any]:
    """Genera una predicción usando sólo el estado histórico disponible."""

    cutoff = _time(fixture["kickoff_ts"])
    records = _with_profiles(_records(rows))
    home = _probabilities(model, _feature_row(int(fixture["home_team_id"]), True, cutoff, records))
    away = _probabilities(model, _feature_row(int(fixture["away_team_id"]), False, cutoff, records))
    markov, baseline, support = _markov_probability(fixture, (home, away), rows, transitions, cutoff)
    return {"fixture": fixture, "markov_probability": markov, "baseline_probability": 1.0 - math.exp(-baseline), "baseline_rate": baseline, "state_0_home": home, "state_0_away": away, "entropy_home": _entropy(home), "entropy_away": _entropy(away), "transition_states": support, "prediction_cutoff": fixture["kickoff_ts"], "target_used_before_prediction": False}


def _run_replay(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ejecuta predicción y luego incorpora el target al historial."""

    cutoff = _development_cutoff(raw)
    fixtures = _fixtures(raw, cutoff)
    grouped = _match_index(raw)
    target_ids = {int(item["match_id"]) for item in fixtures}
    historical = _state_rows([row for row in raw if int(row["match_id"]) not in target_ids and _time(row["match_date"]) < _time(fixtures[0]["kickoff_ts"])])
    model = joblib.load(MODEL)
    transitions = _transition_index(_load(TRANSITIONS))
    output = []
    for fixture in fixtures:
        prediction = _predict(fixture, historical, model, transitions)
        actual = _actual(grouped[int(fixture["match_id"])])
        markov_loss, markov_brier = _loss(prediction["markov_probability"], actual)
        baseline_loss, baseline_brier = _loss(prediction["baseline_probability"], actual)
        output.append({**prediction, "actual_first_half_goal": actual, "markov_log_loss": markov_loss, "baseline_log_loss": baseline_loss, "markov_brier": markov_brier, "baseline_brier": baseline_brier, "improvement_log_loss": baseline_loss - markov_loss})
        historical.extend(_state_rows(grouped[int(fixture["match_id"])]))
    return output


def run() -> dict[str, Any]:
    """Publica replay diagnóstico sin habilitar promoción."""

    raw = _load(WINDOWS)
    rows = _run_replay(raw)
    improvements = [float(row["improvement_log_loss"]) for row in rows]
    split = max(1, len(rows) // 2)
    alpha, alpha_grid = _select_alpha(rows[:split])
    holdout = _fusion_metrics(rows[split:], alpha)
    for row in rows:
        row["fusion_probability"] = _blend_probability(row, alpha)
    metrics = {"match_count": len(rows), "positive_count": sum(bool(row["actual_first_half_goal"]) for row in rows), "markov_log_loss": float(np.mean([row["markov_log_loss"] for row in rows])), "baseline_log_loss": float(np.mean([row["baseline_log_loss"] for row in rows])), "markov_brier": float(np.mean([row["markov_brier"] for row in rows])), "baseline_brier": float(np.mean([row["baseline_brier"] for row in rows])), "improvement_vs_baseline": _bootstrap(improvements), "fusion_validation": {"match_count": split, "alpha_grid": alpha_grid, "selected_alpha": alpha}, "fusion_holdout": holdout}
    audit = {"classification": "historical_replay_diagnostic_only", "prediction_count": len(rows), "target_used_before_prediction": False, "walk_forward_order": True, "fusion_validation_before_holdout": True, "selection_period_overlap": True, "independent_confirmation": False, "official_router_modified": False, "markov_promoted": False, "windows_hash": _hash(WINDOWS), "model_hash": _hash(MODEL), "transition_hash": _hash(TRANSITIONS)}
    result = {"config": {"market": "first_half_goal", "target_count": TARGET_COUNT, "selection_period_overlap": True}, "metrics": metrics, "predictions": rows, "audit": audit}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name, value in result.items():
        (OUTPUT / f"{name}.json").write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    report = ["# Fase 64 — replay histórico Markov", "", f"**Clasificación:** `{audit['classification']}`", "", f"- partidos: `{len(rows)}`", f"- log-loss Markov: `{metrics['markov_log_loss']}`", f"- log-loss baseline: `{metrics['baseline_log_loss']}`", f"- mejora media: `{metrics['improvement_vs_baseline']['mean']}`", f"- IC bootstrap: `{metrics['improvement_vs_baseline']['ci_95']}`", f"- alpha seleccionado en validación: `{alpha}`", f"- log-loss fusión en holdout: `{holdout['log_loss']}`", f"- mejora fusión en holdout: `{holdout['improvement_vs_baseline']['mean']}`", "- predicción antes del target: `True`", "- evaluación confirmatoria independiente: `False`", "- router modificado: `False`"]
    (OUTPUT / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    LOGGER.info("Replay histórico Markov: %s", audit["classification"])
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    raise SystemExit(0 if run()["audit"]["classification"] == "historical_replay_diagnostic_only" else 1)
