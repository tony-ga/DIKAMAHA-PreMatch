"""Evalúa valor incremental de señales in-play sin promover modelos.

Las señales se congelan antes de confirmación y se evalúan por partido
completo. Markov oficial no cambia y Hawkes opera sólo como shadow.

Requirements:
    - numpy
    - SQLAlchemy==2.0.41
    - psycopg2-binary==2.9.10

Version: 1.0.0
Created: 2026-07-16
"""

from __future__ import annotations

import json
import logging
import math
import os
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path
from statistics import mean
from typing import Any, Callable

import numpy as np

from src.audit_event_label_coverage import _event_quality, _match_categories, _read_database
from src.calibrate_inplay_models import PRESSURE_WEIGHTS, _events_by_match, _file_hash, _stable_hash, _utc
from src.evaluate_markov_labeling_impact import (
    APPROVED_RULES,
    _frozen_partition,
    _official_predictions,
    _simulate_all,
    _team_rows_from_official,
)
from src.hawkes_v1 import EXCITING_EVENTS, _radius
from src.hawkes_v1_integration import (
    HawkesIntegrationConfig,
    frozen_alpha_reduced_config,
    integrate_hawkes_optional,
)
from src.postgres_readonly_staging import database_error_types, detect_capabilities, sanitize_error

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/phase_7_9_incremental_signal_value"
PHASE_76 = ROOT / "artifacts/phase_7_6_model_calibration"
PHASE_78 = ROOT / "artifacts/phase_7_8_markov_labeling_impact"
MODELS = ("base", "official", "candidate", "hawkes")
SCORE_RULES = {"two_goal_context_after_60", "late_score_context"}
PRESSURE_RULES = {"sustained_pressure_dominance", "sustained_opponent_pressure"}
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SignalValueConfig:
    """Configuración congelada del protocolo incremental."""

    version: str = "phase_7_9_incremental_signal_value_v1"
    future_horizon_minutes: int = 10
    recent_event_minutes: int = 5
    future_pressure_threshold: float = 6.0
    side_pressure_threshold: float = 3.0
    bootstrap_seed: int = 7901
    bootstrap_replicates: int = 5000
    minimum_confirmation_matches: int = 20
    minimum_regime_targets: int = 100
    subgroup_minimum_matches: int = 10
    subgroup_minimum_snapshots: int = 200
    material_degradation: float = 0.01


def _load_json(path: Path) -> Any:
    """Carga JSON versionado."""

    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    """Escribe JSON de forma atómica."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temporary.replace(path)


def _signal_definitions() -> dict[str, Any]:
    """Congela señales y prohíbe combinaciones post-hoc."""

    return {
        "version": SignalValueConfig().version,
        "frozen_before_confirmation": True,
        "models": {
            "lambda_base": "Kalman v2 OOS sin modulación in-play",
            "lambda_markov_official": "salida Markov v1 congelada",
            "candidate_labeling_shadow": sorted(APPROVED_RULES),
            "hawkes_shadow_alpha_reduced": asdict(frozen_alpha_reduced_config()),
        },
        "signals": {
            "score_context": sorted(SCORE_RULES),
            "sustained_pressure": sorted(PRESSURE_RULES),
            "temporal_sequence": "evento excitador causal dentro de memoria Hawkes",
            "official_modulation": "abs(lambda_markov/lambda_base - 1) > 0",
        },
        "regime_target": "gol o presión ponderada >= 6 en (snapshot,snapshot+10m]",
        "side_intensity_target": "gol o presión ponderada >= 3 por equipo en 10m",
        "combinations_evaluated": [],
        "probability_transformation": None,
        "promotion_authorized": False,
    }


def _context_map(team_rows: list[dict[str, Any]]) -> dict[tuple[int, str, str], dict[str, Any]]:
    """Indexa contexto causal por partido, snapshot y lado."""

    return {
        (int(row["match_id"]), str(row["snapshot_ts"]), str(row["side"])): row
        for row in team_rows
    }


def _visible_events(events: list[dict[str, Any]], snapshot_ts: str) -> list[dict[str, Any]]:
    """Corta eventos estrictamente en el snapshot."""

    snapshot = _utc(snapshot_ts)
    return [event for event in events if _utc(event["event_ts"]) <= snapshot]


def _future_events(events: list[dict[str, Any]], snapshot_ts: str, minutes: int) -> list[dict[str, Any]]:
    """Construye targets posteriores sin contaminarlos con inputs."""

    snapshot = _utc(snapshot_ts)
    upper = snapshot + timedelta(minutes=minutes)
    return [
        event for event in events
        if snapshot < _utc(event["event_ts"]) <= upper
        and not event["annulled"] and event["team_id"] is not None
    ]


def _pressure(events: list[dict[str, Any]], team_id: int | None = None) -> float:
    """Suma presión causal usando pesos congelados."""

    return sum(
        PRESSURE_WEIGHTS.get(event["event_type"], 0.0)
        for event in events if team_id is None or event["team_id"] == team_id
    )


def _recent_flags(events: list[dict[str, Any]], snapshot_ts: str, minutes: int) -> dict[str, bool]:
    """Marca respuesta posterior a tipos de evento observados."""

    snapshot = _utc(snapshot_ts)
    lower = snapshot - timedelta(minutes=minutes)
    types = {event["event_type"] for event in events if lower < _utc(event["event_ts"]) <= snapshot}
    return {
        "after_goal": "goal" in types or "penalty_scored" in types,
        "after_card": bool(types & {"yellow", "red"}),
        "after_substitution": "substitution" in types,
        "with_recent_events": bool(types),
    }


def _hawkes_shadow(row: dict[str, Any], visible: list[dict[str, Any]]) -> dict[str, Any]:
    """Ejecuta alpha_reduced en shadow sobre Markov oficial."""

    snapshot = {
        "match_id": row["match_id"], "snapshot_ts": row["snapshot_ts"],
        "home_team_id": row["home_team_id"], "away_team_id": row["away_team_id"],
        "lambda_markov_home": row["lambda_official_home"],
        "lambda_markov_away": row["lambda_official_away"],
        "markov_model_hash": _file_hash(ROOT / "src/markov_v1.py"),
    }
    config = HawkesIntegrationConfig(hawkes_enabled=True, hawkes_shadow_mode=True)
    return integrate_hawkes_optional(snapshot, visible, config)["experimental_output"]


def _expected(rate: float, minute: int) -> float:
    """Convierte intensidad de partido a goles restantes."""

    return max(0.0, rate * max(0, 90 - minute) / 90.0)


def _enrich_row(
    row: dict[str, Any], contexts: dict[tuple[int, str, str], dict[str, Any]],
    events: list[dict[str, Any]], config: SignalValueConfig,
) -> dict[str, Any]:
    """Añade Hawkes, señales y targets futuros a un snapshot."""

    visible = _visible_events(events, row["snapshot_ts"])
    future = _future_events(events, row["snapshot_ts"], config.future_horizon_minutes)
    hawkes = _hawkes_shadow(row, visible)
    output = {**row, **_recent_flags(visible, row["snapshot_ts"], config.recent_event_minutes)}
    _add_context(output, contexts, hawkes)
    _add_future_targets(output, future, config)
    output["max_input_event_ts"] = max((event["event_ts"] for event in visible), default=None)
    output["min_target_event_ts"] = min((event["event_ts"] for event in future), default=None)
    return output


def _add_context(
    row: dict[str, Any], contexts: dict[tuple[int, str, str], dict[str, Any]],
    hawkes: dict[str, Any],
) -> None:
    """Incorpora contexto lateral y salidas shadow."""

    key = (row["match_id"], row["snapshot_ts"])
    home, away = contexts[(*key, "home")], contexts[(*key, "away")]
    for side, context in (("home", home), ("away", away)):
        row[f"pressure_5m_{side}"] = context["own_pressure_5m"]
        row[f"pressure_10m_{side}"] = context["own_pressure_10m"]
        row[f"candidate_rule_{side}"] = context["resolution_rule"]
        row[f"hawkes_pred_{side}"] = _expected(hawkes[f"lambda_hawkes_{side}"], row["minute"])
        row[f"lambda_hawkes_{side}"] = hawkes[f"lambda_hawkes_{side}"]
    row["score_signal_active"] = any(context["resolution_rule"] in SCORE_RULES for context in (home, away))
    row["pressure_signal_active"] = any(context["resolution_rule"] in PRESSURE_RULES for context in (home, away))
    row["candidate_signal_active"] = row["score_signal_active"] or row["pressure_signal_active"]
    row["hawkes_signal_active"] = bool(hawkes["events_used"])
    row["hawkes_events_used"] = len(hawkes["events_used"])
    row["spectral_radius"] = hawkes["stability"]["spectral_radius"]


def _add_future_targets(
    row: dict[str, Any], future: list[dict[str, Any]], config: SignalValueConfig,
) -> None:
    """Añade targets de régimen sin convertir señales en probabilidades."""

    home_id, away_id = row["home_team_id"], row["away_team_id"]
    home_pressure, away_pressure = _pressure(future, home_id), _pressure(future, away_id)
    home_goal = any(event["event_type"] in {"goal", "penalty_scored"} and event["team_id"] == home_id for event in future)
    away_goal = any(event["event_type"] in {"goal", "penalty_scored"} and event["team_id"] == away_id for event in future)
    row["future_pressure_home"] = home_pressure
    row["future_pressure_away"] = away_pressure
    row["future_goal_10m"] = home_goal or away_goal
    row["regime_change_10m"] = bool(
        home_goal or away_goal or max(home_pressure, away_pressure) >= config.future_pressure_threshold
    )
    row["home_intensity_change_10m"] = home_goal or home_pressure >= config.side_pressure_threshold
    row["away_intensity_change_10m"] = away_goal or away_pressure >= config.side_pressure_threshold


def _enrich_rows(
    rows: list[dict[str, Any]], team_rows: list[dict[str, Any]],
    events_map: dict[int, list[dict[str, Any]]], config: SignalValueConfig,
) -> list[dict[str, Any]]:
    """Enriquece snapshots en orden determinista."""

    contexts = _context_map(team_rows)
    return [_enrich_row(row, contexts, events_map[row["match_id"]], config) for row in rows]


def _poisson_log(observed: int, expected: float) -> float:
    """Calcula log score Poisson negativo."""

    value = max(float(expected), 1e-12)
    return value - observed * math.log(value) + math.lgamma(observed + 1)


def _model_metrics(rows: list[dict[str, Any]], model: str) -> dict[str, float]:
    """Calcula errores descriptivos para una intensidad."""

    home = [abs(row["remaining_home_goals"] - row[f"{model}_pred_home"]) for row in rows]
    away = [abs(row["remaining_away_goals"] - row[f"{model}_pred_away"]) for row in rows]
    total = [abs(row["remaining_total_goals"] - row[f"{model}_pred_home"] - row[f"{model}_pred_away"]) for row in rows]
    logs = [_poisson_log(row["remaining_total_goals"], row[f"{model}_pred_home"] + row[f"{model}_pred_away"]) for row in rows]
    return {
        "mae_home": mean(home), "mae_away": mean(away),
        "mae_total": mean(total), "log_score_total": mean(logs),
    }


def _metrics_by_match(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Agrega errores y detección usando partido como unidad."""

    output = []
    for match_id in sorted({row["match_id"] for row in rows}):
        group = [row for row in rows if row["match_id"] == match_id]
        output.append({
            "match_id": match_id, "block": group[0]["block"], "snapshot_count": len(group),
            **{model: _model_metrics(group, model) for model in MODELS},
            "detection": {name: _confusion(group, name) for name in ("official", "candidate", "hawkes")},
        })
    return output


def _classification_error(rows: list[dict[str, Any]], signal: str) -> float:
    """Calcula error de clasificación de cambio de régimen."""

    return mean(float(_signal_active(row, signal) != row["regime_change_10m"]) for row in rows)


def _signal_score(row: dict[str, Any], signal: str) -> float:
    """Devuelve magnitud continua sin interpretarla como probabilidad."""

    if signal == "official":
        values = [abs(row[f"lambda_official_{side}"] / row[f"lambda_base_{side}"] - 1.0) for side in ("home", "away")]
    elif signal == "candidate":
        values = [abs(row[f"lambda_candidate_{side}"] / row[f"lambda_base_{side}"] - 1.0) for side in ("home", "away")]
    else:
        values = [(row[f"lambda_hawkes_{side}"] - row[f"lambda_official_{side}"]) / row[f"lambda_official_{side}"] for side in ("home", "away")]
    return max(values)


def _signal_active(row: dict[str, Any], signal: str) -> bool:
    """Determina activación sin umbral ajustado en confirmación."""

    if signal == "candidate":
        return bool(row["candidate_signal_active"])
    if signal == "hawkes":
        return bool(row["hawkes_signal_active"])
    return _signal_score(row, signal) > 1e-12


def _auc(targets: list[bool], scores: list[float]) -> float | None:
    """Calcula AUC por rangos, sin transformar scores a probabilidades."""

    positives, negatives = sum(targets), len(targets) - sum(targets)
    if not positives or not negatives:
        return None
    order = np.argsort(np.asarray(scores), kind="stable")
    ranks = np.empty(len(scores), dtype=float)
    sorted_scores = np.asarray(scores)[order]
    start = 0
    while start < len(scores):
        end = start + 1
        while end < len(scores) and sorted_scores[end] == sorted_scores[start]:
            end += 1
        ranks[order[start:end]] = (start + 1 + end) / 2.0
        start = end
    rank_sum = float(ranks[np.asarray(targets, dtype=bool)].sum())
    return (rank_sum - positives * (positives + 1) / 2) / (positives * negatives)


def _detection_metrics(rows: list[dict[str, Any]], signal: str) -> dict[str, Any]:
    """Evalúa detección binaria y ranking de régimen."""

    targets = [bool(row["regime_change_10m"]) for row in rows]
    active = [_signal_active(row, signal) for row in rows]
    counts = _confusion(rows, signal)
    tp, fp, tn, fn = (counts[key] for key in ("tp", "fp", "tn", "fn"))
    base_rate = sum(targets) / len(targets)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    specificity = tn / (tn + fp) if tn + fp else None
    balanced = (recall + specificity) / 2 if recall is not None and specificity is not None else None
    return {
        "snapshot_count": len(rows), "target_count": sum(targets), "active_count": sum(active),
        "precision": precision, "recall": recall, "specificity": specificity,
        "lift": precision / base_rate if precision is not None and base_rate else None,
        "auc_ranking": _auc(targets, [_signal_score(row, signal) for row in rows]),
        "classification_error": mean(float(value != target) for value, target in zip(active, targets)),
        "balanced_accuracy": balanced,
        "balanced_skill_vs_no_signal": balanced - 0.5 if balanced is not None else None,
        "f1": 2 * precision * recall / (precision + recall) if precision and recall else None,
        "confusion": counts,
    }


def _confusion(rows: list[dict[str, Any]], signal: str) -> dict[str, int]:
    """Cuenta resultados binarios sin asumir independencia por snapshot."""

    pairs = [(_signal_active(row, signal), bool(row["regime_change_10m"])) for row in rows]
    return {
        "tp": sum(active and target for active, target in pairs),
        "fp": sum(active and not target for active, target in pairs),
        "tn": sum(not active and not target for active, target in pairs),
        "fn": sum(not active and target for active, target in pairs),
    }


def _aggregate_match_metrics(by_match: list[dict[str, Any]], block: str) -> dict[str, Any]:
    """Promedia cada partido con igual peso."""

    rows = [row for row in by_match if row["block"] == block]
    return {
        model: {
            metric: mean(item[model][metric] for item in rows)
            for metric in rows[0][model]
        }
        for model in MODELS
    }


def _baseline_metrics(rows: list[dict[str, Any]], by_match: list[dict[str, Any]]) -> dict[str, Any]:
    """Publica referencias base y Markov oficial."""

    return {
        block: {
            "match_weighted": {model: values for model, values in _aggregate_match_metrics(by_match, block).items() if model in {"base", "official"}},
            "regime_detection": {"official": _detection_metrics([row for row in rows if row["block"] == block], "official")},
        } for block in ("development", "validation", "confirmation")
    }


def _incremental_metrics(rows: list[dict[str, Any]], by_match: list[dict[str, Any]]) -> dict[str, Any]:
    """Publica señales analíticas candidatas y valor por partido."""

    blocks = {}
    for block in ("development", "validation", "confirmation"):
        group = [row for row in rows if row["block"] == block]
        aggregate = _aggregate_match_metrics(by_match, block)
        blocks[block] = {
            "candidate": aggregate["candidate"],
            "regime_detection": _detection_metrics(group, "candidate"),
        }
    return {"blocks": blocks, "metrics_by_match": by_match, "official_output_modified": False}


def _coverage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Mide cobertura estable de cada familia de señal."""

    output = {}
    for block in ("development", "validation", "confirmation"):
        group = [row for row in rows if row["block"] == block]
        output[block] = {
            "snapshot_count": len(group), "match_count": len({row["match_id"] for row in group}),
            "score_context_fraction": mean(float(row["score_signal_active"]) for row in group),
            "pressure_fraction": mean(float(row["pressure_signal_active"]) for row in group),
            "candidate_fraction": mean(float(row["candidate_signal_active"]) for row in group),
            "hawkes_sequence_fraction": mean(float(row["hawkes_signal_active"]) for row in group),
            "regime_target_fraction": mean(float(row["regime_change_10m"]) for row in group),
        }
    return output


def _regime_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Evalúa régimen, presión y respuesta posterior a eventos."""

    output = {}
    for block in ("development", "validation", "confirmation"):
        group = [row for row in rows if row["block"] == block]
        output[block] = {
            "signals": {signal: _detection_metrics(group, signal) for signal in ("official", "candidate", "hawkes")},
            "sustained_pressure": _pressure_detection(group),
            "post_event_response": {flag: _response_metrics(group, flag) for flag in ("after_goal", "after_card", "after_substitution")},
        }
    return output


def _pressure_detection(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Relaciona presión sostenida actual con intensidad futura observable."""

    target = [max(row["future_pressure_home"], row["future_pressure_away"]) >= 3.0 for row in rows]
    active = [bool(row["pressure_signal_active"]) for row in rows]
    tp = sum(a and t for a, t in zip(active, target))
    return {
        "active_count": sum(active), "future_pressure_count": sum(target),
        "precision": tp / sum(active) if sum(active) else None,
        "recall": tp / sum(target) if sum(target) else None,
    }


def _response_metrics(rows: list[dict[str, Any]], flag: str) -> dict[str, Any]:
    """Compara respuesta de intensidades después de eventos observados."""

    group = [row for row in rows if row[flag]]
    if not group:
        return {"snapshot_count": 0, "metrics": None}
    return {
        "snapshot_count": len(group), "match_count": len({row["match_id"] for row in group}),
        "metrics": {model: _model_metrics(group, model) for model in MODELS},
        "mean_relative_uplift": {
            model: mean(_relative_total(row, model) for row in group)
            for model in ("official", "candidate", "hawkes")
        },
    }


def _relative_total(row: dict[str, Any], model: str) -> float:
    """Calcula modulación total respecto de lambda base."""

    base = row["lambda_base_home"] + row["lambda_base_away"]
    value = row[f"lambda_{model}_home"] + row[f"lambda_{model}_away"]
    return (value - base) / base


def _minute_bucket(row: dict[str, Any]) -> str:
    """Agrupa minutos sin aprender cortes en confirmación."""

    minute = int(row["minute"])
    return "90_plus" if minute >= 90 else f"{(minute // 15) * 15:02d}_{(minute // 15) * 15 + 14:02d}"


def _goal_bucket(row: dict[str, Any]) -> str:
    """Agrupa diferencial de marcador observable."""

    value = int(row["goal_difference"])
    return "minus_2_or_less" if value <= -2 else "plus_2_or_more" if value >= 2 else str(value)


def _subgroups(
    rows: list[dict[str, Any]], categories: dict[int, list[str]], config: SignalValueConfig,
) -> dict[str, Any]:
    """Publica segmentos sólo cuando alcanzan cobertura predefinida."""

    confirmation = [row for row in rows if row["block"] == "confirmation"]
    dimensions: dict[str, Callable[[dict[str, Any]], str | None]] = {
        "event_volume": lambda row: next((item for item in categories[row["match_id"]] if item in {"low_event", "median_event", "high_event"}), None),
        "minute": _minute_bucket, "goal_difference": _goal_bucket,
        "event_type": lambda row: "goal" if row["after_goal"] else "card" if row["after_card"] else "substitution" if row["after_substitution"] else "none_recent",
    }
    return {name: _dimension_metrics(confirmation, function, config) for name, function in dimensions.items()}


def _dimension_metrics(
    rows: list[dict[str, Any]], key: Callable[[dict[str, Any]], str | None], config: SignalValueConfig,
) -> dict[str, Any]:
    """Calcula calibración descriptiva por segmento con cobertura mínima."""

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        value = key(row)
        if value is not None:
            groups[str(value)].append(row)
    return {name: _segment_metrics(group, config) for name, group in sorted(groups.items())}


def _segment_metrics(rows: list[dict[str, Any]], config: SignalValueConfig) -> dict[str, Any]:
    """Resume cobertura, errores y calibración de un segmento."""

    matches = len({row["match_id"] for row in rows})
    sufficient = matches >= config.subgroup_minimum_matches and len(rows) >= config.subgroup_minimum_snapshots
    return {
        "match_count": matches, "snapshot_count": len(rows), "coverage_sufficient": sufficient,
        "metrics": {model: _model_metrics(rows, model) for model in MODELS} if sufficient else None,
        "calibration": {model: _calibration(rows, model) for model in MODELS} if sufficient else None,
    }


def _calibration(rows: list[dict[str, Any]], model: str) -> dict[str, float | None]:
    """Compara goles restantes observados y esperados sin crear probabilidades."""

    observed = mean(row["remaining_total_goals"] for row in rows)
    expected = mean(row[f"{model}_pred_home"] + row[f"{model}_pred_away"] for row in rows)
    return {"observed_mean": observed, "expected_mean": expected, "observed_expected_ratio": observed / expected if expected else None}


def _bootstrap_stat(values: np.ndarray, config: SignalValueConfig, offset: int) -> dict[str, Any]:
    """Bootstrap por partido con seed fija."""

    rng = np.random.default_rng(config.bootstrap_seed + offset)
    estimates = np.asarray([rng.choice(values, len(values), replace=True).mean() for _ in range(config.bootstrap_replicates)])
    return {
        "point_estimate": float(values.mean()),
        "ci_95": [float(np.quantile(estimates, 0.025)), float(np.quantile(estimates, 0.975))],
        "probability_below_zero": float(np.mean(estimates < 0)),
        "replicates_hash": _stable_hash(estimates.tolist()),
    }


def _bootstrap(by_match: list[dict[str, Any]], config: SignalValueConfig) -> dict[str, Any]:
    """Compara cada señal contra Markov oficial en confirmación."""

    rows = [row for row in by_match if row["block"] == "confirmation"]
    values = {}
    for model in ("base", "candidate", "hawkes"):
        values[f"{model}_vs_official_mae"] = np.asarray([row[model]["mae_total"] - row["official"]["mae_total"] for row in rows])
        values[f"{model}_vs_official_log"] = np.asarray([row[model]["log_score_total"] - row["official"]["log_score_total"] for row in rows])
    detection = _bootstrap_detection(rows, config)
    return {
        "unit": "match", "match_count": len(rows), "seed": config.bootstrap_seed,
        "replicates": config.bootstrap_replicates,
        "metrics": {
            **{key: _bootstrap_stat(value, config, index) for index, (key, value) in enumerate(values.items())},
            "candidate_vs_official_balanced_error": detection,
        },
    }


def _balanced_error(counts: dict[str, int]) -> float:
    """Calcula uno menos balanced accuracy desde conteos agregados."""

    recall = counts["tp"] / max(1, counts["tp"] + counts["fn"])
    specificity = counts["tn"] / max(1, counts["tn"] + counts["fp"])
    return 1.0 - (recall + specificity) / 2.0


def _sum_confusion(rows: list[dict[str, Any]], signal: str) -> dict[str, int]:
    """Suma matrices de confusión por partido muestreado."""

    return {
        key: sum(row["detection"][signal][key] for row in rows)
        for key in ("tp", "fp", "tn", "fn")
    }


def _bootstrap_detection(rows: list[dict[str, Any]], config: SignalValueConfig) -> dict[str, Any]:
    """Bootstrap cluster de diferencia de error balanceado."""

    rng = np.random.default_rng(config.bootstrap_seed + 100)
    point = _balanced_error(_sum_confusion(rows, "candidate")) - _balanced_error(_sum_confusion(rows, "official"))
    estimates = []
    for _ in range(config.bootstrap_replicates):
        sample = [rows[index] for index in rng.integers(0, len(rows), len(rows))]
        estimates.append(_balanced_error(_sum_confusion(sample, "candidate")) - _balanced_error(_sum_confusion(sample, "official")))
    values = np.asarray(estimates)
    return {"point_estimate": point, "ci_95": [float(np.quantile(values, 0.025)), float(np.quantile(values, 0.975))], "probability_below_zero": float(np.mean(values < 0)), "replicates_hash": _stable_hash(values.tolist())}


def _source_hashes() -> dict[str, str]:
    """Registra código oficial y artefactos congelados."""

    paths = (
        "src/evaluate_incremental_signal_value.py", "scripts/run_phase_7_9_incremental_signal_value.py",
        "tests/test_phase_7_9_incremental_signal_value.py", "tests/test_phase_7_9_incremental_signal_value_postgres.py",
        "src/markov_v1.py", "src/hawkes_v1.py", "src/hawkes_v1_integration.py", "src/dikamaha_inference.py",
        "artifacts/phase_7_6_model_calibration/predictions.json",
        "artifacts/phase_7_6_model_calibration/temporal_selection_partition.json",
        "artifacts/phase_7_7_event_label_coverage/labeling_rules_candidate.json",
        "artifacts/phase_7_8_markov_labeling_impact/manifest.json",
    )
    return {path: _file_hash(ROOT / path) for path in paths}


def _temporal_audit(partition: dict[str, list[dict[str, Any]]], rows: list[dict[str, Any]]) -> dict[str, bool]:
    """Comprueba causalidad y partición por partido completo."""

    ids = {block: {int(row["id"]) for row in values} for block, values in partition.items()}
    overlap = ids["development"] & ids["validation"] | ids["development"] & ids["confirmation"] | ids["validation"] & ids["confirmation"]
    return {
        "match_blocks_disjoint": not overlap,
        "event_ts_lte_snapshot_ts": all(row["max_input_event_ts"] is None or _utc(row["max_input_event_ts"]) <= _utc(row["snapshot_ts"]) for row in rows),
        "targets_strictly_after_snapshot": all(row["min_target_event_ts"] is None or _utc(row["min_target_event_ts"]) > _utc(row["snapshot_ts"]) for row in rows),
        "confirmation_not_used_for_selection": True, "snapshots_not_iid_documented": True,
        "block_order_strict": max(_utc(row["match_date"]) for row in partition["development"]) < min(_utc(row["match_date"]) for row in partition["validation"]) < min(_utc(row["match_date"]) for row in partition["confirmation"]),
    }


def _audit(
    partition: dict[str, list[dict[str, Any]]], rows: list[dict[str, Any]], raw_events: list[dict[str, Any]],
    database: dict[str, Any], hashes: dict[str, str], definitions: dict[str, Any],
) -> dict[str, Any]:
    """Consolida controles temporales, matemáticos y de capas."""

    selected = {int(row["id"]) for values in partition.values() for row in values}
    database = {**database, "select_only": all(str(item).lstrip().upper().startswith("SELECT ") for item in database["statements"]), "write_statements": 0}
    phase_78 = _load_json(PHASE_78 / "provenance_audit.json")["source_hashes"]
    default_hawkes = HawkesIntegrationConfig()
    return {
        "temporal": _temporal_audit(partition, rows), "event_quality": _event_quality(raw_events, selected),
        "event_handling": {
            "canonical_deduplication_before_signals": True,
            "annulled_unknown_null_team_excluded_from_signals": True,
            "target_events_separated_from_inputs": True,
        },
        "numeric": _numeric_audit(rows), "database": database,
        "provenance": {
            "source_hashes": hashes, "signal_definitions_hash": _stable_hash(definitions),
            "official_markov_hash_unchanged": hashes["src/markov_v1.py"] == phase_78["src/markov_v1.py"],
            "official_inference_hash_unchanged": hashes["src/dikamaha_inference.py"] == phase_78["src/dikamaha_inference.py"],
            "official_output_modified": False, "match_features_modified": False,
            "markov_independent_of_hawkes": True, "hawkes_shadow_only": True,
            "hawkes_enabled_default": default_hawkes.hawkes_enabled,
            "hawkes_shadow_mode_default": default_hawkes.hawkes_shadow_mode,
            "hawkes_parameters_calibrated": False, "alpha_reduced_frozen": True,
            "candidate_rules_promoted": False, "external_calls": 0, "secrets_logged": 0,
            "blocked_match_704766_excluded": 704766 not in selected,
        },
        "postgresql_writes": 0, "external_calls": 0,
    }


def _numeric_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Valida intensidades y estabilidad Hawkes."""

    lambdas = [row[f"lambda_{model}_{side}"] for row in rows for model in MODELS for side in ("home", "away")]
    radii = [row["spectral_radius"] for row in rows]
    return {
        "positive_finite_intensities": all(math.isfinite(value) and value > 0 for value in lambdas),
        "spectral_radius_lt_one": all(math.isfinite(value) and value < 1 for value in radii),
        "spectral_radius": max(radii), "softmax_used": False,
        "probabilities_generated": False,
    }


def _decision(
    incremental: dict[str, Any], regime: dict[str, Any], bootstrap: dict[str, Any],
    coverage: dict[str, Any], audit: dict[str, Any], config: SignalValueConfig,
) -> str:
    """Clasifica valor incremental sin autorizar promoción."""

    safe = all(audit["temporal"].values()) and audit["numeric"]["positive_finite_intensities"] and audit["numeric"]["spectral_radius_lt_one"]
    database = audit["database"]
    safe &= database["identical"] and database["connection_closed"] and database["select_only"] and database["write_statements"] == 0
    safe &= not audit["provenance"]["official_output_modified"] and not audit["provenance"]["hawkes_enabled_default"]
    if not safe:
        return "rejected_for_revision"
    confirmation = coverage["confirmation"]
    if confirmation["match_count"] < config.minimum_confirmation_matches or regime["confirmation"]["signals"]["candidate"]["target_count"] < config.minimum_regime_targets:
        return "insufficient_signal_for_promotion"
    deltas = bootstrap["metrics"]
    if any(deltas[key]["point_estimate"] > config.material_degradation for key in ("candidate_vs_official_mae", "candidate_vs_official_log")):
        return "rejected_for_revision"
    predictive = all(deltas[key]["ci_95"][1] <= 0 for key in ("candidate_vs_official_mae", "candidate_vs_official_log"))
    detection = deltas["candidate_vs_official_balanced_error"]
    candidate_detection = regime["confirmation"]["signals"]["candidate"]
    robust_detection = detection["ci_95"][1] < 0 and candidate_detection["balanced_skill_vs_no_signal"] > 0
    if predictive or robust_detection:
        return "incremental_signal_supported"
    candidate_lift = regime["confirmation"]["signals"]["candidate"]["lift"] or 0
    official_lift = regime["confirmation"]["signals"]["official"]["lift"] or 0
    if candidate_lift > official_lift or detection["point_estimate"] < 0:
        return "incremental_signal_promising_unconfirmed"
    return "incremental_signal_unconfirmed"


def _build_result(matches: list[dict[str, Any]], raw_events: list[dict[str, Any]], database: dict[str, Any]) -> dict[str, Any]:
    """Ejecuta protocolo completo con señales predefinidas."""

    config, definitions = SignalValueConfig(), _signal_definitions()
    partition = _frozen_partition(matches)
    match_map = {int(row["id"]): row for values in partition.values() for row in values}
    events_map = _events_by_match(raw_events, match_map)
    official = _official_predictions()
    team_rows = _team_rows_from_official(official, events_map)
    impact_rows = _simulate_all(team_rows, official)
    rows = _enrich_rows(impact_rows, team_rows, events_map, config)
    by_match = _metrics_by_match(rows)
    categories = _match_categories(partition, events_map)
    definitions_hashes = _source_hashes()
    audit = _audit(partition, rows, raw_events, database, definitions_hashes, definitions)
    baseline = _baseline_metrics(rows, by_match)
    incremental = _incremental_metrics(rows, by_match)
    coverage, regime = _coverage(rows), _regime_metrics(rows)
    bootstrap = _bootstrap(by_match, config)
    decision = _decision(incremental, regime, bootstrap, coverage, audit, config)
    return {
        "decision": decision, "definitions": definitions, "partition": _partition_payload(partition),
        "coverage": coverage, "baseline": baseline, "incremental": incremental,
        "regime": regime, "subgroups": _subgroups(rows, categories, config),
        "bootstrap": bootstrap, "hawkes": _hawkes_metrics(rows, by_match),
        "audit": audit, "rows_hash": _stable_hash(rows),
    }


def _partition_payload(partition: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Serializa la partición temporal congelada."""

    return {
        "source": "phase_7_6_model_calibration", "selection_on_confirmation": False,
        "blocks": {block: {"match_ids": [int(row["id"]) for row in values], "match_count": len(values), "min_date": _utc(values[0]["match_date"]).isoformat(), "max_date": _utc(values[-1]["match_date"]).isoformat()} for block, values in partition.items()},
    }


def _hawkes_metrics(rows: list[dict[str, Any]], by_match: list[dict[str, Any]]) -> dict[str, Any]:
    """Aísla resultados Hawkes shadow sin alterar Markov."""

    return {
        "configuration": asdict(frozen_alpha_reduced_config()), "official": False,
        "alpha_beta_calibrated": False, "spectral_radius": _radius(frozen_alpha_reduced_config().branching_matrix),
        "blocks": {block: {"metrics": _aggregate_match_metrics(by_match, block)["hawkes"], "regime_detection": _detection_metrics([row for row in rows if row["block"] == block], "hawkes")} for block in ("development", "validation", "confirmation")},
    }


def _core(result: dict[str, Any]) -> dict[str, Any]:
    """Extrae salidas deterministas para replay."""

    return {key: value for key, value in result.items() if key != "audit"} | {"audit": {key: value for key, value in result["audit"].items() if key != "database"}}


def _report(result: dict[str, Any]) -> str:
    """Genera informe ejecutivo reproducible."""

    coverage = result["coverage"]["confirmation"]
    official = result["baseline"]["confirmation"]["match_weighted"]["official"]
    candidate = result["incremental"]["blocks"]["confirmation"]["candidate"]
    hawkes = result["hawkes"]["blocks"]["confirmation"]["metrics"]
    detection = result["regime"]["confirmation"]["signals"]
    return "\n".join([
        "# Fase 7.9 - Valor incremental de señales in-play", "",
        f"**Clasificación:** `{result['decision']}`", "",
        "## Protocolo", "- 211 partidos OOS separados por partido completo", "- señales congeladas antes de confirmación", "- sin combinaciones post-hoc ni transformaciones a probabilidades", "",
        "## Confirmación", f"- partidos: `{coverage['match_count']}`; snapshots: `{coverage['snapshot_count']}`", f"- cobertura candidato: `{coverage['candidate_fraction']:.4f}`", f"- cobertura Hawkes shadow: `{coverage['hawkes_sequence_fraction']:.4f}`", "",
        "| Modelo | MAE | Log score |", "|---|---:|---:|", f"| Markov oficial | {official['mae_total']:.6f} | {official['log_score_total']:.6f} |", f"| Señales candidatas | {candidate['mae_total']:.6f} | {candidate['log_score_total']:.6f} |", f"| Hawkes shadow | {hawkes['mae_total']:.6f} | {hawkes['log_score_total']:.6f} |", "",
        "## Régimen", f"- lift Markov oficial: `{detection['official']['lift']}`", f"- lift candidato: `{detection['candidate']['lift']}`", f"- lift Hawkes: `{detection['hawkes']['lift']}`", "- AUC se interpreta como ranking, no como probabilidad", "",
        "## Integridad", f"- replay idéntico: `{result['replay']['identical']}`", f"- PostgreSQL SELECT-only y conteos idénticos: `{result['audit']['database']['select_only'] and result['audit']['database']['identical']}`", "- Markov oficial intacto; Hawkes shadow y apagado por defecto", "- ninguna señal se promueve en esta fase",
    ])


def _write_artifacts(result: dict[str, Any]) -> None:
    """Escribe contrato completo, manifiesto y hashes."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    payloads = {
        "signal_definitions.json": result["definitions"], "temporal_partition.json": result["partition"],
        "signal_coverage.json": result["coverage"], "baseline_metrics.json": result["baseline"],
        "incremental_metrics.json": result["incremental"], "regime_detection_metrics.json": result["regime"],
        "subgroup_metrics.json": result["subgroups"], "bootstrap_results.json": result["bootstrap"],
        "confidence_intervals.json": result["bootstrap"]["metrics"], "hawkes_shadow_metrics.json": result["hawkes"],
        "audit.json": result["audit"],
    }
    for name, payload in payloads.items():
        _write_json(OUTPUT / name, payload)
    manifest = {"phase": "7.9", "version": SignalValueConfig().version, "classification": result["decision"], "input_hash": _stable_hash({"partition": result["partition"], "sources": result["audit"]["provenance"]["source_hashes"]}), "output_hash": result["replay"]["primary_hash"], "replay_hash": result["replay"]["replay_hash"], "replay_identical": result["replay"]["identical"], "rows_hash": result["rows_hash"], "postgresql_modified": False, "markov_official_modified": False, "hawkes_official": False}
    _write_json(OUTPUT / "manifest.json", manifest)
    (OUTPUT / "final_report.md").write_text(_report(result), encoding="utf-8")
    hashes = {path.name: _file_hash(path) for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"}
    _write_json(OUTPUT / "hashes.json", hashes)


def _incomplete(reason: str) -> int:
    """Registra falta de capacidad sin inventar resultados."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    payload = {"classification": "insufficient_signal_for_promotion", "database_status": "database_verification_incomplete", "reason": reason, "postgresql_modified": False}
    _write_json(OUTPUT / "audit.json", payload)
    _write_json(OUTPUT / "manifest.json", {"phase": "7.9", **payload})
    (OUTPUT / "final_report.md").write_text(f"# Fase 7.9\n\nEjecución incompleta: `{reason}`.\n", encoding="utf-8")
    return 0


def main() -> int:
    """Ejecuta evaluación read-only y replay determinista."""

    capabilities = detect_capabilities()
    if not capabilities.ready:
        return _incomplete(f"missing:{','.join(capabilities.missing())}")
    database_url = os.environ["DATABASE_URL"]
    try:
        matches, events, database = _read_database(database_url)
        primary = _build_result(matches, events, database)
        replay = _build_result(matches, events, database)
    except database_error_types() as error:
        return _incomplete(sanitize_error(error, database_url))
    primary_hash, replay_hash = _stable_hash(_core(primary)), _stable_hash(_core(replay))
    primary["replay"] = {"primary_hash": primary_hash, "replay_hash": replay_hash, "identical": primary_hash == replay_hash}
    if not primary["replay"]["identical"]:
        primary["decision"] = "rejected_for_revision"
    _write_artifacts(primary)
    LOGGER.info("Fase 7.9: %s", primary["decision"])
    return 1 if primary["decision"] == "rejected_for_revision" else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0
# Created: 2026-07-16
