"""Recalibración Markov con priors dinámicos y expansión causal del train.

La fase usa resultados previos al kickoff para estimar forma por equipo,
entrena Markov con la cohorte disponible antes de cada bloque y confirma en
una extensión posterior. No usa eventos ni marcador del partido objetivo.

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
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from src.phase_09_historical_target_revision import derive_targets
from src.phase_10_temporal_target_evaluation import (
    DIAGNOSTIC_TARGETS, TARGETS, Phase10Config, _bootstrap, _predict,
)
from src.state_labeling_v1 import StateLabelingConfig, label_rows

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
CANONICAL_WINDOWS = ROOT / "artifacts/phase_01_event_windows_v1/event_windows.json"
CANONICAL_LABELS = ROOT / "artifacts/phase_02_state_labeling_v1/state_labels.json"
PHASE09_WINDOWS = ROOT / "artifacts/phase_09_historical_target_revision/candidate_event_windows.json"
PHASE09_TARGETS = ROOT / "artifacts/phase_09_historical_target_revision/target_labels.json"
PHASE12_WINDOWS = ROOT / "artifacts/phase_12_extension_windows_targets/event_windows.json"
PHASE12_TARGETS = ROOT / "artifacts/phase_12_extension_windows_targets/target_labels.json"
SPEC = ROOT / "docs/specs/temporal_targets_v2.md"
OUTPUT = ROOT / "artifacts/phase_14_dynamic_markov_recalibration"


@dataclass(frozen=True, slots=True)
class Phase14Config:
    """Parámetros congelados de la recalibración dinámica."""

    version: str = "dynamic_markov_recalibration_v1"
    half_life_days: float = 90.0
    shrinkage_matches: float = 2.0
    simulations_per_match: int = 5000
    simulation_seed: int = 20260726
    bootstrap_samples: int = 5000
    bootstrap_seed: int = 20260726
    minimum_positive_events: int = 20
    minimum_opportunities: int = 30


def _load(path: Path) -> Any:
    """Carga JSON local."""

    return json.loads(path.read_text(encoding="utf-8"))


def _hash_file(path: Path) -> str:
    """Calcula SHA-256 de un archivo."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _instant(value: str) -> datetime:
    """Normaliza timestamps a datetimes UTC comparables."""

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00").replace(" ", "T"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _team_mapping() -> tuple[dict[int, int], dict[str, Any]]:
    """Lee el mapeo ESPN→ID interno con una consulta SELECT."""

    load_dotenv(ROOT / ".env")
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("missing_database_url")
    engine = create_engine(database_url)
    with engine.connect() as connection:
        rows = connection.execute(text("SELECT id, espn_team_id FROM teams WHERE espn_team_id IS NOT NULL")).mappings().all()
    mapping = {int(row["espn_team_id"]): int(row["id"]) for row in rows}
    return mapping, {"select_only": True, "team_rows": len(rows), "write_statements": 0}


def _match_rows(windows: list[dict[str, Any]], mapping: dict[int, int]) -> list[dict[str, Any]]:
    """Resume un partido por identidad, fecha y marcador observado."""

    grouped: dict[int, dict[str, Any]] = {}
    for row in windows:
        match_id = int(row["match_id"])
        if int(row["window_index"]) == 0:
            home = int(row["team_id"]) if bool(row["is_home"]) else int(row["opponent_team_id"])
            away = int(row["opponent_team_id"]) if bool(row["is_home"]) else int(row["team_id"])
            grouped[match_id] = {"match_id": match_id, "match_date": str(row["match_date"]), "home_team_id": mapping.get(home, home), "away_team_id": mapping.get(away, away)}
    for match_id, match in grouped.items():
        rows = [row for row in windows if int(row["match_id"]) == match_id]
        match["home_score"] = sum(int(row["goals"]) for row in rows if bool(row["is_home"]))
        match["away_score"] = sum(int(row["goals"]) for row in rows if not bool(row["is_home"]))
    return sorted(grouped.values(), key=lambda row: (row["match_date"], row["match_id"]))


def _dynamic_prior(match: dict[str, Any], history: list[dict[str, Any]], config: Phase14Config) -> dict[str, Any]:
    """Calcula intensidades pre-kickoff con forma venue-aware y shrinkage."""

    cutoff = _instant(str(match["match_date"]))
    sums: dict[tuple[int, bool], list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    league = [0.0, 0.0, 0.0, 0.0]
    for row in history:
        event_time = _instant(str(row["match_date"]))
        if event_time >= cutoff:
            continue
        weight = math.exp(-max(0.0, (cutoff - event_time).total_seconds() / 86400.0) / config.half_life_days)
        _add_rate(sums[(int(row["home_team_id"]), True)], row["home_score"], row["away_score"], weight)
        _add_rate(sums[(int(row["away_team_id"]), False)], row["away_score"], row["home_score"], weight)
        league[0] += float(row["home_score"]) * weight; league[1] += float(row["away_score"]) * weight
        league[2] += weight; league[3] += weight
    home_rate, away_rate = league[0] / max(league[2], 1e-9), league[1] / max(league[3], 1e-9)
    home_attack, _ = _venue_rate(sums, int(match["home_team_id"]), True, home_rate, config)
    _, home_defense = _venue_rate(sums, int(match["home_team_id"]), True, away_rate, config)
    away_attack, away_defense = _venue_rate(sums, int(match["away_team_id"]), False, away_rate, config)
    home_lambda = home_rate * (home_attack / home_rate) * (away_defense / home_rate)
    away_lambda = away_rate * (away_attack / away_rate) * (home_defense / away_rate)
    return {"match_id": int(match["match_id"]), "home_team_id": int(match["home_team_id"]), "away_team_id": int(match["away_team_id"]), "cutoff_ts": str(match["match_date"]), "lambda_base_home": max(0.05, min(5.0, home_lambda)), "lambda_base_away": max(0.05, min(5.0, away_lambda))}


def _add_rate(bucket: list[float], goals_for: int, goals_against: int, weight: float) -> None:
    """Acumula goles ponderados y masa efectiva."""

    bucket[0] += float(goals_for) * weight; bucket[1] += float(goals_against) * weight; bucket[2] += weight


def _venue_rate(sums: dict[tuple[int, bool], list[float]], team_id: int, home: bool, base: float, config: Phase14Config) -> tuple[float, float]:
    """Aplica shrinkage de ataque y defensa hacia la media de liga."""

    goals_for, goals_against, weight = sums[(team_id, home)]
    denominator = weight + config.shrinkage_matches
    return (goals_for + config.shrinkage_matches * base) / denominator, (goals_against + config.shrinkage_matches * base) / denominator


def _score_rows(predictions: list[dict[str, Any]], targets: list[dict[str, Any]], baseline: dict[str, float]) -> list[dict[str, Any]]:
    """Calcula log-loss por partido para cada target."""

    index = {int(row["match_id"]): row for row in targets}; output = []
    for prediction in predictions:
        target = index[int(prediction["match_id"])]; row = dict(prediction)
        for name in TARGETS + DIAGNOSTIC_TARGETS:
            actual = bool(target[name]); probability = min(max(float(prediction[f"prob_{name}"]), 1e-12), 1.0 - 1e-12)
            row[f"target_{name}"] = actual; row[f"loss_{name}"] = -math.log(probability if actual else 1.0 - probability); row[f"baseline_{name}"] = baseline[name]; row[f"baseline_loss_{name}"] = -math.log(baseline[name] if actual else 1.0 - baseline[name])
        output.append(row)
    return output


def _metrics(rows: list[dict[str, Any]], targets: list[dict[str, Any]], config: Phase14Config) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resume log-loss y soporte sin tratar ventanas como IID."""

    target_index = {int(row["match_id"]): row for row in targets}; metrics, bootstrap = {}, {}
    for name in TARGETS + DIAGNOSTIC_TARGETS:
        values = [row for row in rows]; opportunity = _opportunities(values, target_index, name)
        metrics[name] = {"match_count": len(values), "actual_rate": sum(bool(row[f"target_{name}"]) for row in values) / len(values), "model_probability_mean": sum(float(row[f"prob_{name}"]) for row in values) / len(values), "model_log_loss": sum(float(row[f"loss_{name}"]) for row in values) / len(values), "baseline_log_loss": sum(float(row[f"baseline_loss_{name}"]) for row in values) / len(values), "support": {"positive_events": sum(bool(row[f"target_{name}"]) for row in values), "opportunities": opportunity, "support_sufficient": sum(bool(row[f"target_{name}"]) for row in values) >= config.minimum_positive_events and opportunity >= config.minimum_opportunities}}
        improvements = [row[f"baseline_loss_{name}"] - row[f"loss_{name}"] for row in values]
        bootstrap[name] = _bootstrap(improvements, Phase10Config(bootstrap_samples=config.bootstrap_samples, bootstrap_seed=config.bootstrap_seed))
    return metrics, bootstrap


def _opportunities(rows: list[dict[str, Any]], targets: dict[int, dict[str, Any]], name: str) -> int:
    """Cuenta denominador condicional de reacciones."""

    key = {"home_recovery_draw_or_win": "home_trailing_at_half", "away_recovery_draw_or_win": "away_trailing_at_half", "home_reaches_level_after_half": "home_trailing_at_half", "away_reaches_level_after_half": "away_trailing_at_half"}.get(name)
    return len(rows) if key is None else sum(bool(targets[int(row["match_id"])][key]) for row in rows)


def _baseline(targets: list[dict[str, Any]], train_ids: set[int]) -> dict[str, float]:
    """Estima prevalencias sólo desde partidos anteriores."""

    index = {int(row["match_id"]): row for row in targets}; train = [index[match_id] for match_id in train_ids]
    return {name: sum(bool(row[name]) for row in train) / len(train) for name in TARGETS + DIAGNOSTIC_TARGETS}


def _block(windows: list[dict[str, Any]], labels: list[dict[str, Any]], targets: list[dict[str, Any]], priors: dict[int, dict[str, Any]], train_ids: set[int], confirmation_ids: set[int], config: Phase14Config) -> dict[str, Any]:
    """Entrena Markov en un corte y evalúa sólo su confirmación."""

    partition = {"train_ids": sorted(train_ids), "validation_ids": [], "confirmation_ids": sorted(confirmation_ids), "train_count": len(train_ids), "validation_count": 0, "confirmation_count": len(confirmation_ids), "overlap": sorted(train_ids & confirmation_ids)}
    predictions = _predict(windows, labels, priors, partition, Phase10Config(simulations_per_match=config.simulations_per_match, simulation_seed=config.simulation_seed, bootstrap_samples=config.bootstrap_samples))
    baseline = _baseline(targets, train_ids); scored = _score_rows(predictions, targets, baseline); metrics, bootstrap = _metrics(scored, targets, config)
    return {"partition": partition, "predictions": scored, "metrics": metrics, "bootstrap_results": bootstrap, "baseline": baseline}


def _publish(result: dict[str, Any]) -> None:
    """Publica artefactos contractuales y hashes."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name in ("config", "input_manifest", "coverage", "calibration", "confirmation", "audit"):
        (OUTPUT / f"{name}.json").write_text(json.dumps(result[name], indent=2, sort_keys=True, default=str), encoding="utf-8")
    (OUTPUT / "validation_report.md").write_text(result["validation_report"] + "\n", encoding="utf-8")
    (OUTPUT / "final_report.md").write_text(result["final_report"] + "\n", encoding="utf-8")
    hashes = {path.name: _hash_file(path) for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"}
    (OUTPUT / "hashes.json").write_text(json.dumps(hashes, indent=2, sort_keys=True), encoding="utf-8")


def run(config: Phase14Config | None = None) -> dict[str, Any]:
    """Ejecuta recalibración en 44 partidos y confirmación en 241."""

    active = config or Phase14Config(); mapping, database = _team_mapping()
    canonical_windows, canonical_labels = _load(CANONICAL_WINDOWS), _load(CANONICAL_LABELS)
    previous_windows, previous_targets = _load(PHASE09_WINDOWS), _load(PHASE09_TARGETS)
    confirmation_windows, confirmation_targets = _load(PHASE12_WINDOWS), _load(PHASE12_TARGETS)
    previous_labels = label_rows(previous_windows, StateLabelingConfig()); train_windows = canonical_windows + previous_windows; train_labels = canonical_labels + previous_labels
    canonical_ids = {int(row["match_id"]) for row in previous_targets if row["cohort"] == "canonical_v1"}; previous_ids = {int(row["match_id"]) for row in previous_targets if row["cohort"] != "canonical_v1"}; confirmation_ids = {int(row["match_id"]) for row in confirmation_targets}
    history_canonical = _match_rows(canonical_windows, mapping); history_previous = _match_rows(previous_windows, mapping); history_all = history_canonical + history_previous
    previous_matches = _match_rows(previous_windows, mapping); confirmation_matches = _match_rows(confirmation_windows, mapping)
    previous_priors = {int(row["match_id"]): _dynamic_prior(row, history_canonical, active) for row in previous_matches}; confirmation_priors = {int(row["match_id"]): _dynamic_prior(row, history_all, active) for row in confirmation_matches}
    all_previous_targets = previous_targets; all_confirmation_targets = previous_targets + confirmation_targets
    calibration = _block(canonical_windows, canonical_labels, all_previous_targets, previous_priors, canonical_ids, previous_ids, active)
    confirmation = _block(train_windows + confirmation_windows, train_labels + label_rows(confirmation_windows, StateLabelingConfig()), all_confirmation_targets, confirmation_priors, canonical_ids | previous_ids, confirmation_ids, active)
    supported = [name for name in TARGETS if confirmation["metrics"][name]["support"]["support_sufficient"]]; confirmed = [name for name in supported if confirmation["bootstrap_results"][name]["improvement_confirmed"]]
    classification = "promising_unconfirmed" if confirmed else "rejected_for_revision"
    audit = {"classification": classification, "database": database, "fit_scope_calibration": "canonical_v1_only", "fit_scope_confirmation": "canonical_v1_plus_phase09", "prior_semantics": "rolling_venue_aware_pre_kickoff", "hyperparameter_selection_scope": "frozen_90_day_half_life_2_match_shrinkage", "target_outcomes_used_as_features": False, "calibration_prediction_coverage": len(calibration["predictions"]) == len(previous_ids), "confirmation_prediction_coverage": len(confirmation["predictions"]) == len(confirmation_ids), "train_confirmation_overlap": sorted((canonical_ids | previous_ids) & confirmation_ids), "targets_with_sufficient_support": supported, "targets_with_confirmed_improvement": confirmed, "markets_promoted": False}
    manifest = {"canonical_windows_hash": _hash_file(CANONICAL_WINDOWS), "canonical_labels_hash": _hash_file(CANONICAL_LABELS), "phase09_windows_hash": _hash_file(PHASE09_WINDOWS), "phase09_targets_hash": _hash_file(PHASE09_TARGETS), "phase12_windows_hash": _hash_file(PHASE12_WINDOWS), "phase12_targets_hash": _hash_file(PHASE12_TARGETS), "target_spec_hash": _hash_file(SPEC), "database_mapping_rows": database["team_rows"]}
    coverage = {"canonical_train_matches": len(canonical_ids), "phase09_calibration_matches": len(previous_ids), "phase12_confirmation_matches": len(confirmation_ids), "train_windows_for_confirmation": len(train_windows), "confirmation_windows": len(confirmation_windows), "previous_labels_generated": len(previous_labels)}
    validation = f"# Validation report — Fase 14\n\n- calibración temporal: `{len(previous_ids)}` partidos\n- confirmación: `{len(confirmation_ids)}` partidos\n- targets con soporte: `{supported}`\n- mejoras confirmadas: `{confirmed}`\n- priors: forma venue-aware con half-life de `{active.half_life_days}` días y shrinkage `{active.shrinkage_matches}`."
    final = ["# Fase 14 — recalibración dinámica de Markov", "", f"**Clasificación:** `{classification}`", "", f"- calibración: `{len(previous_ids)}` partidos", f"- confirmación: `{len(confirmation_ids)}` partidos", f"- train Markov confirmatorio: `{len(canonical_ids | previous_ids)}` partidos", f"- targets con soporte suficiente: `{supported}`"]
    for name in TARGETS:
        item = confirmation["metrics"][name]; ci = confirmation["bootstrap_results"][name]["ci_95"]; final.append(f"- `{name}`: Markov `{item['model_log_loss']:.6f}`, baseline `{item['baseline_log_loss']:.6f}`, IC mejora `{ci}`")
    final.extend(["", "Mercados promovidos: `False`.", "Siguiente paso: si no hay mejora confirmada, revisar la definición de estado/eventos antes de volver a cargar datos."])
    result = {"config": asdict(active), "input_manifest": manifest, "coverage": coverage, "calibration": calibration, "confirmation": confirmation, "audit": audit, "validation_report": validation, "final_report": "\n".join(final)}
    _publish(result); LOGGER.info("Fase 14 recalibración dinámica: %s", classification); return result


# Version: 1.0.0
# Created: 2026-07-26
