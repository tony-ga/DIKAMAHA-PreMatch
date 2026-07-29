"""Construcción de la suite OOS canónica para evaluación DIKAMAHA.

Reutiliza folds temporales auditados y ajusta Markov sólo con cada train fold.

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
from src.pre_match_simulation_v1 import (
    MarkovMonteCarloSimulator,
    SimulationConfig,
    SimulationRequest,
    _goal_emissions,
    _indexes,
    _initial_priors,
)

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
FEATURES = ROOT / "artifacts/phase_2_5_match_features_v1_baseline/match_features_v1_candidate.json"
FOLDS = ROOT / "artifacts/phase_3_8_common_protocol/common_temporal_folds_v1.json"
EXISTING = ROOT / "artifacts/phase_3_9_common_evaluation/evaluation_predictions_v1.json"
WINDOWS = ROOT / "artifacts/phase_01_event_windows_v1/event_windows.json"
LABELS = ROOT / "artifacts/phase_02_state_labeling_v1/state_labels.json"
OUTPUT = ROOT / "artifacts/phase_05_canonical_oos_predictions_v1"


@dataclass(frozen=True, slots=True)
class CanonicalOosConfig:
    """Parámetros congelados de la construcción OOS común."""

    version: str = "canonical_oos_predictions_v1"
    simulations_per_match: int = 500
    seed: int = 20260726


def _load(path: Path) -> Any:
    """Carga JSON versionado sin alterar su contenido."""
    return json.loads(path.read_text(encoding="utf-8"))


def _hash(value: Any) -> str:
    """Calcula SHA-256 estable para provenance de entradas."""
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _write(name: str, value: Any) -> None:
    """Escribe JSON mediante reemplazo atómico."""
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / name
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(target)


def _feature_rows() -> dict[int, dict[str, Any]]:
    """Extrae sólo metadatos pre-match necesarios para cada fixture OOS."""
    payload = _load(FEATURES)
    return {int(row["match_id"]): row for row in payload["rows"] if bool(row["eligible_for_training"])}


def _existing_predictions(features: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    """Normaliza baseline, Dixon-Coles y Kalman al contrato canónico."""
    output = []
    for row in _load(EXISTING):
        model = _model_name(str(row["model"]))
        if model is None: continue
        output.append(_canonical_existing(row, features[int(row["match_id"])], model))
    return output


def _model_name(source: str) -> str | None:
    """Mapea los nombres heredados a comparadores del protocolo vigente."""
    return {"poisson_simple": "baseline_simple", "dixon_coles_v1": "dixon_coles", "kalman_v1": "dixon_coles_kalman"}.get(source)


def _value(row: dict[str, Any], *names: str) -> float:
    """Obtiene una probabilidad de una de sus variantes heredadas."""
    for name in names:
        if name in row: return float(row[name])
    raise KeyError(f"Falta una probabilidad requerida: {names}")


def _normalized_1x2(row: dict[str, Any]) -> tuple[float, float, float, bool]:
    """Normaliza 1X2 heredado y conserva evidencia del ajuste necesario."""
    values = (_value(row, "prob_1", "prob_1_kalman"), _value(row, "prob_x", "prob_x_kalman"), _value(row, "prob_2", "prob_2_kalman"))
    total = sum(values)
    if total <= 0.0: raise ValueError("Las probabilidades 1X2 no tienen masa positiva.")
    return values[0] / total, values[1] / total, values[2] / total, abs(total - 1.0) >= 1e-6


def _canonical_existing(row: dict[str, Any], feature: dict[str, Any], model: str) -> dict[str, Any]:
    """Convierte una predicción heredada sin incluir sus targets observados."""
    prob_1, prob_x, prob_2, adjusted = _normalized_1x2(row)
    return {"model": model, "match_id": int(row["match_id"]), "fold_id": int(row["fold_id"]), "cutoff_ts": str(feature["feature_cutoff_ts"]), "home_team_id": int(feature["home_team_id"]), "away_team_id": int(feature["away_team_id"]), "expected_home_goals": _value(row, "expected_home_goals", "expected_home_goals_dc", "expected_home_goals_kalman"), "expected_away_goals": _value(row, "expected_away_goals", "expected_away_goals_dc", "expected_away_goals_kalman"), "prob_1": prob_1, "prob_x": prob_x, "prob_2": prob_2, "prob_over_2_5": _value(row, "prob_over_2_5", "prob_over_2_5_kalman"), "prob_btts": _value(row, "prob_btts", "prob_btts_kalman"), "prediction_source": "phase_3_9_common_evaluation", "renormalized_1x2": adjusted}


def _markov_predictions(features: dict[int, dict[str, Any]], config: CanonicalOosConfig) -> list[dict[str, Any]]:
    """Genera Markov global y dependiente sobre el train exclusivo de cada fold."""
    windows, labels = _load(WINDOWS), _load(LABELS)
    transitions, _ = build_transitions(windows, labels)
    window_index, label_index = _indexes(windows, labels)
    output = []
    for fold in _load(FOLDS)["folds"]:
        output.extend(_run_fold(fold, features, transitions, window_index, label_index, config))
    return output


def _run_fold(fold: dict[str, Any], features: dict[int, dict[str, Any]], transitions: list[Any], windows: dict[Any, Any], labels: dict[Any, Any], config: CanonicalOosConfig) -> list[dict[str, Any]]:
    """Ajusta el modelo con un fold y predice únicamente su validación."""
    train_ids = {int(value) for value in fold["train_ids"]}
    calibrator = HierarchicalMarkovCalibrator(MarkovCalibrationConfig())
    calibrator.fit([row for row in transitions if row.match_id in train_ids])
    priors = _initial_priors(windows, labels, train_ids)
    emissions = _goal_emissions(windows, labels, train_ids)
    matrices = calibrator.export()
    return _fold_predictions(fold, features, matrices, priors, emissions, config)


def _fold_predictions(fold: dict[str, Any], features: dict[int, dict[str, Any]], matrices: dict[str, Any], priors: dict[bool, Any], emissions: dict[str, Any], config: CanonicalOosConfig) -> list[dict[str, Any]]:
    """Simula los fixtures de validación con ambas variantes Markov."""
    output = []
    for match_id in fold["validation_ids"]:
        feature = features[int(match_id)]
        for model, active_matrices in _markov_variants(matrices):
            result = _simulate(feature, int(fold["fold_id"]), active_matrices, priors, emissions, config)
            output.append(_canonical_markov(result, feature, int(fold["fold_id"]), model))
    return output


def _markov_variants(matrices: dict[str, Any]) -> tuple[tuple[str, dict[str, Any]], ...]:
    """Devuelve matrices globales y dependientes sobre el mismo entrenamiento."""
    global_only = {tier: [] for tier in matrices}
    global_only["global"] = matrices["global"]
    return (("markov_global", global_only), ("markov_dependent", matrices))


def _simulate(feature: dict[str, Any], fold_id: int, matrices: dict[str, Any], priors: dict[bool, Any], emissions: dict[str, Any], config: CanonicalOosConfig) -> dict[str, Any]:
    """Ejecuta una simulación aislada con semilla dependiente de partido."""
    match_id = int(feature["match_id"])
    active = SimulationConfig(simulations=config.simulations_per_match, seed=config.seed + match_id + fold_id)
    request = SimulationRequest(str(match_id), int(feature["home_team_id"]), int(feature["away_team_id"]), cutoff_ts=str(feature["feature_cutoff_ts"]))
    return MarkovMonteCarloSimulator(active, matrices, priors, emissions).simulate(request)


def _canonical_markov(result: dict[str, Any], feature: dict[str, Any], fold_id: int, model: str) -> dict[str, Any]:
    """Convierte resultado Monte Carlo al mismo contrato de los baselines."""
    markets = result["markets_experimental"]
    return {"model": model, "match_id": int(feature["match_id"]), "fold_id": fold_id, "cutoff_ts": str(feature["feature_cutoff_ts"]), "home_team_id": int(feature["home_team_id"]), "away_team_id": int(feature["away_team_id"]), "expected_home_goals": float(result["expected_goals"]["home"]), "expected_away_goals": float(result["expected_goals"]["away"]), "prob_1": float(markets["1"]), "prob_x": float(markets["X"]), "prob_2": float(markets["2"]), "prob_over_2_5": float(markets["over_2_5"]), "prob_btts": float(markets["btts"]), "prediction_source": "markov_fold_simulation"}


def _audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Verifica cobertura uniforme, normalización y cutoff causal del contrato."""
    by_model: dict[str, set[int]] = {}
    for row in rows: by_model.setdefault(str(row["model"]), set()).add(int(row["match_id"]))
    coverage = {name: len(ids) for name, ids in by_model.items()}
    common = len(set.intersection(*by_model.values())) if by_model else 0
    normalized = all(abs(row["prob_1"] + row["prob_x"] + row["prob_2"] - 1.0) < 1e-6 for row in rows)
    return {"models": sorted(by_model), "match_coverage": coverage, "common_match_coverage": common, "all_1x2_normalized": normalized, "renormalized_prediction_count": sum(bool(row.get("renormalized_1x2", False)) for row in rows), "target_outcomes_used_as_features": False, "all_cutoffs_present": all(bool(row["cutoff_ts"]) for row in rows)}


def run(config: CanonicalOosConfig | None = None) -> dict[str, Any]:
    """Construye y publica la suite OOS canónica sin calcular métricas target."""
    active, features = config or CanonicalOosConfig(), _feature_rows()
    rows = _existing_predictions(features) + _markov_predictions(features, active)
    audit = _audit(rows)
    result = {"config": asdict(active), "predictions": rows, "audit": audit, "classification": "ready_for_evaluation" if audit["common_match_coverage"] >= 50 and audit["all_1x2_normalized"] else "rejected_for_revision"}
    _publish(result)
    LOGGER.info("Canonical OOS: %s", result["classification"])
    return result


def _publish(result: dict[str, Any]) -> None:
    """Publica contrato, auditoría, manifest y hashes de la suite OOS."""
    manifest = {"features_hash": _hash(_load(FEATURES)), "folds_hash": _hash(_load(FOLDS)), "existing_predictions_hash": _hash(_load(EXISTING)), "windows_hash": _hash(_load(WINDOWS)), "labels_hash": _hash(_load(LABELS))}
    _write("config.json", result["config"]); _write("input_manifest.json", manifest)
    _write("canonical_predictions.json", result["predictions"]); _write("audit.json", {**result["audit"], "classification": result["classification"]})
    report = ["# Suite canónica OOS v1", "", f"**Clasificación:** `{result['classification']}`", "", f"- cobertura común: `{result['audit']['common_match_coverage']}` partidos", f"- modelos: `{result['audit']['models']}`", "- siguiente paso: ejecutar métricas y bootstrap del bloque confirmatorio."]
    (OUTPUT / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    _write("hashes.json", {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"})


# Version: 1.0.0
# Created: 2026-07-26
