"""Evaluación OOS ampliada de targets temporales v2.

El Markov se ajusta sólo con la cohorte canónica. La extensión de Fase 11
se usa exclusivamente como confirmación temporal posterior al corte.

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
import math
import os
import random
from dataclasses import asdict
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from src.markov_pre_match_v1 import build_transitions
from src.phase_10_temporal_target_evaluation import (
    DIAGNOSTIC_TARGETS, TARGETS, Phase10Config, _bootstrap, _predict,
)

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
CANONICAL_WINDOWS = ROOT / "artifacts/phase_01_event_windows_v1/event_windows.json"
CANONICAL_LABELS = ROOT / "artifacts/phase_02_state_labeling_v1/state_labels.json"
PHASE09 = ROOT / "artifacts/phase_09_historical_target_revision"
PHASE11 = ROOT / "artifacts/phase_11_historical_extension_fetch"
PHASE12 = ROOT / "artifacts/phase_12_extension_windows_targets"
PARAMETERS = ROOT / "artifacts/phase_3_4_dixon_coles_v1_dry_run/dixon_coles_v1_fold_parameters.json"
SPEC = ROOT / "docs/specs/temporal_targets_v2.md"
OUTPUT = ROOT / "artifacts/phase_13_temporal_target_evaluation_extension"


def _load(path: Path) -> Any:
    """Carga JSON local."""

    return json.loads(path.read_text(encoding="utf-8"))


def _hash_file(path: Path) -> str:
    """Calcula SHA-256 de un archivo."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _loss(probability: float, actual: bool) -> float:
    """Calcula log-loss binario con clipping finito."""

    value = min(max(float(probability), 1e-12), 1.0 - 1e-12)
    return -math.log(value if actual else 1.0 - value)


def _target_index(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Indexa labels temporales por partido."""

    return {int(row["match_id"]): row for row in rows}


def _score(predictions: list[dict[str, Any]], targets: list[dict[str, Any]], baseline: dict[str, float]) -> list[dict[str, Any]]:
    """Alinea probabilidades y resultados sin usar el target como feature."""

    index = _target_index(targets)
    scored = []
    for prediction in predictions:
        target = index[int(prediction["match_id"])]
        row = dict(prediction)
        for name in TARGETS + DIAGNOSTIC_TARGETS:
            actual = bool(target[name])
            probability = float(prediction[f"prob_{name}"])
            row[f"target_{name}"] = actual
            row[f"loss_{name}"] = _loss(probability, actual)
            row[f"baseline_{name}"] = baseline[name]
            row[f"baseline_loss_{name}"] = _loss(baseline[name], actual)
        scored.append(row)
    return scored


def _support(rows: list[dict[str, Any]], targets: list[dict[str, Any]], name: str, config: Phase10Config) -> dict[str, Any]:
    """Calcula soporte positivo y oportunidades de un target."""

    index = _target_index(targets)
    positives = sum(bool(row[f"target_{name}"]) for row in rows)
    opportunity_key = {"home_recovery_draw_or_win": "home_trailing_at_half", "away_recovery_draw_or_win": "away_trailing_at_half", "home_reaches_level_after_half": "home_trailing_at_half", "away_reaches_level_after_half": "away_trailing_at_half"}.get(name)
    opportunities = sum(bool(index[int(row["match_id"])][opportunity_key]) for row in rows) if opportunity_key else len(rows)
    return {"positive_events": positives, "opportunities": opportunities, "minimum_positive_events": config.minimum_positive_events, "minimum_opportunities": config.minimum_opportunities, "support_sufficient": positives >= config.minimum_positive_events and opportunities >= config.minimum_opportunities}


def _baseline(targets: list[dict[str, Any]], train_ids: list[int]) -> dict[str, float]:
    """Estima prevalencias usando sólo labels de entrenamiento."""

    index = _target_index(targets)
    train = [index[match_id] for match_id in train_ids]
    return {name: sum(bool(row[name]) for row in train) / len(train) for name in TARGETS + DIAGNOSTIC_TARGETS}


def _metrics(rows: list[dict[str, Any]], targets: list[dict[str, Any]], baseline: dict[str, float], config: Phase10Config) -> dict[str, Any]:
    """Resume log-loss, prevalencia y soporte por target."""

    metrics = {}
    for name in TARGETS + DIAGNOSTIC_TARGETS:
        metrics[name] = {"match_count": len(rows), "actual_rate": sum(bool(row[f"target_{name}"]) for row in rows) / len(rows), "model_probability_mean": sum(float(row[f"prob_{name}"]) for row in rows) / len(rows), "model_log_loss": sum(float(row[f"loss_{name}"]) for row in rows) / len(rows), "baseline_probability": baseline[name], "baseline_log_loss": sum(float(row[f"baseline_loss_{name}"]) for row in rows) / len(rows), "support": _support(rows, targets, name, config)}
    return metrics


def _mapping() -> tuple[dict[int, int], dict[str, Any]]:
    """Lee el catálogo provider→ID interno mediante SELECT únicamente."""

    load_dotenv(ROOT / ".env")
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("missing_database_url")
    engine = create_engine(database_url)
    with engine.connect() as connection:
        rows = connection.execute(text("SELECT id, espn_team_id FROM teams WHERE espn_team_id IS NOT NULL")).mappings().all()
    mapping = {int(row["espn_team_id"]): int(row["id"]) for row in rows}
    return mapping, {"select_only": True, "team_rows": len(rows), "write_statements": 0}


def _priors(mapping: dict[int, int]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Construye lambdas pre-kickoff con parámetros DC congelados."""

    parameters = _load(PARAMETERS)[-1]
    matches = _load(PHASE11 / "candidate_matches.json")
    provider_ids = {int(value) for row in matches for value in (row["home_provider_team_id"], row["away_provider_team_id"])}
    missing = sorted(provider_ids - set(mapping))
    mapping.update({team_id: team_id for team_id in missing})
    attack = {str(key): float(value) for key, value in parameters["attack"].items()}
    defense = {str(key): float(value) for key, value in parameters["defense"].items()}
    mean_attack, mean_defense = sum(attack.values()) / len(attack), sum(defense.values()) / len(defense)
    rows = []
    for match in matches:
        home, away = mapping[int(match["home_provider_team_id"])], mapping[int(match["away_provider_team_id"])]
        rows.append({"match_id": int(match["provider_match_id"]), "home_team_id": home, "away_team_id": away, "cutoff_ts": str(match["kickoff_ts"]), "lambda_base_home": _lambda(parameters, attack, defense, home, away, True, mean_attack, mean_defense), "lambda_base_away": _lambda(parameters, attack, defense, away, home, False, mean_attack, mean_defense)})
    return rows, {"parameter_source": str(PARAMETERS.relative_to(ROOT)), "catalog_missing_fallback_to_provider_id": missing, "mapping_size": len(mapping)}


def _lambda(parameters: dict[str, Any], attack: dict[str, float], defense: dict[str, float], team: int, rival: int, home: bool, mean_attack: float, mean_defense: float) -> float:
    """Calcula una intensidad DC limitada a un rango finito."""

    attack_value = attack.get(str(team), mean_attack)
    defense_value = defense.get(str(rival), mean_defense)
    offset = float(parameters["home_advantage"]) if home else 0.0
    value = math.exp(float(parameters["league_intercept"]) + offset + attack_value - defense_value)
    return max(1e-9, min(value, 100.0))


def _partition(train_targets: list[dict[str, Any]], confirmation: list[dict[str, Any]]) -> dict[str, Any]:
    """Crea la partición temporal sin solapamiento."""

    train_ids = sorted(int(row["match_id"]) for row in train_targets)
    confirmation_ids = sorted(int(row["match_id"]) for row in confirmation)
    return {"train_ids": train_ids, "validation_ids": [], "confirmation_ids": confirmation_ids, "train_count": len(train_ids), "validation_count": 0, "confirmation_count": len(confirmation_ids), "order": "match_date_asc_match_id_asc", "overlap": sorted(set(train_ids) & set(confirmation_ids))}


def _publish(result: dict[str, Any]) -> None:
    """Publica resultados, auditoría y hashes."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name in ("config", "input_manifest", "partition", "coverage", "metrics", "bootstrap_results", "metrics_by_match", "audit"):
        (OUTPUT / f"{name}.json").write_text(json.dumps(result[name], indent=2, sort_keys=True, default=str), encoding="utf-8")
    (OUTPUT / "validation_report.md").write_text(result["validation_report"] + "\n", encoding="utf-8")
    (OUTPUT / "final_report.md").write_text(result["final_report"] + "\n", encoding="utf-8")
    hashes = {path.name: _hash_file(path) for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"}
    (OUTPUT / "hashes.json").write_text(json.dumps(hashes, indent=2, sort_keys=True), encoding="utf-8")


def run(config: Phase10Config | None = None) -> dict[str, Any]:
    """Ejecuta evaluación Markov OOS sobre la extensión ampliada."""

    active = config or Phase10Config()
    canonical_windows, canonical_labels = _load(CANONICAL_WINDOWS), _load(CANONICAL_LABELS)
    old_targets = [row for row in _load(PHASE09 / "target_labels.json") if row["cohort"] == "canonical_v1"]
    extension_targets = _load(PHASE12 / "target_labels.json")
    targets = old_targets + extension_targets
    partition = _partition(old_targets, extension_targets)
    mapping, db_audit = _mapping()
    prior_rows, prior_audit = _priors(mapping)
    priors = {int(row["match_id"]): row for row in prior_rows}
    transitions, _ = build_transitions(canonical_windows, canonical_labels)
    predictions = _predict(canonical_windows, canonical_labels, priors, partition, active)
    baseline = _baseline(targets, partition["train_ids"])
    scored = _score(predictions, targets, baseline)
    metrics = _metrics(scored, targets, baseline, active)
    bootstrap = {name: _bootstrap([row[f"baseline_loss_{name}"] - row[f"loss_{name}"] for row in scored], active) for name in TARGETS + DIAGNOSTIC_TARGETS}
    audit = {"partition": partition, "prior_coverage_complete": set(priors) == set(partition["confirmation_ids"]), "prediction_coverage_complete": {int(row["match_id"]) for row in predictions} == set(partition["confirmation_ids"]), "train_confirmation_overlap": partition["overlap"], "model_fit_match_ids": partition["train_ids"], "target_outcomes_used_as_features": False, "bootstrap_unit": "complete_match", "postgresql_modified": False, "database": db_audit, "prior_generation": prior_audit}
    supported = [name for name in TARGETS if metrics[name]["support"]["support_sufficient"]]
    confirmed = [name for name in supported if bootstrap[name]["improvement_confirmed"]]
    classification = "promising_unconfirmed" if confirmed else "rejected_for_revision"
    audit.update({"classification": classification, "targets_with_sufficient_support": supported, "targets_with_confirmed_improvement": confirmed, "markets_promoted": False})
    coverage = {"train_match_count": partition["train_count"], "confirmation_match_count": partition["confirmation_count"], "prediction_count": len(predictions), "target_count": len(TARGETS), "diagnostic_target_count": len(DIAGNOSTIC_TARGETS), "transition_count_train": len(transitions)}
    manifest = {"canonical_windows_hash": _hash_file(CANONICAL_WINDOWS), "canonical_labels_hash": _hash_file(CANONICAL_LABELS), "canonical_targets_hash": _hash_file(PHASE09 / "target_labels.json"), "extension_windows_hash": _hash_file(PHASE12 / "event_windows.json"), "extension_targets_hash": _hash_file(PHASE12 / "target_labels.json"), "parameters_hash": _hash_file(PARAMETERS), "target_spec_hash": _hash_file(SPEC), "fit_scope": "canonical_v1_only", "confirmation_scope": "phase11_extension_candidate_only"}
    validation = f"# Validation report — Fase 13\n\n- entrenamiento: `{partition['train_count']}` partidos\n- confirmación: `{partition['confirmation_count']}` partidos\n- targets con soporte: `{supported}`\n- mejoras bootstrap confirmadas: `{confirmed}`\n- ningún mercado se promueve automáticamente."
    lines = [f"# Fase 13 — evaluación OOS ampliada", "", f"**Clasificación:** `{classification}`", "", f"- train: `{partition['train_count']}` partidos", f"- confirmación: `{partition['confirmation_count']}` partidos", f"- targets con soporte suficiente: `{supported}`"]
    for name in TARGETS:
        item, ci = metrics[name], bootstrap[name]["ci_95"]
        lines.append(f"- `{name}`: log-loss Markov `{item['model_log_loss']:.6f}`, baseline `{item['baseline_log_loss']:.6f}`, IC mejora `{ci}`")
    lines.extend(["", "Mercados promovidos: `False`.", "Siguiente paso: conservar sólo targets que superen soporte, calibración y replicación temporal independiente."])
    result = {"config": asdict(active), "input_manifest": manifest, "partition": partition, "coverage": coverage, "metrics": metrics, "bootstrap_results": bootstrap, "metrics_by_match": scored, "audit": audit, "validation_report": validation, "final_report": "\n".join(lines)}
    _publish(result)
    LOGGER.info("Fase 13 evaluación OOS ampliada: %s", classification)
    return result


# Version: 1.0.0
# Created: 2026-07-26
