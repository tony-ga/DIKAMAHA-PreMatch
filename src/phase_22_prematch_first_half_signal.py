"""Evalúa una señal pre-match de ritmo histórico de primera mitad.

La fase mantiene intactos Markov y ``match_features v1``. Construye features
causales desde ventanas de partidos anteriores, entrena una regresión logística
auxiliar y la compara con el baseline y Markov en dos cortes temporales.

Requirements:
    - numpy
    - scikit-learn

Version: 1.0.0
Created: 2026-07-26
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from src.phase_10_temporal_target_evaluation import TARGETS

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
CANONICAL_WINDOWS = ROOT / "artifacts/phase_01_event_windows_v1/event_windows.json"
CANONICAL_TARGETS = ROOT / "artifacts/phase_09_historical_target_revision/target_labels.json"
PHASE09_WINDOWS = ROOT / "artifacts/phase_09_historical_target_revision/candidate_event_windows.json"
PHASE12_WINDOWS = ROOT / "artifacts/phase_12_extension_windows_targets/event_windows.json"
PHASE12_TARGETS = ROOT / "artifacts/phase_12_extension_windows_targets/target_labels.json"
PHASE16_WINDOWS = ROOT / "artifacts/phase_16_backfill_windows/event_windows.json"
PHASE16_TARGETS = ROOT / "artifacts/phase_16_backfill_windows/target_labels.json"
PHASE19_WINDOWS = ROOT / "artifacts/phase_19_current_season_windows/event_windows.json"
PHASE19_TARGETS = ROOT / "artifacts/phase_19_current_season_windows/target_labels.json"
PHASE20 = ROOT / "artifacts/phase_20_full_preconfirmation_retraining"
SPEC = ROOT / "docs/phases/phase_22_prematch_first_half_signal.md"
OUTPUT = ROOT / "artifacts/phase_22_prematch_first_half_signal"
EVENT_METRICS = ("goals", "goals_against", "shots", "shots_on_target", "corners", "pressure", "fouls", "yellow_cards", "red_cards", "event_count")
FEATURE_NAMES = tuple(f"{side}_first_half_{metric}_rate" for side in ("home", "away") for metric in EVENT_METRICS) + ("home_history_count", "away_history_count", "home_rest_days", "away_rest_days")


@dataclass(frozen=True, slots=True)
class Phase22Config:
    """Parámetros congelados de la señal auxiliar."""

    version: str = "prematch_first_half_signal_v1"
    history_matches: int = 5
    shrinkage_matches: float = 2.0
    logistic_c: float = 0.2
    bootstrap_samples: int = 5000
    bootstrap_seed: int = 20260726
    minimum_confirmation_matches: int = 30
    minimum_positive_events: int = 20


def _load(path: Path) -> Any:
    """Carga un JSON local."""

    return json.loads(path.read_text(encoding="utf-8"))


def _hash_file(path: Path) -> str:
    """Calcula el SHA-256 de un archivo."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _instant(value: str) -> datetime:
    """Normaliza una fecha a UTC."""

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00").replace(" ", "T"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _side_metrics() -> dict[str, float]:
    """Crea un acumulador de métricas de una plantilla."""

    return {metric: 0.0 for metric in EVENT_METRICS}


def _match_stats(windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Resume cada partido y sus métricas observadas de primera mitad."""

    grouped: dict[int, dict[str, Any]] = {}
    for row in windows:
        match_id = int(row["match_id"])
        match = grouped.setdefault(match_id, {"match_id": match_id, "match_date": str(row["match_date"]), "home": _side_metrics(), "away": _side_metrics(), "home_team_id": None, "away_team_id": None})
        side = "home" if bool(row["is_home"]) else "away"
        match[f"{side}_team_id"] = int(row["team_id"])
        if not bool(row["is_home"]):
            match["away_team_id"] = int(row["team_id"])
        if int(row["window_index"]) >= 3:
            continue
        for metric in EVENT_METRICS:
            match[side][metric] += float(row.get(metric, 0) or 0)
    output = []
    for match in grouped.values():
        if match["home_team_id"] is None or match["away_team_id"] is None:
            raise ValueError(f"incomplete_team_identity:{match['match_id']}")
        match["home"]["goals_against"] = match["away"]["goals"]
        match["away"]["goals_against"] = match["home"]["goals"]
        output.append(match)
    return sorted(output, key=lambda row: (_instant(str(row["match_date"])), int(row["match_id"])))


def _normalize_windows(rows: list[dict[str, Any]], mapping: dict[int, int]) -> list[dict[str, Any]]:
    """Normaliza IDs ESPN de cohortes externas con fallback explícito."""

    return [{**row, "team_id": mapping.get(int(row["team_id"]), int(row["team_id"])), "opponent_team_id": mapping.get(int(row["opponent_team_id"]), int(row["opponent_team_id"]))} for row in rows]


def _global_rate(history: list[dict[str, Any]], side: str, metric: str) -> float:
    """Calcula la media histórica previa de una métrica."""

    values = [float(row[side][metric]) for row in history]
    return sum(values) / len(values) if values else 0.0


def _team_records(history: list[dict[str, Any]], team_id: int, side: str) -> list[dict[str, Any]]:
    """Obtiene los registros históricos de un equipo en una localía."""

    return [row[side] | {"match_id": row["match_id"], "match_date": row["match_date"]} for row in history if int(row[f"{side}_team_id"]) == team_id]


def _rate(records: list[dict[str, Any]], metric: str, history: list[dict[str, Any]], side: str, config: Phase22Config) -> float:
    """Suaviza la tasa reciente hacia la media histórica previa."""

    recent = records[-config.history_matches:]
    prior_mean = _global_rate(history, side, metric)
    numerator = sum(float(row[metric]) for row in recent) + config.shrinkage_matches * prior_mean
    return numerator / (len(recent) + config.shrinkage_matches)


def _team_features(history: list[dict[str, Any]], team_id: int, side: str, cutoff_ts: str, config: Phase22Config) -> dict[str, float]:
    """Calcula features causales de primera mitad para un equipo."""

    records = _team_records(history, team_id, side)
    values = {f"{side}_first_half_{metric}_rate": _rate(records, metric, history, side, config) for metric in EVENT_METRICS}
    recent = records[-config.history_matches:]
    latest = _instant(str(records[-1]["match_date"])) if records else None
    cutoff = _instant(cutoff_ts)
    values[f"{side}_history_count"] = float(len(records))
    values[f"{side}_rest_days"] = float(max(0, (cutoff - latest).total_seconds() / 86400.0)) if latest and cutoff else 0.0
    return values


def build_feature_rows(matches: list[dict[str, Any]], config: Phase22Config | None = None) -> list[dict[str, Any]]:
    """Construye features partido a partido usando sólo historia anterior."""

    active = config or Phase22Config()
    history: list[dict[str, Any]] = []
    output: list[dict[str, Any]] = []
    for match in sorted(matches, key=lambda row: (_instant(str(row["match_date"])), int(row["match_id"]))):
        cutoff_ts = str(match["match_date"])
        home = _team_features(history, int(match["home_team_id"]), "home", cutoff_ts, active)
        away = _team_features(history, int(match["away_team_id"]), "away", cutoff_ts, active)
        output.append({"match_id": int(match["match_id"]), "cutoff_ts": str(match["match_date"]), **home, **away, "target_match_data_used": False, "home_prior_match_ids": [int(row["match_id"]) for row in _team_records(history, int(match["home_team_id"]), "home")[-active.history_matches:]], "away_prior_match_ids": [int(row["match_id"]) for row in _team_records(history, int(match["away_team_id"]), "away")[-active.history_matches:]]})
        history.append(match)
    return output


def _target_index(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Indexa targets y rechaza IDs duplicados."""

    index: dict[int, dict[str, Any]] = {}
    for row in rows:
        match_id = int(row["match_id"])
        if match_id in index:
            raise ValueError(f"duplicate_target_id:{match_id}")
        index[match_id] = row
    return index


def _feature_index(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Indexa features y rechaza IDs duplicados."""

    index: dict[int, dict[str, Any]] = {}
    for row in rows:
        match_id = int(row["match_id"])
        if match_id in index:
            raise ValueError(f"duplicate_feature_id:{match_id}")
        index[match_id] = row
    return index


def _model(config: Phase22Config) -> Pipeline:
    """Construye el modelo regularizado y reproducible."""

    return Pipeline([("imputer", SimpleImputer(strategy="median")), ("scaler", StandardScaler()), ("classifier", LogisticRegression(C=config.logistic_c, max_iter=2000, solver="lbfgs"))])


def _fit_predict(train_rows: list[dict[str, Any]], train_targets: dict[int, dict[str, Any]], eval_rows: list[dict[str, Any]], config: Phase22Config) -> list[float]:
    """Ajusta el modelo en train y predice sólo el bloque recibido."""

    model = _model(config)
    train_x = [[float(row[name]) for name in FEATURE_NAMES] for row in train_rows]
    train_y = [int(bool(train_targets[int(row["match_id"])]["first_half_goal"])) for row in train_rows]
    model.fit(train_x, train_y)
    eval_x = [[float(row[name]) for name in FEATURE_NAMES] for row in eval_rows]
    return [float(value) for value in model.predict_proba(eval_x)[:, 1]]


def _loss(probability: float, actual: bool) -> float:
    """Calcula log-loss binario con clipping."""

    value = min(max(float(probability), 1e-12), 1.0 - 1e-12)
    return -math.log(value if actual else 1.0 - value)


def _bootstrap(values: list[float], config: Phase22Config, seed_offset: int) -> dict[str, Any]:
    """Calcula IC bootstrap por partido completo."""

    if not values:
        return {"mean_improvement": 0.0, "ci_95": [0.0, 0.0], "improvement_confirmed": False}
    rng = np.random.default_rng(config.bootstrap_seed + seed_offset)
    sample = rng.integers(0, len(values), size=(config.bootstrap_samples, len(values)))
    means = np.asarray(values)[sample].mean(axis=1)
    ci = np.quantile(means, [0.025, 0.975]).tolist()
    return {"mean_improvement": float(np.mean(values)), "ci_95": [float(ci[0]), float(ci[1])], "improvement_confirmed": bool(ci[0] > 0.0)}


def _baseline(targets: dict[int, dict[str, Any]], train_ids: set[int]) -> float:
    """Calcula prevalencia exclusivamente sobre el train."""

    return sum(bool(targets[match_id]["first_half_goal"]) for match_id in train_ids) / len(train_ids)


def _score(eval_rows: list[dict[str, Any]], targets: dict[int, dict[str, Any]], markov: dict[int, dict[str, Any]], baseline: float, feature_probabilities: list[float]) -> list[dict[str, Any]]:
    """Alinea features, targets, baseline y Markov por partido."""

    output = []
    for row, feature_probability in zip(eval_rows, feature_probabilities):
        match_id = int(row["match_id"]); actual = bool(targets[match_id]["first_half_goal"]); markov_probability = float(markov[match_id]["prob_first_half_goal"])
        output.append({"match_id": match_id, "target_first_half_goal": actual, "feature_probability": feature_probability, "markov_probability": markov_probability, "baseline_probability": baseline, "feature_loss": _loss(feature_probability, actual), "markov_loss": _loss(markov_probability, actual), "baseline_loss": _loss(baseline, actual), "cutoff_ts": row["cutoff_ts"]})
    return output


def _metrics(rows: list[dict[str, Any]], config: Phase22Config) -> dict[str, Any]:
    """Resume log-loss, mejoras y soporte de una cohorte."""

    positive = sum(bool(row["target_first_half_goal"]) for row in rows)
    result: dict[str, Any] = {"match_count": len(rows), "positive_events": positive, "actual_rate": positive / len(rows), "baseline_log_loss": sum(float(row["baseline_loss"]) for row in rows) / len(rows), "support_sufficient": len(rows) >= config.minimum_confirmation_matches and positive >= config.minimum_positive_events}
    for model_name in ("feature", "markov"):
        loss_key = f"{model_name}_loss"; values = [float(row[loss_key]) for row in rows]; improvements = [float(row["baseline_loss"]) - value for row, value in zip(rows, values)]
        result[f"{model_name}_log_loss"] = sum(values) / len(values); result[f"{model_name}_improvement_vs_baseline"] = sum(improvements) / len(improvements); result[f"{model_name}_bootstrap_vs_baseline"] = _bootstrap(improvements, config, 1 if model_name == "feature" else 2)
    return result


def _audit_causality(feature_rows: list[dict[str, Any]], match_index: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """Comprueba que los historiales preceden al kickoff del objetivo."""

    violations = []
    for row in feature_rows:
        cutoff = _instant(str(row["cutoff_ts"]))
        for prior_id in row["home_prior_match_ids"] + row["away_prior_match_ids"]:
            if prior_id == int(row["match_id"]) or _instant(str(match_index[prior_id]["match_date"])) >= cutoff:
                violations.append({"match_id": int(row["match_id"]), "prior_match_id": prior_id})
    return {"target_match_data_used": False, "temporal_violations": violations, "temporal_causality_pass": not violations}


def _publish(result: dict[str, Any]) -> None:
    """Publica los artefactos contractuales y sus hashes."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name in ("config", "input_manifest", "coverage", "feature_rows", "metrics", "calibration", "confirmation", "audit"):
        (OUTPUT / f"{name}.json").write_text(json.dumps(result[name], indent=2, sort_keys=True, default=str), encoding="utf-8")
    (OUTPUT / "validation_report.md").write_text(result["validation_report"] + "\n", encoding="utf-8")
    (OUTPUT / "final_report.md").write_text(result["final_report"] + "\n", encoding="utf-8")
    hashes = {path.name: _hash_file(path) for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"}
    (OUTPUT / "hashes.json").write_text(json.dumps(hashes, indent=2, sort_keys=True), encoding="utf-8")


def _match_signature(row: dict[str, Any]) -> tuple[str, int, int]:
    """Construye una identidad temporal independiente del ID de origen."""

    return (str(_instant(str(row["match_date"]))), int(row["home_team_id"]), int(row["away_team_id"]))


def _datasets(mapping: dict[int, int]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Carga todas las ventanas y targets de las cohortes de la fase."""

    sources = [("canonical", _match_stats(_load(CANONICAL_WINDOWS)), [row for row in _load(CANONICAL_TARGETS) if row.get("cohort") == "canonical_v1"]), ("backfill", _match_stats(_normalize_windows(_load(PHASE16_WINDOWS), mapping)), _load(PHASE16_TARGETS)), ("current", _match_stats(_normalize_windows(_load(PHASE19_WINDOWS), mapping)), _load(PHASE19_TARGETS)), ("calibration", _match_stats(_normalize_windows(_load(PHASE09_WINDOWS), mapping)), [row for row in _load(CANONICAL_TARGETS) if row.get("cohort") != "canonical_v1"]), ("confirmation", _match_stats(_normalize_windows(_load(PHASE12_WINDOWS), mapping)), _load(PHASE12_TARGETS))]
    priority = {name: index for index, (name, _, _) in enumerate(sources)}; owners: dict[tuple[str, int, int], tuple[str, dict[str, Any]]] = {}; duplicate_records = []
    for name, rows, _ in sources:
        for row in rows:
            signature = _match_signature(row)
            if signature not in owners:
                owners[signature] = (name, row); continue
            previous_name, previous = owners[signature]
            if previous_name == name:
                raise ValueError(f"duplicate_match_signature_same_source:{signature}")
            winner = name if priority[name] > priority[previous_name] else previous_name
            owners[signature] = (winner, row if winner == name else previous)
            duplicate_records.append({"signature": signature, "kept_source": winner, "excluded_source": previous_name if winner == name else name, "kept_match_id": int(row["match_id"] if winner == name else previous["match_id"]), "excluded_match_id": int(previous["match_id"] if winner == name else row["match_id"])})
    kept_ids = {int(row["match_id"]) for _, row in owners.values()}; matches = [row for _, row in owners.values()]; targets = [target for _, _, source_targets in sources for target in source_targets if int(target["match_id"]) in kept_ids]
    return matches, targets, {"duplicate_records": duplicate_records, "kept_match_count": len(matches), "excluded_match_count": len(duplicate_records)}


def _model_predictions(features: dict[int, dict[str, Any]], targets: dict[int, dict[str, Any]], markov_rows: list[dict[str, Any]], config: Phase22Config) -> tuple[dict[str, Any], dict[str, Any]]:
    """Calcula calibración en 44 y confirmación independiente en 241."""

    available_ids = set(features)
    base_ids = ({int(row["match_id"]) for row in _load(PHASE16_TARGETS) + [r for r in _load(CANONICAL_TARGETS) if r.get("cohort") == "canonical_v1"] + _load(PHASE19_TARGETS)}) & available_ids
    calibration_ids = ({int(row["match_id"]) for row in _load(CANONICAL_TARGETS) if row.get("cohort") != "canonical_v1"}) & available_ids; confirmation_ids = {int(row["match_id"]) for row in _load(PHASE12_TARGETS)} & available_ids
    markov = {int(row["match_id"]): row for row in markov_rows}; base_rows = [features[match_id] for match_id in sorted(base_ids)]; calibration_rows = [features[match_id] for match_id in sorted(calibration_ids)]; full_ids = base_ids | calibration_ids; full_rows = [features[match_id] for match_id in sorted(full_ids)]; confirmation_rows = [features[match_id] for match_id in sorted(confirmation_ids)]
    calibration_scored = _score(calibration_rows, targets, markov, _baseline(targets, base_ids), _fit_predict(base_rows, targets, calibration_rows, config)); confirmation_scored = _score(confirmation_rows, targets, markov, _baseline(targets, full_ids), _fit_predict(full_rows, targets, confirmation_rows, config))
    return {"partition": {"train_count": len(base_ids), "evaluation_count": len(calibration_ids), "train_ids": sorted(base_ids), "evaluation_ids": sorted(calibration_ids), "overlap": sorted(base_ids & calibration_ids)}, "predictions": calibration_scored, "metrics": _metrics(calibration_scored, config)}, {"partition": {"train_count": len(full_ids), "evaluation_count": len(confirmation_ids), "train_ids": sorted(full_ids), "evaluation_ids": sorted(confirmation_ids), "overlap": sorted(full_ids & confirmation_ids)}, "predictions": confirmation_scored, "metrics": _metrics(confirmation_scored, config)}


def run(config: Phase22Config | None = None) -> dict[str, Any]:
    """Ejecuta la señal auxiliar y publica Fase 22."""

    active = config or Phase22Config()
    from src.phase_14_dynamic_markov_recalibration import _team_mapping
    mapping, database = _team_mapping(); matches, target_rows, deduplication = _datasets(mapping); targets = _target_index(target_rows); stats_index = _feature_index(build_feature_rows(matches, active))
    if len(matches) != len(stats_index):
        raise ValueError("feature_coverage_mismatch")
    markov_calibration = _load(PHASE20 / "calibration.json")["predictions"]; markov_confirmation = _load(PHASE20 / "confirmation.json")["predictions"]
    calibration, confirmation = _model_predictions(stats_index, targets, markov_calibration + markov_confirmation, active)
    all_features = [stats_index[match_id] for match_id in sorted(stats_index)]
    causality = _audit_causality(all_features, {int(row["match_id"]): row for row in matches}); supported = bool(confirmation["metrics"]["support_sufficient"]); confirmed = bool(confirmation["metrics"]["feature_bootstrap_vs_baseline"]["improvement_confirmed"] and supported); classification = "validated" if confirmed else "promising_unconfirmed" if confirmation["metrics"]["feature_improvement_vs_baseline"] > 0 else "rejected_for_revision"
    audit = {"classification": classification, "database": database, **causality, "feature_version": "first_half_event_pace_v1", "target_outcomes_used_as_features": False, "match_level_iid": True, "train_calibration_overlap": calibration["partition"]["overlap"], "train_confirmation_overlap": confirmation["partition"]["overlap"], "markov_used_as_feature": False, "match_features_v1_modified": False, "markets_promoted": False, "confirmation_support_sufficient": supported, "confirmation_improvement_confirmed": confirmed, "deduplication": deduplication}
    manifest = {"canonical_windows_hash": _hash_file(CANONICAL_WINDOWS), "canonical_targets_hash": _hash_file(CANONICAL_TARGETS), "phase09_windows_hash": _hash_file(PHASE09_WINDOWS), "phase12_windows_hash": _hash_file(PHASE12_WINDOWS), "phase12_targets_hash": _hash_file(PHASE12_TARGETS), "phase16_windows_hash": _hash_file(PHASE16_WINDOWS), "phase16_targets_hash": _hash_file(PHASE16_TARGETS), "phase19_windows_hash": _hash_file(PHASE19_WINDOWS), "phase19_targets_hash": _hash_file(PHASE19_TARGETS), "phase20_calibration_hash": _hash_file(PHASE20 / "calibration.json"), "phase20_confirmation_hash": _hash_file(PHASE20 / "confirmation.json"), "phase_spec_hash": _hash_file(SPEC)}
    coverage = {"all_matches": len(matches), "feature_rows": len(all_features), "base_train": calibration["partition"]["train_count"], "calibration": calibration["partition"]["evaluation_count"], "confirmation_train": confirmation["partition"]["train_count"], "confirmation": confirmation["partition"]["evaluation_count"], "feature_fields": list(FEATURE_NAMES), "causal_rows": sum(bool(row["target_match_data_used"] is False) for row in all_features), "duplicate_signatures_excluded": deduplication["excluded_match_count"]}
    validation = f"# Validation report — Fase 22\n\n- train base: `{calibration['partition']['train_count']}` partidos\n- calibración: `{calibration['partition']['evaluation_count']}` partidos\n- train confirmatorio: `{confirmation['partition']['train_count']}` partidos\n- confirmación: `{confirmation['partition']['evaluation_count']}` partidos\n- causalidad temporal: `{causality['temporal_causality_pass']}`\n- soporte confirmatorio: `{supported}`\n- mejora bootstrap confirmada: `{confirmed}`."
    metrics = {"calibration": calibration["metrics"], "confirmation": confirmation["metrics"]}
    lines = ["# Fase 22 — señal pre-match de ritmo de primera mitad", "", f"**Clasificación:** `{classification}`", "", "La señal usa sólo eventos históricos de primera mitad y no modifica `match_features v1`."]
    for label, block in (("calibración", calibration), ("confirmación", confirmation)):
        item = block["metrics"]; lines.append(f"- `{label}`: feature `{item['feature_log_loss']:.6f}`, Markov `{item['markov_log_loss']:.6f}`, baseline `{item['baseline_log_loss']:.6f}`, mejora feature `{item['feature_improvement_vs_baseline']:.6f}`, IC `{item['feature_bootstrap_vs_baseline']['ci_95']}`")
    lines.extend(["", "Mercados promovidos: `False`.", "Siguiente paso: conservar o descartar la señal según el intervalo confirmatorio; no cambiar el router sin una decisión documentada."])
    result = {"config": asdict(active), "input_manifest": manifest, "coverage": coverage, "feature_rows": all_features, "metrics": metrics, "calibration": calibration, "confirmation": confirmation, "audit": audit, "validation_report": validation, "final_report": "\n".join(lines)}
    _publish(result); LOGGER.info("Fase 22 señal de primera mitad: %s", classification); return result


# Version: 1.0.0
# Created: 2026-07-26
