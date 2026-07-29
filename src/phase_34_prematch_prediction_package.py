"""Genera predicciones pre-match para candidatos preparados.

La fase reconstruye el Markov v2 congelado con el histórico de Fase 20 y
aplica el selector por target de Fase 21. No lee resultados del candidato.

Requirements:
    - numpy
    - SQLAlchemy==2.0.41
    - python-dotenv

Version: 1.0.0
Created: 2026-07-26
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

from src.markov_pre_match_v1 import build_transitions
from src.markov_v2_oos_predictions import _fit_fold
from src.phase_10_temporal_target_evaluation import DIAGNOSTIC_TARGETS, TARGETS
from src.phase_14_dynamic_markov_recalibration import Phase14Config, _dynamic_prior, _match_rows, _team_mapping
from src.phase_17_extended_markov_retraining import _normalize_team_ids
from src.phase_20_full_preconfirmation_retraining import _remove_canonical_duplicates
from src.pre_match_simulation_v1 import _indexes
from src.pre_match_simulation_v2 import StructuralSimulationConfig
from src.state_labeling_v1 import StateLabelingConfig, label_rows

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "artifacts/phase_31_prospective_cohort_gate/gate_result.json"
PREPARATION = ROOT / "artifacts/phase_32_prematch_candidate_preparation/preparation_result.json"
PHASE20 = ROOT / "artifacts/phase_20_full_preconfirmation_retraining"
PHASE21 = ROOT / "artifacts/phase_21_target_model_router"
OUTPUT = ROOT / "artifacts/phase_34_prematch_prediction_package"
ALL_TARGETS = TARGETS + DIAGNOSTIC_TARGETS
MARKET_KEYS = {"home_comeback_win": "home_comeback", "away_comeback_win": "away_comeback"}


def _load(path: Path) -> Any:
    """Carga un JSON local."""

    return json.loads(path.read_text(encoding="utf-8"))


def _hash(path: Path) -> str:
    """Calcula el hash SHA-256 de un artefacto."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, payload: Any) -> None:
    """Escribe un JSON con reemplazo atómico."""

    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temporary.replace(path)


def _prepared_candidates() -> list[dict[str, Any]]:
    """Alinea candidatos del gate con los preparados por Fase 32."""

    gate = _load(GATE); preparation = _load(PREPARATION)
    prepared_ids = {int(row["match_id"]) for row in preparation.get("prepared_candidates", [])}
    return [row for row in gate.get("candidate_matches", []) if int(row["match_id"]) in prepared_ids]


def _historical_inputs(mapping: dict[int, int]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], set[int]]:
    """Reconstruye exactamente el histórico de entrenamiento congelado."""

    cw, cl = _load(ROOT / "artifacts/phase_01_event_windows_v1/event_windows.json"), _load(ROOT / "artifacts/phase_02_state_labeling_v1/state_labels.json")
    w16 = _load(ROOT / "artifacts/phase_16_backfill_windows/event_windows.json")
    w19 = _load(ROOT / "artifacts/phase_19_current_season_windows/event_windows.json")
    w09 = _normalize_team_ids(_load(ROOT / "artifacts/phase_09_historical_target_revision/candidate_event_windows.json"), mapping)
    cw, cl, _, _ = _remove_canonical_duplicates(cw, cl, w09, [], mapping)
    windows = w16 + cw + w19 + w09
    labels = label_rows(w16, StateLabelingConfig()) + cl + label_rows(w19, StateLabelingConfig()) + label_rows(w09, StateLabelingConfig())
    history = _match_rows(w16, mapping) + _match_rows(cw, mapping) + _match_rows(w19, mapping) + _match_rows(w09, mapping)
    train_ids = {int(value) for value in _load(PHASE20 / "confirmation.json")["partition"]["train_ids"]}
    return windows, labels, history, train_ids


def _candidate_match(row: dict[str, Any], mapping: dict[int, int]) -> dict[str, Any]:
    """Normaliza la identidad del candidato sin copiar sus resultados."""

    return {"match_id": int(row["match_id"]), "match_date": str(row["kickoff_ts"]), "home_team_id": mapping.get(int(row["home_team_id"]), int(row["home_team_id"])), "away_team_id": mapping.get(int(row["away_team_id"]), int(row["away_team_id"]))}


def _prediction(result: dict[str, Any], prior: dict[str, Any], config: Phase14Config) -> dict[str, Any]:
    """Serializa mercados Monte Carlo sin targets ni pérdidas."""

    markets = result["markets_experimental"]; outcomes = result["prob_1x2"]
    row = {"match_id": int(prior["match_id"]), "cutoff_ts": prior["cutoff_ts"], "home_team_id": int(prior["home_team_id"]), "away_team_id": int(prior["away_team_id"]), "model": "markov_dependent_v2", "expected_home_goals": result["expected_goals"]["home"], "expected_away_goals": result["expected_goals"]["away"], "prob_1": outcomes["1"], "prob_x": outcomes["X"], "prob_2": outcomes["2"], "prob_over_2_5": markets["over_2_5"], "prob_btts": markets["btts"], "lambda_base_home": prior["lambda_base_home"], "lambda_base_away": prior["lambda_base_away"], "simulation_count": config.simulations_per_match}
    row.update({f"prob_{name}": markets[MARKET_KEYS.get(name, name)] for name in ALL_TARGETS})
    return row


def _route(row: dict[str, Any], selected: dict[str, str], baseline: dict[str, float]) -> dict[str, Any]:
    """Aplica selección congelada sin consultar outcomes del candidato."""

    output = dict(row)
    for name in ALL_TARGETS:
        model = selected[name]; probability = float(row[f"prob_{name}"]) if model.startswith("markov") else float(baseline[name])
        output[f"baseline_{name}"] = float(baseline[name]); output[f"selected_model_{name}"] = model; output[f"routed_probability_{name}"] = probability
    return output


def _publish(result: dict[str, Any]) -> None:
    """Publica predicciones, auditoría y provenance."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    payloads = {"config.json": result["config"], "input_manifest.json": result["input_manifest"], "coverage.json": result["coverage"], "predictions.json": result["predictions"], "audit.json": result["audit"], "package_result.json": result}
    for name, payload in payloads.items(): _write(OUTPUT / name, payload)
    lines = ["# Fase 34 — paquete de predicciones pre-match", "", f"**Clasificación:** `{result['classification']}`", "", f"- candidatos preparados: `{result['coverage']['prepared_candidates']}`", f"- predicciones: `{result['coverage']['predictions']}`", f"- simulaciones por partido: `{result['config']['simulations_per_match']}`", "- targets del candidato usados: `False`", "- pérdidas calculadas: `False`", "- router oficial modificado: `False`", "- mercados promovidos: `False`"]
    (OUTPUT / "final_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    _write(OUTPUT / "hashes.json", {path.name: _hash(path) for path in sorted(OUTPUT.iterdir()) if path.name != "hashes.json"})


def run() -> dict[str, Any]:
    """Genera el paquete sólo cuando Fase 32 entrega candidatos completos."""

    candidates = _prepared_candidates()
    if not candidates:
        result = {"classification": "waiting_for_independent_cohort", "config": {"version": "phase_34_prematch_prediction_package_v1", "simulations_per_match": 5000, "targets_forbidden": True}, "input_manifest": {"gate_hash": _hash(GATE), "preparation_hash": _hash(PREPARATION)}, "coverage": {"prepared_candidates": 0, "predictions": 0}, "predictions": [], "audit": {"target_outcomes_used_as_features": False, "target_outcomes_read": False, "losses_calculated": False, "router_modified": False, "markets_promoted": False}}
        _publish(result); return result
    mapping, database = _team_mapping(); windows, labels, history, train_ids = _historical_inputs(mapping); transitions, _ = build_transitions(windows, labels); window_index, label_index = _indexes(windows, labels); model_config = Phase14Config(**_load(PHASE20 / "config.json")); simulator = _fit_fold(train_ids, transitions, window_index, label_index); selected, baseline = _load(PHASE21 / "selected_models.json"), _load(PHASE20 / "confirmation.json")["baseline"]
    predictions = []
    for candidate in candidates:
        prior = _dynamic_prior(_candidate_match(candidate, mapping), history, model_config); simulator.config = StructuralSimulationConfig(simulations=model_config.simulations_per_match, seed=model_config.simulation_seed + int(candidate["match_id"])); predictions.append(_route(_prediction(simulator.simulate(prior), prior, model_config), selected, baseline))
    audit = {"database": database, "train_match_count": len(train_ids), "target_outcomes_used_as_features": False, "target_outcomes_read": False, "losses_calculated": False, "candidate_events_read": False, "cutoff_causal": True, "router_modified": False, "markets_promoted": False}
    result = {"classification": "prematch_predictions_ready", "config": {"version": "phase_34_prematch_prediction_package_v1", **asdict(model_config), "targets_forbidden": True}, "input_manifest": {"gate_hash": _hash(GATE), "preparation_hash": _hash(PREPARATION), "selected_models_hash": _hash(PHASE21 / "selected_models.json"), "phase20_confirmation_hash": _hash(PHASE20 / "confirmation.json")}, "coverage": {"prepared_candidates": len(candidates), "predictions": len(predictions), "train_matches": len(train_ids)}, "predictions": predictions, "audit": audit}
    _publish(result); LOGGER.info("Fase 34 paquete pre-match: %s", result["classification"]); return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()

# Version: 1.0.0
# Created: 2026-07-26
