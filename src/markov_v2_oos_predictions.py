"""Predicciones OOS de Markov v2 sobre folds temporales comunes.

Version: 1.0.0
Created: 2026-07-26
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from src.markov_pre_match_v1 import HierarchicalMarkovCalibrator, MarkovCalibrationConfig, build_transitions
from src.pre_match_simulation_v1 import _goal_emissions, _indexes, _initial_priors, _initial_priors_by_team
from src.pre_match_simulation_v2 import ConservingMarkovSimulator, StructuralSimulationConfig

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
WINDOWS = ROOT / "artifacts/phase_01_event_windows_v1/event_windows.json"
LABELS = ROOT / "artifacts/phase_02_state_labeling_v1/state_labels.json"
PRIORS = ROOT / "artifacts/phase_06_markov_v2_goal_prior/goal_priors.json"
FOLDS = ROOT / "artifacts/phase_3_8_common_protocol/common_temporal_folds_v1.json"
OUTPUT = ROOT / "artifacts/phase_06_markov_v2_oos_predictions"


@dataclass(frozen=True, slots=True)
class MarkovV2OosConfig:
    """Configuración congelada de predicción OOS Markov v2."""

    version: str = "markov_v2_oos_predictions"
    simulations_per_match: int = 500
    seed: int = 20260726


def _load(path: Path) -> Any:
    """Carga un artefacto JSON versionado sin alterarlo."""
    return json.loads(path.read_text(encoding="utf-8"))


def _write(name: str, value: Any) -> None:
    """Escribe un artefacto JSON mediante reemplazo atómico."""
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / name
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(target)


def _prior_index() -> dict[int, dict[str, Any]]:
    """Indexa priors estructurales OOS por identidad de partido."""
    return {int(row["match_id"]): row for row in _load(PRIORS)}


def _fit_fold(train_ids: set[int], transitions: list[Any], windows: dict[Any, Any], labels: dict[Any, Any]) -> ConservingMarkovSimulator:
    """Ajusta Markov y emisiones usando exclusivamente el train fold."""
    calibrator = HierarchicalMarkovCalibrator(MarkovCalibrationConfig())
    calibrator.fit([row for row in transitions if row.match_id in train_ids])
    config = StructuralSimulationConfig(simulations=1)
    return ConservingMarkovSimulator(config, calibrator.export(), _initial_priors(windows, labels, train_ids), _goal_emissions(windows, labels, train_ids), _initial_priors_by_team(windows, labels, train_ids))


def _predict_fold(fold: dict[str, Any], priors: dict[int, dict[str, Any]], transitions: list[Any], windows: dict[Any, Any], labels: dict[Any, Any], config: MarkovV2OosConfig) -> list[dict[str, Any]]:
    """Predice todos los partidos de validación con el modelo ajustado al pasado."""
    train_ids = {int(value) for value in fold["train_ids"]}
    simulator = _fit_fold(train_ids, transitions, windows, labels)
    output = []
    for match_id in fold["validation_ids"]:
        prior = priors[int(match_id)]
        simulator.config = StructuralSimulationConfig(simulations=config.simulations_per_match, seed=config.seed + int(match_id) + int(fold["fold_id"]))
        result = simulator.simulate(prior)
        output.append(_canonical(result, prior, int(fold["fold_id"])))
    return output


def _canonical(result: dict[str, Any], prior: dict[str, Any], fold_id: int) -> dict[str, Any]:
    """Serializa una simulación v2 en el contrato OOS de mercados."""
    probs = result["prob_1x2"]
    markets = result["markets_experimental"]
    return {"model": "markov_dependent_v2", "match_id": int(prior["match_id"]), "fold_id": fold_id, "cutoff_ts": prior["cutoff_ts"], "home_team_id": int(prior["home_team_id"]), "away_team_id": int(prior["away_team_id"]), "expected_home_goals": result["expected_goals"]["home"], "expected_away_goals": result["expected_goals"]["away"], "prob_1": probs["1"], "prob_x": probs["X"], "prob_2": probs["2"], "prob_over_2_5": markets["over_2_5"], "prob_btts": markets["btts"], "prob_first_half_goal": markets["first_half_goal"], "prob_second_half_goal": markets["second_half_goal"], "prob_home_comeback": markets["home_comeback"], "prob_away_comeback": markets["away_comeback"], "lambda_base_home": prior["lambda_base_home"], "lambda_base_away": prior["lambda_base_away"], "prediction_source": "markov_v2_fold_simulation"}


def _audit(rows: list[dict[str, Any]], folds: list[dict[str, Any]]) -> dict[str, Any]:
    """Verifica cobertura, normalización y ausencia de targets en predicción."""
    expected = {int(match_id) for fold in folds for match_id in fold["validation_ids"]}
    observed = {int(row["match_id"]) for row in rows}
    normalized = all(abs(float(row["prob_1"]) + float(row["prob_x"]) + float(row["prob_2"]) - 1.0) < 1e-12 for row in rows)
    return {"expected_match_count": len(expected), "predicted_match_count": len(observed), "coverage_complete": observed == expected, "all_1x2_normalized": normalized, "target_outcomes_used_as_features": False, "per_fold_counts": {str(fold["fold_id"]): sum(int(row["fold_id"]) == int(fold["fold_id"]) for row in rows) for fold in folds}}


def run(config: MarkovV2OosConfig | None = None) -> dict[str, Any]:
    """Genera las 264 predicciones OOS v2 sin evaluar ni promover mercados."""
    active, windows, labels, folds = config or MarkovV2OosConfig(), _load(WINDOWS), _load(LABELS), _load(FOLDS)["folds"]
    transitions, _ = build_transitions(windows, labels)
    window_index, label_index, priors = *_indexes(windows, labels), _prior_index()
    rows = [row for fold in folds for row in _predict_fold(fold, priors, transitions, window_index, label_index, active)]
    audit = _audit(rows, folds)
    result = {"config": asdict(active), "predictions": rows, "audit": audit, "classification": "ready_for_evaluation" if audit["coverage_complete"] and audit["all_1x2_normalized"] else "rejected_for_revision"}
    _publish(result)
    LOGGER.info("Markov v2 OOS: %s", result["classification"])
    return result


def _publish(result: dict[str, Any]) -> None:
    """Publica predicciones, auditoría, provenance y hashes reproducibles."""
    manifest = {"windows_hash": hashlib.sha256(WINDOWS.read_bytes()).hexdigest(), "labels_hash": hashlib.sha256(LABELS.read_bytes()).hexdigest(), "priors_hash": hashlib.sha256(PRIORS.read_bytes()).hexdigest(), "folds_hash": hashlib.sha256(FOLDS.read_bytes()).hexdigest()}
    _write("config.json", result["config"]); _write("input_manifest.json", manifest)
    _write("predictions.json", result["predictions"]); _write("audit.json", {**result["audit"], "classification": result["classification"]})
    report = ["# Markov v2 — predicciones OOS", "", f"**Clasificación:** `{result['classification']}`", "", f"- partidos predichos: `{result['audit']['predicted_match_count']}`", "- sin targets del partido como features.", "- siguiente paso: evaluación y bootstrap independientes."]
    (OUTPUT / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    _write("hashes.json", {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"})


# Version: 1.0.0
# Created: 2026-07-26
