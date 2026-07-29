"""Evaluación OOS de targets temporales v2 sobre extensión histórica.

La confirmación usa partidos de `prospective_staging_v2` y un Markov ajustado
únicamente con el histórico canónico. La fase es experimental y no promueve
mercados.

Requirements:
    - numpy

Version: 1.0.0
Created: 2026-07-26
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import random
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from src.markov_pre_match_v1 import build_transitions
from src.markov_v2_oos_predictions import _fit_fold
from src.pre_match_simulation_v2 import StructuralSimulationConfig
from src.pre_match_simulation_v1 import _indexes

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
CANONICAL_WINDOWS = ROOT / "artifacts/phase_01_event_windows_v1/event_windows.json"
CANONICAL_LABELS = ROOT / "artifacts/phase_02_state_labeling_v1/state_labels.json"
PHASE09 = ROOT / "artifacts/phase_09_historical_target_revision"
LAMBDA_INPUT = ROOT / "artifacts/phase_7_16_prospective_evaluation/prospective_lambda_base_input.json"
SPEC = ROOT / "docs/specs/temporal_targets_v2.md"
OUTPUT = ROOT / "artifacts/phase_10_temporal_target_evaluation"
TARGETS = (
    "first_half_goal", "second_half_goal", "home_recovery_draw_or_win",
    "away_recovery_draw_or_win", "home_reaches_level_after_half",
    "away_reaches_level_after_half",
)
DIAGNOSTIC_TARGETS = ("home_comeback_win", "away_comeback_win")


@dataclass(frozen=True, slots=True)
class Phase10Config:
    """Criterios congelados para la evaluación temporal v2."""

    version: str = "temporal_target_evaluation_v2"
    simulations_per_match: int = 5000
    simulation_seed: int = 20260726
    bootstrap_samples: int = 5000
    bootstrap_seed: int = 20260726
    minimum_confirmation_matches: int = 30
    minimum_positive_events: int = 20
    minimum_opportunities: int = 30


def _load(path: Path) -> Any:
    """Carga JSON versionado sin modificar su origen."""

    return json.loads(path.read_text(encoding="utf-8"))


def _hash_file(path: Path) -> str:
    """Calcula SHA-256 de un artefacto de entrada."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash(value: Any) -> str:
    """Calcula hash estable de una estructura serializable."""

    raw = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _inputs() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[int, dict[str, Any]]]:
    """Carga ventanas, labels, priors y labels v2 de la cohorte."""

    windows = _load(CANONICAL_WINDOWS)
    labels = _load(CANONICAL_LABELS)
    targets = _load(PHASE09 / "target_labels.json")
    priors = {int(row["match_id"]): row for row in _load(LAMBDA_INPUT)["rows"]}
    return windows, labels, targets, priors


def _partition(targets: list[dict[str, Any]]) -> dict[str, Any]:
    """Construye train canónico y confirmación staging sin solapamiento."""

    train = sorted(int(row["match_id"]) for row in targets if row["cohort"] == "canonical_v1")
    confirmation = sorted(int(row["match_id"]) for row in targets if row["cohort"] == "staging_extension_candidate")
    return {"train_ids": train, "validation_ids": [], "confirmation_ids": confirmation, "train_count": len(train), "validation_count": 0, "confirmation_count": len(confirmation), "order": "match_date_asc_match_id_asc", "overlap": sorted(set(train).intersection(confirmation))}


def _extension_context(windows: list[dict[str, Any]], targets: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Obtiene identidad de equipos y kickoff de la extensión candidata."""

    ids = {int(row["match_id"]) for row in targets if row["cohort"] == "staging_extension_candidate"}
    context: dict[int, dict[str, Any]] = {}
    for row in windows:
        match_id = int(row["match_id"])
        if match_id not in ids or int(row["window_index"]) != 0:
            continue
        context[match_id] = {"home_team_id": int(row["team_id"]) if bool(row["is_home"]) else int(row["opponent_team_id"]), "away_team_id": int(row["opponent_team_id"]) if bool(row["is_home"]) else int(row["team_id"]), "cutoff_ts": str(row["match_date"])}
    return context


def _priors(prior_rows: dict[int, dict[str, Any]], context: dict[int, dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Combina priors pre-kickoff con identidad de la cohorte nueva."""

    output = {}
    for match_id, prior in prior_rows.items():
        if match_id not in context:
            continue
        output[match_id] = {"match_id": match_id, **context[match_id], "cutoff_ts": str(prior.get("kickoff_ts") or context[match_id]["cutoff_ts"]), "lambda_base_home": float(prior["lambda_base_home"]), "lambda_base_away": float(prior["lambda_base_away"])}
    return output


def _predict(
    windows: list[dict[str, Any]], labels: list[dict[str, Any]],
    priors: dict[int, dict[str, Any]], partition: dict[str, Any], config: Phase10Config,
) -> list[dict[str, Any]]:
    """Ajusta Markov con train canónico y simula sólo la confirmación."""

    transitions, _ = build_transitions(windows, labels)
    window_index, label_index = _indexes(windows, labels)
    simulator = _fit_fold(set(partition["train_ids"]), transitions, window_index, label_index)
    output = []
    for match_id in partition["confirmation_ids"]:
        simulator.config = StructuralSimulationConfig(simulations=config.simulations_per_match, seed=config.simulation_seed + match_id)
        result = simulator.simulate(priors[match_id])
        markets = result["markets_experimental"]
        output.append({"match_id": match_id, "cutoff_ts": priors[match_id]["cutoff_ts"], "model": "markov_dependent_v2", "prob_first_half_goal": markets["first_half_goal"], "prob_second_half_goal": markets["second_half_goal"], "prob_home_recovery_draw_or_win": markets["home_recovery_draw_or_win"], "prob_away_recovery_draw_or_win": markets["away_recovery_draw_or_win"], "prob_home_reaches_level_after_half": markets["home_reaches_level_after_half"], "prob_away_reaches_level_after_half": markets["away_reaches_level_after_half"], "prob_home_comeback_win": markets["home_comeback"], "prob_away_comeback_win": markets["away_comeback"], "lambda_base_home": priors[match_id]["lambda_base_home"], "lambda_base_away": priors[match_id]["lambda_base_away"], "simulation_count": config.simulations_per_match})
    return output


def _target_index(targets: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Indexa labels v2 por partido."""

    return {int(row["match_id"]): row for row in targets}


def _baseline(targets: list[dict[str, Any]], train_ids: list[int]) -> dict[str, float]:
    """Estima probabilidades base sólo con el bloque train."""

    index = _target_index(targets)
    train = [index[match_id] for match_id in train_ids]
    return {target: sum(bool(row[target]) for row in train) / len(train) for target in TARGETS + DIAGNOSTIC_TARGETS}


def _loss(probability: float, actual: bool) -> float:
    """Calcula log-loss binario con clipping finito."""

    bounded = min(max(float(probability), 1e-12), 1.0 - 1e-12)
    return -math.log(bounded if actual else 1.0 - bounded)


def _score(predictions: list[dict[str, Any]], targets: list[dict[str, Any]], baseline: dict[str, float]) -> list[dict[str, Any]]:
    """Alinea predicción, target y baseline por partido completo."""

    index = _target_index(targets)
    rows = []
    for prediction in predictions:
        target_row = index[int(prediction["match_id"])]
        scored = {**prediction}
        for target in TARGETS + DIAGNOSTIC_TARGETS:
            probability_key = f"prob_{target}"
            actual = bool(target_row[target])
            probability = float(prediction[probability_key])
            scored[f"target_{target}"] = actual
            scored[f"baseline_{target}"] = baseline[target]
            scored[f"loss_{target}"] = _loss(probability, actual)
            scored[f"baseline_loss_{target}"] = _loss(baseline[target], actual)
        rows.append(scored)
    return rows


def _bootstrap(values: list[float], config: Phase10Config) -> dict[str, Any]:
    """Calcula intervalo bootstrap de mejora agrupado por partido."""

    if not values:
        return {"match_count": 0, "mean_improvement": None, "ci_95": [None, None], "improvement_confirmed": False}
    rng = random.Random(config.bootstrap_seed)
    samples = [sum(rng.choice(values) for _ in values) / len(values) for _ in range(config.bootstrap_samples)]
    samples.sort()
    lower = samples[int(config.bootstrap_samples * 0.025)]
    upper = samples[int(config.bootstrap_samples * 0.975) - 1]
    return {"match_count": len(values), "mean_improvement": sum(values) / len(values), "ci_95": [lower, upper], "improvement_confirmed": lower > 0.0}


def _support(rows: list[dict[str, Any]], target: str, config: Phase10Config) -> dict[str, Any]:
    """Mide positivos y oportunidades antes de interpretar un target."""

    positives = sum(bool(row[f"target_{target}"]) for row in rows)
    opportunity_key = {"home_recovery_draw_or_win": "home_trailing_at_half", "away_recovery_draw_or_win": "away_trailing_at_half", "home_reaches_level_after_half": "home_trailing_at_half", "away_reaches_level_after_half": "away_trailing_at_half"}.get(target)
    targets = _target_index(_load(PHASE09 / "target_labels.json"))
    opportunities = sum(bool(targets[int(row["match_id"])][opportunity_key]) for row in rows) if opportunity_key else len(rows)
    return {"positive_events": positives, "opportunities": opportunities, "minimum_positive_events": config.minimum_positive_events, "minimum_opportunities": config.minimum_opportunities, "support_sufficient": positives >= config.minimum_positive_events and opportunities >= config.minimum_opportunities}


def _metrics(rows: list[dict[str, Any]], baseline: dict[str, float], config: Phase10Config) -> dict[str, Any]:
    """Resume pérdidas, prevalencia y gate de soporte de confirmación."""

    output = {}
    for target in TARGETS + DIAGNOSTIC_TARGETS:
        values = [row for row in rows]
        output[target] = {"match_count": len(values), "actual_rate": sum(bool(row[f"target_{target}"]) for row in values) / len(values) if values else None, "model_probability_mean": sum(float(row[f"prob_{target}"]) for row in values) / len(values) if values else None, "model_log_loss": sum(float(row[f"loss_{target}"]) for row in values) / len(values) if values else None, "baseline_probability": baseline[target], "baseline_log_loss": sum(float(row[f"baseline_loss_{target}"]) for row in values) / len(values) if values else None, "support": _support(values, target, config)}
    return output


def _audit(partition: dict[str, Any], priors: dict[int, dict[str, Any]], predictions: list[dict[str, Any]], metrics: dict[str, Any], config: Phase10Config) -> dict[str, Any]:
    """Verifica separación temporal, cobertura, leakage y soporte."""

    return {"partition": partition, "train_confirmation_overlap": partition["overlap"], "prior_coverage_complete": set(priors) == set(partition["confirmation_ids"]), "prediction_coverage_complete": {int(row["match_id"]) for row in predictions} == set(partition["confirmation_ids"]), "confirmation_match_count_sufficient": partition["confirmation_count"] >= config.minimum_confirmation_matches, "target_outcomes_used_as_features": False, "model_fit_match_ids": partition["train_ids"], "prediction_semantics": "unconditional_pre_match_event_probability", "conditional_opportunity_rates_descriptive_only": True, "bootstrap_unit": "complete_match", "support_by_target": {target: metrics[target]["support"] for target in TARGETS + DIAGNOSTIC_TARGETS}, "promotion_allowed": False}


def _classification(metrics: dict[str, Any], audit: dict[str, Any], bootstrap: dict[str, Any]) -> str:
    """Clasifica sin promover si hay soporte insuficiente o CIs cruzadas."""

    supported = [target for target, value in metrics.items() if value["support"]["support_sufficient"]]
    confirmed = [target for target in supported if bootstrap[target]["improvement_confirmed"]]
    if not audit["prior_coverage_complete"] or not audit["prediction_coverage_complete"] or audit["train_confirmation_overlap"]:
        return "rejected_for_revision"
    return "promising_unconfirmed" if confirmed else "rejected_for_revision"


def _bootstrap_results(rows: list[dict[str, Any]], config: Phase10Config) -> dict[str, Any]:
    """Calcula mejoras Markov contra baseline por target."""

    return {target: _bootstrap([row[f"baseline_loss_{target}"] - row[f"loss_{target}"] for row in rows], config) for target in TARGETS + DIAGNOSTIC_TARGETS}


def _reports(classification: str, partition: dict[str, Any], metrics: dict[str, Any], bootstrap: dict[str, Any]) -> tuple[str, str]:
    """Construye reportes de validación y cierre de fase."""

    supported = [target for target in TARGETS if metrics[target]["support"]["support_sufficient"]]
    validation = "\n".join(["# Validation report — Fase 10", "", f"- entrenamiento: `{partition['train_count']}` partidos", f"- confirmación temporal: `{partition['confirmation_count']}` partidos", f"- targets con soporte suficiente: `{supported}`", "- bootstrap agrupado por partido completo.", "- la confirmación no habilita mercados automáticamente."])
    lines = ["# Fase 10 — evaluación temporal targets v2", "", f"**Clasificación:** `{classification}`", "", f"- train: `{partition['train_count']}` partidos", f"- confirmation: `{partition['confirmation_count']}` partidos", f"- targets con soporte suficiente: `{supported}`"]
    for target in TARGETS:
        item = metrics[target]; ci = bootstrap[target]["ci_95"]
        lines.append(f"- `{target}`: log-loss Markov `{item['model_log_loss']:.6f}`, baseline `{item['baseline_log_loss']:.6f}`, IC mejora `{ci}`")
    lines.extend(["", "Mercados promovidos: `False`.", "Siguiente paso: ampliar la cohorte confirmatoria o revisar targets con soporte insuficiente antes de reabrir promoción."])
    return validation, "\n".join(lines)


def _publish(result: dict[str, Any]) -> None:
    """Publica artefactos contractuales y hashes."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    payloads = {key: result[key] for key in ("config", "input_manifest", "partition", "coverage", "metrics", "bootstrap_results", "metrics_by_match", "audit")}
    for name, value in payloads.items():
        (OUTPUT / f"{name}.json").write_text(json.dumps(value, indent=2, sort_keys=True, default=str), encoding="utf-8")
    (OUTPUT / "validation_report.md").write_text(result["validation_report"] + "\n", encoding="utf-8")
    (OUTPUT / "final_report.md").write_text(result["final_report"] + "\n", encoding="utf-8")
    hashes = {path.name: _hash_file(path) for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"}
    (OUTPUT / "hashes.json").write_text(json.dumps(hashes, indent=2, sort_keys=True), encoding="utf-8")


def run(config: Phase10Config | None = None) -> dict[str, Any]:
    """Ejecuta partición, predicción, métricas y bootstrap OOS."""

    active = config or Phase10Config()
    windows, labels, targets, prior_rows = _inputs()
    partition = _partition(targets)
    context = _extension_context(_load(PHASE09 / "candidate_event_windows.json"), targets)
    priors = _priors(prior_rows, context)
    predictions = _predict(windows, labels, priors, partition, active)
    baseline = _baseline(targets, partition["train_ids"])
    scored = _score(predictions, targets, baseline)
    metrics = _metrics(scored, baseline, active)
    bootstrap = _bootstrap_results(scored, active)
    audit = _audit(partition, priors, predictions, metrics, active)
    classification = _classification(metrics, audit, bootstrap)
    validation, final = _reports(classification, partition, metrics, bootstrap)
    manifest = {"canonical_windows_hash": _hash_file(CANONICAL_WINDOWS), "canonical_labels_hash": _hash_file(CANONICAL_LABELS), "phase09_targets_hash": _hash_file(PHASE09 / "target_labels.json"), "phase09_windows_hash": _hash_file(PHASE09 / "candidate_event_windows.json"), "lambda_input_hash": _hash_file(LAMBDA_INPUT), "target_spec_hash": _hash_file(SPEC), "fit_scope": "canonical_v1_only", "confirmation_scope": "staging_extension_candidate_only"}
    coverage = {"train_match_count": partition["train_count"], "confirmation_match_count": partition["confirmation_count"], "prediction_count": len(predictions), "target_count": len(TARGETS), "diagnostic_target_count": len(DIAGNOSTIC_TARGETS)}
    result = {"config": asdict(active), "input_manifest": manifest, "partition": partition, "coverage": coverage, "metrics": metrics, "bootstrap_results": bootstrap, "metrics_by_match": scored, "audit": {**audit, "classification": classification}, "validation_report": validation, "final_report": final}
    _publish(result)
    LOGGER.info("Fase 10 targets temporales: %s", classification)
    return result


# Version: 1.0.0
# Created: 2026-07-26
