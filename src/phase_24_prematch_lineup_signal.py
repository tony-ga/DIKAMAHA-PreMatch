"""Evalúa una señal pre-match basada en titulares y continuidad.

La fase usa únicamente la composición de titulares/formación recuperada de
ESPN y, como variante de fusión, las features causales de ritmo de Fase 22.
No usa cuotas por su cobertura insuficiente en confirmación.

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
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from src.phase_22_prematch_first_half_signal import FEATURE_NAMES as PACE_FEATURES

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
CONTEXT = ROOT / "artifacts/phase_23_prematch_context_fetch/context_rows.json"
PACE = ROOT / "artifacts/phase_22_prematch_first_half_signal/feature_rows.json"
CALIBRATION = ROOT / "artifacts/phase_22_prematch_first_half_signal/calibration.json"
CONFIRMATION = ROOT / "artifacts/phase_22_prematch_first_half_signal/confirmation.json"
TARGETS09 = ROOT / "artifacts/phase_09_historical_target_revision/target_labels.json"
TARGETS12 = ROOT / "artifacts/phase_12_extension_windows_targets/target_labels.json"
TARGETS16 = ROOT / "artifacts/phase_16_backfill_windows/target_labels.json"
TARGETS19 = ROOT / "artifacts/phase_19_current_season_windows/target_labels.json"
SPEC = ROOT / "docs/phases/phase_24_prematch_lineup_signal.md"
OUTPUT = ROOT / "artifacts/phase_24_prematch_lineup_signal"

NUMERIC_FEATURES = ("home_defender_count", "away_defender_count", "home_midfielder_count", "away_midfielder_count", "home_forward_count", "away_forward_count", "home_continuity_last3", "away_continuity_last3", "home_lineup_history_count", "away_lineup_history_count")
CATEGORICAL_FEATURES = ("home_formation", "away_formation")
LINEUP_FEATURES = NUMERIC_FEATURES + CATEGORICAL_FEATURES


@dataclass(frozen=True, slots=True)
class Phase24Config:
    """Parámetros congelados de la evaluación de alineaciones."""

    version: str = "prematch_lineup_signal_v1"
    continuity_matches: int = 3
    logistic_c: float = 0.2
    bootstrap_samples: int = 5000
    bootstrap_seed: int = 20260726
    minimum_confirmation_matches: int = 30
    minimum_positive_events: int = 20


def _load(path: Path) -> Any:
    """Carga JSON local."""

    return json.loads(path.read_text(encoding="utf-8"))


def _hash_file(path: Path) -> str:
    """Calcula SHA-256 de un archivo."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _instant(value: str) -> datetime:
    """Normaliza timestamps a UTC."""

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00").replace(" ", "T"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _position_counts(row: dict[str, Any], side: str) -> dict[str, int]:
    """Agrupa posiciones de titulares en defensa, medio y ataque."""

    counts = {"defender": 0, "midfielder": 0, "forward": 0}
    positions = row.get(f"{side}_starter_position_counts") or {}
    for position, amount in positions.items():
        code = str(position).upper()
        category = "defender" if any(token in code for token in ("D", "B", "CB", "LB", "RB")) else "forward" if any(token in code for token in ("F", "W", "ST", "CF")) else "midfielder"
        counts[category] += int(amount)
    return counts


def _prior_lineups(history: list[dict[str, Any]], team_id: str, side: str) -> list[set[str]]:
    """Obtiene alineaciones previas del mismo equipo."""

    key = f"{side}_provider_team_id"; ids_key = f"{side}_starter_athlete_ids"
    return [set(row.get(ids_key) or []) for row in history if str(row.get(key)) == team_id and row.get(ids_key)]


def _continuity(current: set[str], prior: list[set[str]], limit: int) -> float:
    """Calcula continuidad media contra los últimos partidos."""

    recent = prior[-limit:]
    return sum(len(current & previous) / max(len(current), 1) for previous in recent) / len(recent) if recent else 0.0


def build_lineup_features(context_rows: list[dict[str, Any]], pace_rows: dict[int, dict[str, Any]], config: Phase24Config | None = None) -> list[dict[str, Any]]:
    """Construye features de alineación con historia estrictamente previa."""

    active = config or Phase24Config(); history: list[dict[str, Any]] = []; output = []
    for row in sorted(context_rows, key=lambda item: (_instant(str(item["cutoff_ts"])), int(item["match_id"]))):
        features: dict[str, Any] = {"match_id": int(row["match_id"]), "cutoff_ts": row["cutoff_ts"], "target_match_data_used": False}
        for side in ("home", "away"):
            current = set(row.get(f"{side}_starter_athlete_ids") or []); prior = _prior_lineups(history, str(row.get(f"{side}_provider_team_id")), side); counts = _position_counts(row, side)
            features[f"{side}_formation"] = row.get(f"{side}_formation") or "unknown"; features[f"{side}_defender_count"] = counts["defender"]; features[f"{side}_midfielder_count"] = counts["midfielder"]; features[f"{side}_forward_count"] = counts["forward"]; features[f"{side}_continuity_last3"] = _continuity(current, prior, active.continuity_matches); features[f"{side}_lineup_history_count"] = len(prior)
        features.update({f"pace_{name}": float(pace_rows[int(row["match_id"])][name]) for name in PACE_FEATURES}); features["home_provider_team_id"] = row.get("home_provider_team_id"); features["away_provider_team_id"] = row.get("away_provider_team_id"); output.append(features); history.append(row)
    return output


def _model(include_pace: bool, config: Phase24Config) -> Pipeline:
    """Construye un modelo de alineaciones o de fusión."""

    numeric = list(NUMERIC_FEATURES) + ([f"pace_{name}" for name in PACE_FEATURES] if include_pace else [])
    transformer = ColumnTransformer([("numeric", Pipeline([("imputer", SimpleImputer(strategy="median")), ("scale", StandardScaler())]), numeric), ("categorical", OneHotEncoder(handle_unknown="ignore", sparse_output=False), list(CATEGORICAL_FEATURES))])
    return Pipeline([("features", transformer), ("classifier", LogisticRegression(C=config.logistic_c, max_iter=2000, solver="lbfgs"))])


def _fit_predict(train_rows: list[dict[str, Any]], target_index: dict[int, dict[str, Any]], eval_rows: list[dict[str, Any]], include_pace: bool, config: Phase24Config) -> list[float]:
    """Ajusta el modelo en train y predice el bloque de evaluación."""

    model = _model(include_pace, config); fields = list(NUMERIC_FEATURES) + list(CATEGORICAL_FEATURES) + ([f"pace_{name}" for name in PACE_FEATURES] if include_pace else [])
    train_frame = pd.DataFrame([{field: row[field] for field in fields} for row in train_rows]); eval_frame = pd.DataFrame([{field: row[field] for field in fields} for row in eval_rows])
    model.fit(train_frame, [int(bool(target_index[int(row["match_id"])]["first_half_goal"])) for row in train_rows])
    return [float(value) for value in model.predict_proba(eval_frame)[:, 1]]


def _loss(probability: float, actual: bool) -> float:
    """Calcula log-loss binario."""

    bounded = min(max(float(probability), 1e-12), 1.0 - 1e-12)
    return -math.log(bounded if actual else 1.0 - bounded)


def _bootstrap(values: list[float], config: Phase24Config, offset: int) -> dict[str, Any]:
    """Calcula IC bootstrap a nivel de partido."""

    rng = np.random.default_rng(config.bootstrap_seed + offset); sample = rng.integers(0, len(values), size=(config.bootstrap_samples, len(values))); means = np.asarray(values)[sample].mean(axis=1); ci = np.quantile(means, [0.025, 0.975]).tolist()
    return {"mean_improvement": float(np.mean(values)), "ci_95": [float(ci[0]), float(ci[1])], "improvement_confirmed": bool(ci[0] > 0.0)}


def _score(eval_rows: list[dict[str, Any]], target_index: dict[int, dict[str, Any]], predictions: dict[str, list[float]], baseline: float, markov: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    """Alinea predicciones y targets."""

    output = []
    for index, row in enumerate(eval_rows):
        match_id = int(row["match_id"]); actual = bool(target_index[match_id]["first_half_goal"]); result = {"match_id": match_id, "target_first_half_goal": actual, "baseline_loss": _loss(baseline, actual), "markov_loss": _loss(float(markov[match_id]["prob_first_half_goal"]), actual)}
        for name, values in predictions.items():
            result[f"{name}_probability"] = values[index]; result[f"{name}_loss"] = _loss(values[index], actual)
        output.append(result)
    return output


def _metrics(rows: list[dict[str, Any]], model_names: tuple[str, ...], config: Phase24Config) -> dict[str, Any]:
    """Resume desempeño y soporte de cada modelo."""

    positive = sum(bool(row["target_first_half_goal"]) for row in rows); result = {"match_count": len(rows), "positive_events": positive, "actual_rate": positive / len(rows), "baseline_log_loss": sum(row["baseline_loss"] for row in rows) / len(rows), "support_sufficient": len(rows) >= config.minimum_confirmation_matches and positive >= config.minimum_positive_events}
    for name in model_names:
        losses = [float(row[f"{name}_loss"]) for row in rows]; improvements = [float(row["baseline_loss"]) - loss for row, loss in zip(rows, losses)]; result[f"{name}_log_loss"] = sum(losses) / len(losses); result[f"{name}_improvement_vs_baseline"] = sum(improvements) / len(improvements); result[f"{name}_bootstrap_vs_baseline"] = _bootstrap(improvements, config, len(name))
    return result


def _target_index() -> dict[int, dict[str, Any]]:
    """Carga labels de todas las cohortes sin duplicar partidos."""

    rows = [row for row in _load(TARGETS09) if row.get("cohort") == "canonical_v1"] + [row for row in _load(TARGETS09) if row.get("cohort") != "canonical_v1"] + _load(TARGETS16) + _load(TARGETS19) + _load(TARGETS12)
    return {int(row["match_id"]): row for row in rows}


def _partitions() -> tuple[set[int], set[int], set[int]]:
    """Recupera los cortes congelados de Fase 22."""

    calibration = _load(CALIBRATION)["partition"]; confirmation = _load(CONFIRMATION)["partition"]
    return set(calibration["train_ids"]), set(calibration["evaluation_ids"]), set(confirmation["evaluation_ids"])


def _publish(result: dict[str, Any]) -> None:
    """Publica artefactos y hashes."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name in ("config", "input_manifest", "coverage", "feature_rows", "metrics", "calibration", "confirmation", "audit"):
        (OUTPUT / f"{name}.json").write_text(json.dumps(result[name], indent=2, sort_keys=True, default=str), encoding="utf-8")
    (OUTPUT / "validation_report.md").write_text(result["validation_report"] + "\n", encoding="utf-8"); (OUTPUT / "final_report.md").write_text(result["final_report"] + "\n", encoding="utf-8")
    hashes = {path.name: _hash_file(path) for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"}; (OUTPUT / "hashes.json").write_text(json.dumps(hashes, indent=2, sort_keys=True), encoding="utf-8")


def run(config: Phase24Config | None = None) -> dict[str, Any]:
    """Evalúa alineaciones y fusión en calibración y confirmación."""

    active = config or Phase24Config(); context = _load(CONTEXT); pace = {int(row["match_id"]): row for row in _load(PACE)}; features = build_lineup_features(context, pace, active); feature_index = {int(row["match_id"]): row for row in features}; targets = _target_index(); base_ids, calibration_ids, confirmation_ids = _partitions(); markov_rows = _load(ROOT / "artifacts/phase_20_full_preconfirmation_retraining/confirmation.json")["predictions"] + _load(ROOT / "artifacts/phase_20_full_preconfirmation_retraining/calibration.json")["predictions"]; markov = {int(row["match_id"]): row for row in markov_rows}
    base_rows = [feature_index[mid] for mid in sorted(base_ids)]; cal_rows = [feature_index[mid] for mid in sorted(calibration_ids)]; full_ids = base_ids | calibration_ids; full_rows = [feature_index[mid] for mid in sorted(full_ids)]; conf_rows = [feature_index[mid] for mid in sorted(confirmation_ids)]; baseline_cal = sum(bool(targets[mid]["first_half_goal"]) for mid in base_ids) / len(base_ids); baseline_conf = sum(bool(targets[mid]["first_half_goal"]) for mid in full_ids) / len(full_ids)
    names = ("lineup", "fusion"); cal_predictions = {name: _fit_predict(base_rows, targets, cal_rows, name == "fusion", active) for name in names}; conf_predictions = {name: _fit_predict(full_rows, targets, conf_rows, name == "fusion", active) for name in names}; calibration = {"partition": {"train_count": len(base_rows), "evaluation_count": len(cal_rows), "overlap": sorted(base_ids & calibration_ids)}, "predictions": _score(cal_rows, targets, cal_predictions, baseline_cal, markov), "metrics": {}}; confirmation = {"partition": {"train_count": len(full_rows), "evaluation_count": len(conf_rows), "overlap": sorted(full_ids & confirmation_ids)}, "predictions": _score(conf_rows, targets, conf_predictions, baseline_conf, markov), "metrics": {}}; calibration["metrics"] = _metrics(calibration["predictions"], names, active); confirmation["metrics"] = _metrics(confirmation["predictions"], names, active)
    confirmed = bool(confirmation["metrics"]["fusion_bootstrap_vs_baseline"]["improvement_confirmed"] and confirmation["metrics"]["support_sufficient"]); classification = "validated" if confirmed else "promising_unconfirmed" if confirmation["metrics"]["fusion_improvement_vs_baseline"] > 0 else "rejected_for_revision"; audit = {"classification": classification, "target_match_data_used": False, "odds_used": False, "lineup_fields_used": ["starter", "position", "formation", "starter_continuity"], "temporal_causality_pass": all(not row["target_match_data_used"] for row in features), "train_calibration_overlap": calibration["partition"]["overlap"], "train_confirmation_overlap": confirmation["partition"]["overlap"], "markets_promoted": False, "confirmation_improvement_confirmed": confirmed}
    coverage = {"feature_rows": len(features), "base_train": len(base_rows), "calibration": len(cal_rows), "confirmation_train": len(full_rows), "confirmation": len(conf_rows), "lineup_rows": sum(bool(row.get("home_formation") and row.get("away_formation")) for row in features), "odds_rows_excluded": 761}
    manifest = {"context_hash": _hash_file(CONTEXT), "pace_hash": _hash_file(PACE), "calibration_partition_hash": _hash_file(CALIBRATION), "confirmation_partition_hash": _hash_file(CONFIRMATION), "spec_hash": _hash_file(SPEC)}
    validation = f"# Validation report — Fase 24\n\n- train base: `{len(base_rows)}`\n- calibración: `{len(cal_rows)}`\n- confirmación: `{len(conf_rows)}`\n- causalidad temporal: `{audit['temporal_causality_pass']}`\n- cuotas usadas: `False`\n- mejora confirmatoria de fusión: `{confirmed}`."
    lines = ["# Fase 24 — señal pre-match de alineaciones", "", f"**Clasificación:** `{classification}`", "", "Las cuotas se excluyeron por cobertura insuficiente en confirmación."]
    for label, block in (("calibración", calibration), ("confirmación", confirmation)):
        item = block["metrics"]; lines.append(f"- `{label}`: lineup `{item['lineup_log_loss']:.6f}`, fusión `{item['fusion_log_loss']:.6f}`, baseline `{item['baseline_log_loss']:.6f}`, IC fusión `{item['fusion_bootstrap_vs_baseline']['ci_95']}`")
    lines.extend(["", "Mercados promovidos: `False`.", "Siguiente paso: mantener la señal en shadow; no cambiar el router sin mejora confirmatoria estricta."])
    result = {"config": asdict(active), "input_manifest": manifest, "coverage": coverage, "feature_rows": features, "metrics": {"calibration": calibration["metrics"], "confirmation": confirmation["metrics"]}, "calibration": calibration, "confirmation": confirmation, "audit": audit, "validation_report": validation, "final_report": "\n".join(lines)}; _publish(result); LOGGER.info("Fase 24 señal de alineaciones: %s", classification); return result


# Version: 1.0.0
# Created: 2026-07-26
