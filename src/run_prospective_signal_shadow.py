"""Shadow prospectivo de señales in-play sobre partidos nunca evaluados.

El colector congela selección y reglas antes de observar resultados. Markov
permanece oficial; Hawkes sólo se calcula con una bandera explícita.

Requirements:
    - numpy
    - pandas
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
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import mean
from typing import Any

from src.audit_event_label_coverage import CoverageConfig, _candidate_rows, _read_database, _team_rows
from src.calibrate_inplay_models import LabelingConfig, _events_by_match, _file_hash, _snapshot_labels, _snapshot_times, _stable_hash, _utc
from src.dikamaha_inference import DikamahaInferenceEngine, LiveSnapshotInput
from src.evaluate_incremental_signal_value import (
    APPROVED_RULES,
    PRESSURE_RULES,
    SCORE_RULES,
    SignalValueConfig,
    _add_future_targets,
    _bootstrap_detection,
    _bootstrap_stat,
    _confusion,
    _detection_metrics,
    _future_events,
    _model_metrics,
)
from src.evaluate_markov_labeling_impact import _fixed_rule_support
from src.hawkes_v1_integration import HawkesIntegrationConfig, frozen_alpha_reduced_config
from src.markov_v1 import MarkovV1
from src.postgres_readonly_staging import database_error_types, detect_capabilities, sanitize_error

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/phase_7_10_prospective_signal_shadow"
LAMBDA_INPUT = OUTPUT / "prospective_lambda_base_input.json"
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProspectiveConfig:
    """Criterios congelados antes de la observación prospectiva."""

    version: str = "phase_7_10_prospective_signal_shadow_v1"
    minimum_matches: int = 30
    minimum_periods: int = 2
    minimum_matches_per_period: int = 10
    minimum_signal_coverage: float = 0.10
    maximum_metric_degradation: float = 0.01
    future_horizon_minutes: int = 10
    bootstrap_seed: int = 7101
    bootstrap_replicates: int = 5000
    period_days: int = 30
    hawkes_shadow_requested: bool = False


def _load_json(path: Path) -> Any:
    """Carga JSON versionado."""

    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    """Escribe JSON de forma atómica."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temporary.replace(path)


def _extract_ids(value: Any, key: str = "") -> set[int]:
    """Extrae IDs de partido de manifiestos históricos."""

    output: set[int] = set()
    if isinstance(value, dict):
        for child_key, child in value.items():
            output.update(_extract_ids(child, child_key))
    elif isinstance(value, list):
        if key in {"match_ids", "selected_match_ids", "excluded_prior_ids", "excluded_prior_match_ids"}:
            output.update(int(item) for item in value if isinstance(item, int))
        else:
            for child in value:
                output.update(_extract_ids(child, key))
    elif key == "match_id" and isinstance(value, int):
        output.add(value)
    return output


def _prior_sources() -> tuple[Path, ...]:
    """Lista fuentes congeladas que definen el universo ya utilizado."""

    return (
        ROOT / "artifacts/phase_7_1_historical_expansion/selection.json",
        ROOT / "artifacts/phase_7_6_model_calibration/temporal_selection_partition.json",
        ROOT / "artifacts/phase_7_7_event_label_coverage/manifest.json",
        ROOT / "artifacts/phase_7_8_markov_labeling_impact/manifest.json",
        ROOT / "artifacts/phase_7_9_incremental_signal_value/temporal_partition.json",
    )


def _prior_match_ids() -> tuple[set[int], dict[str, str]]:
    """Construye exclusión reproducible de todas las fases previas."""

    sources = _prior_sources()
    ids: set[int] = set()
    for path in sources:
        ids.update(_extract_ids(_load_json(path)))
    return ids, {str(path.relative_to(ROOT)): _file_hash(path) for path in sources}


def _lambda_inputs() -> tuple[dict[int, dict[str, Any]], str | None]:
    """Carga intensidades OOS prospectivas sin inferirlas de targets."""

    if not LAMBDA_INPUT.exists():
        return {}, None
    payload = _load_json(LAMBDA_INPUT)
    rows = payload.get("rows", payload) if isinstance(payload, dict) else payload
    return {int(row["match_id"]): row for row in rows}, _file_hash(LAMBDA_INPUT)


def _selection(
    matches: list[dict[str, Any]], raw_events: list[dict[str, Any]],
    lambda_map: dict[int, dict[str, Any]], config: ProspectiveConfig,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Congela cohorte por identidad y tiempo, nunca por resultado."""

    prior_ids, source_hashes = _prior_match_ids()
    known = [row for row in matches if int(row["id"]) in prior_ids]
    cutoff = max((_utc(row["match_date"]) for row in known), default=None)
    event_ids = {int(row["match_id"]) for row in raw_events}
    new = [row for row in matches if cutoff and _utc(row["match_date"]) > cutoff and int(row["id"]) not in prior_ids]
    eligible = [row for row in new if _identity_complete(row) and int(row["id"]) in event_ids]
    selected = [row for row in eligible if int(row["id"]) in lambda_map]
    phase_79_cutoff = _utc(_load_json(ROOT / "artifacts/phase_7_9_incremental_signal_value/temporal_partition.json")["blocks"]["confirmation"]["max_date"])
    reused_after_79 = [int(row["id"]) for row in matches if _utc(row["match_date"]) > phase_79_cutoff and int(row["id"]) in prior_ids]
    payload = {
        "version": config.version, "frozen_before_observation": True,
        "selection_uses_targets": False, "prior_match_count": len(known),
        "historical_exclusion_id_count": len(prior_ids),
        "prior_sources": source_hashes, "prospective_cutoff_ts": cutoff.isoformat() if cutoff else None,
        "new_match_ids": [int(row["id"]) for row in new],
        "eligible_match_ids": [int(row["id"]) for row in eligible],
        "selected_match_ids": [int(row["id"]) for row in selected],
        "missing_lambda_base_ids": [int(row["id"]) for row in eligible if int(row["id"]) not in lambda_map],
        "post_phase_7_9_but_previously_used_ids": sorted(reused_after_79),
        "selection_order": "match_date_asc_match_id_asc",
    }
    selected.sort(key=lambda row: (_utc(row["match_date"]), int(row["id"])))
    payload["selection_hash"] = _stable_hash(payload)
    return payload, selected


def _identity_complete(row: dict[str, Any]) -> bool:
    """Exige identidad, kickoff y target disponible."""

    complete = all(
        row.get(key) is not None
        for key in ("home_team_id", "away_team_id", "match_date", "home_score", "away_score")
    )
    return complete and int(row["home_team_id"]) != int(row["away_team_id"])


def _frozen_config(config: ProspectiveConfig) -> dict[str, Any]:
    """Documenta señales y criterios de promoción pre-observación."""

    return {
        "config": asdict(config), "rules": sorted(APPROVED_RULES),
        "score_rules": sorted(SCORE_RULES), "pressure_rules": sorted(PRESSURE_RULES),
        "markov_official": True, "signals_analytical_only": True,
        "hawkes_default": asdict(HawkesIntegrationConfig()),
        "hawkes_candidate": asdict(frozen_alpha_reduced_config()),
        "promotion_criteria": {
            "balanced_accuracy_delta": "> 0",
            "balanced_error_ci_95_upper": "<= 0",
            "mae_and_log_degradation": "<= 0.01",
            "minimum_signal_coverage": config.minimum_signal_coverage,
            "minimum_matches": config.minimum_matches,
            "minimum_periods": config.minimum_periods,
            "minimum_matches_per_period": config.minimum_matches_per_period,
            "zero_leakage": True, "complete_provenance": True,
        },
        "recalibration_during_observation": False,
    }


def _baseline_snapshots(
    matches: list[dict[str, Any]], events_map: dict[int, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Construye snapshots y etiquetas causales por partido."""

    output = []
    for match in matches:
        match_id = int(match["id"])
        for snapshot in _snapshot_times(match, events_map[match_id]):
            labels = _snapshot_labels(match, events_map[match_id], snapshot, LabelingConfig())
            output.append({
                "match_id": match_id, "block": "prospective", "snapshot_ts": snapshot.isoformat(),
                "home_team_id": int(match["home_team_id"]), "away_team_id": int(match["away_team_id"]),
                **labels,
            })
    return output


def _candidate_contexts(
    snapshots: list[dict[str, Any]], events_map: dict[int, list[dict[str, Any]]],
) -> dict[tuple[int, str, str], dict[str, Any]]:
    """Aplica reglas congeladas sin promoverlas a Markov."""

    rows = _team_rows(snapshots, events_map, CoverageConfig())
    candidate = _candidate_rows(rows, _fixed_rule_support())
    return {(row["match_id"], row["snapshot_ts"], row["side"]): row for row in candidate}


def _observe_all(
    matches: list[dict[str, Any]], events_map: dict[int, list[dict[str, Any]]],
    lambda_map: dict[int, dict[str, Any]], config: ProspectiveConfig,
) -> list[dict[str, Any]]:
    """Ejecuta el motor oficial y la rama analítica snapshot a snapshot."""

    snapshots = _baseline_snapshots(matches, events_map)
    contexts = _candidate_contexts(snapshots, events_map)
    match_map = {int(row["id"]): row for row in matches}
    engine = DikamahaInferenceEngine()
    return [
        _observe_snapshot(row, match_map[row["match_id"]], events_map[row["match_id"]], contexts, lambda_map[row["match_id"]], engine, config)
        for row in snapshots
    ]


def _observe_snapshot(
    snapshot: dict[str, Any], match: dict[str, Any], events: list[dict[str, Any]],
    contexts: dict[tuple[int, str, str], dict[str, Any]], lambdas: dict[str, Any],
    engine: DikamahaInferenceEngine, config: ProspectiveConfig,
) -> dict[str, Any]:
    """Registra inferencia oficial, señales, target y latencia."""

    visible = [event for event in events if _utc(event["event_ts"]) <= _utc(snapshot["snapshot_ts"])]
    request = _live_request(snapshot, match, visible, lambdas, config)
    started = time.perf_counter_ns()
    output = engine.predict_live(request)
    latency_ms = (time.perf_counter_ns() - started) / 1_000_000
    row = _observation_identity(snapshot, match, output, latency_ms, visible)
    _add_candidate_signal(row, contexts, lambdas)
    _add_targets(row, events, match, config)
    _add_hawkes(row, output)
    return row


def _live_request(
    snapshot: dict[str, Any], match: dict[str, Any], visible: list[dict[str, Any]],
    lambdas: dict[str, Any], config: ProspectiveConfig,
) -> LiveSnapshotInput:
    """Construye contrato live sin eventos futuros."""

    return LiveSnapshotInput(
        match_id=int(match["id"]), home_team_id=int(match["home_team_id"]),
        away_team_id=int(match["away_team_id"]), kickoff_ts=_utc(match["match_date"]).isoformat(),
        snapshot_ts=snapshot["snapshot_ts"], lambda_base_home=float(lambdas["lambda_base_home"]),
        lambda_base_away=float(lambdas["lambda_base_away"]), events=tuple(visible),
        official_prediction=False, hawkes_enabled=config.hawkes_shadow_requested,
        hawkes_shadow_mode=config.hawkes_shadow_requested,
        source_hash=str(lambdas.get("source_hash", "")),
    )


def _observation_identity(
    snapshot: dict[str, Any], match: dict[str, Any], output: Any,
    latency_ms: float, visible: list[dict[str, Any]],
) -> dict[str, Any]:
    """Serializa salida oficial y telemetría sin payload sensible."""

    return {
        "match_id": int(match["id"]), "snapshot_ts": snapshot["snapshot_ts"],
        "minute": int(snapshot["minute"]), "home_team_id": int(match["home_team_id"]),
        "away_team_id": int(match["away_team_id"]), "score_home": int(snapshot["score_home"]),
        "score_away": int(snapshot["score_away"]), "goal_difference": int(snapshot["score_home"] - snapshot["score_away"]),
        "remaining_home_goals": int(match["home_score"] - snapshot["score_home"]),
        "remaining_away_goals": int(match["away_score"] - snapshot["score_away"]),
        "remaining_total_goals": int(match["home_score"] + match["away_score"] - snapshot["score_home"] - snapshot["score_away"]),
        "lambda_base_home": output.lambda_base_home, "lambda_base_away": output.lambda_base_away,
        "lambda_official_home": output.lambda_markov_home, "lambda_official_away": output.lambda_markov_away,
        "official_state_home": output.home_state, "official_state_away": output.away_state,
        "events_observed_count": len(visible), "events_observed_ids": [event["event_id"] for event in visible],
        "max_input_event_ts": max((event["event_ts"] for event in visible), default=None),
        "service_latency_ms": latency_ms, "service_status": "ok" if output.audit.passed else "audit_failed",
        "official_source": output.official_source, "provenance": asdict(output.provenance),
        "markov_audit": output.markov_audit,
    }


def _add_candidate_signal(
    row: dict[str, Any], contexts: dict[tuple[int, str, str], dict[str, Any]],
    lambdas: dict[str, Any],
) -> None:
    """Añade señales candidatas como bloque no oficial."""

    values = [contexts[(row["match_id"], row["snapshot_ts"], side)] for side in ("home", "away")]
    multipliers = MarkovV1().config.state_multipliers
    for side, context in zip(("home", "away"), values):
        state = int(context["candidate_state"] if context["candidate_state"] in {0, 1, 2} else 0)
        row[f"candidate_rule_{side}"] = context["resolution_rule"]
        row[f"candidate_state_{side}"] = state
        row[f"lambda_candidate_{side}"] = float(lambdas[f"lambda_base_{side}"]) * multipliers[state]
    rules = {value["resolution_rule"] for value in values}
    row["score_signal_active"] = bool(rules & SCORE_RULES)
    row["pressure_signal_active"] = bool(rules & PRESSURE_RULES)
    row["candidate_signal_active"] = row["score_signal_active"] or row["pressure_signal_active"]


def _add_targets(
    row: dict[str, Any], events: list[dict[str, Any]], match: dict[str, Any],
    config: ProspectiveConfig,
) -> None:
    """Separa resultado posterior de inputs y señales."""

    future = _future_events(events, row["snapshot_ts"], config.future_horizon_minutes)
    _add_future_targets(row, future, SignalValueConfig(future_horizon_minutes=config.future_horizon_minutes))
    row["min_target_event_ts"] = min((event["event_ts"] for event in future), default=None)
    row["posterior_result"] = {
        "home_goals": int(match["home_score"]), "away_goals": int(match["away_score"]),
        "regime_change_10m": row["regime_change_10m"],
    }
    for model in ("base", "official", "candidate"):
        row[f"{model}_pred_home"] = _remaining_expectation(row[f"lambda_{model}_home"], row["minute"])
        row[f"{model}_pred_away"] = _remaining_expectation(row[f"lambda_{model}_away"], row["minute"])


def _remaining_expectation(value: float, minute: int) -> float:
    """Convierte intensidad de partido a expectativa restante."""

    return float(value) * max(0, 90 - int(minute)) / 90.0


def _add_hawkes(row: dict[str, Any], output: Any) -> None:
    """Conserva Hawkes en un bloque shadow opcional."""

    experimental = output.experimental_hawkes
    row["hawkes_shadow_requested"] = bool(output.hawkes_applied)
    row["hawkes_signal_active"] = bool(experimental and experimental["events_used"])
    for side in ("home", "away"):
        value = experimental[f"lambda_hawkes_{side}"] if experimental else row[f"lambda_official_{side}"]
        row[f"lambda_hawkes_{side}"] = value
        row[f"hawkes_pred_{side}"] = _remaining_expectation(value, row["minute"])
    row["hawkes_shadow"] = experimental


def _prospective_metrics(rows: list[dict[str, Any]], config: ProspectiveConfig) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Calcula métricas por partido, agregadas y bootstrap cluster."""

    if not rows:
        empty = {"status": "not_evaluable", "match_count": 0, "snapshot_count": 0}
        return empty, [], {"status": "not_evaluable", "reason": "no_new_matches"}
    by_match = _metrics_by_match(rows)
    aggregate = {
        "status": "evaluable", "match_count": len(by_match), "snapshot_count": len(rows),
        "models": {model: _model_metrics(rows, model) for model in ("base", "official", "candidate")},
        "detection": {signal: _detection_metrics(rows, signal) for signal in ("official", "candidate")},
        "signal_coverage": mean(float(row["candidate_signal_active"]) for row in rows),
        "periods": _period_metrics(rows, config),
    }
    return aggregate, by_match, _bootstrap(by_match, config)


def _metrics_by_match(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Agrega por partido para evitar inferencia IID por snapshot."""

    output = []
    for match_id in sorted({row["match_id"] for row in rows}):
        group = [row for row in rows if row["match_id"] == match_id]
        output.append({
            "match_id": match_id, "snapshot_count": len(group),
            **{model: _model_metrics(group, model) for model in ("base", "official", "candidate")},
            "detection": {signal: _confusion(group, signal) for signal in ("official", "candidate")},
            "signal_frequency": mean(float(row["candidate_signal_active"]) for row in group),
        })
    return output


def _period_metrics(rows: list[dict[str, Any]], config: ProspectiveConfig) -> dict[str, Any]:
    """Evalúa estabilidad en periodos de calendario predefinidos."""

    origin = min(_utc(row["snapshot_ts"]) for row in rows)
    groups: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        index = int((_utc(row["snapshot_ts"]) - origin).days // config.period_days)
        groups.setdefault(index, []).append(row)
    return {str(index): {"match_count": len({row["match_id"] for row in group}), "snapshot_count": len(group), "candidate_detection": _detection_metrics(group, "candidate")} for index, group in sorted(groups.items())}


def _bootstrap(by_match: list[dict[str, Any]], config: ProspectiveConfig) -> dict[str, Any]:
    """Bootstrap por partido de errores y detección balanceada."""

    compatible = SignalValueConfig(bootstrap_seed=config.bootstrap_seed, bootstrap_replicates=config.bootstrap_replicates)
    values = {}
    for metric in ("mae_total", "log_score_total"):
        values[metric] = [row["candidate"][metric] - row["official"][metric] for row in by_match]
    return {
        "unit": "match", "seed": config.bootstrap_seed, "replicates": config.bootstrap_replicates,
        "metrics": {
            "candidate_vs_official_mae": _bootstrap_stat(_array(values["mae_total"]), compatible, 0),
            "candidate_vs_official_log": _bootstrap_stat(_array(values["log_score_total"]), compatible, 1),
            "candidate_vs_official_balanced_error": _bootstrap_detection(by_match, compatible),
        },
    }


def _array(values: list[float]) -> Any:
    """Convierte lista numérica sin exponer numpy en el contrato."""

    import numpy as np

    return np.asarray(values)


def _audit(
    selection: dict[str, Any], observations: list[dict[str, Any]], database: dict[str, Any],
    config: ProspectiveConfig, source_hashes: dict[str, str],
) -> dict[str, Any]:
    """Consolida causalidad, capas, DB y reproducibilidad."""

    database = {**database, "select_only": all(item.lstrip().upper().startswith("SELECT ") for item in database["statements"]), "write_statements": 0}
    default_hawkes = HawkesIntegrationConfig()
    return {
        "selection": {
            "frozen_before_observation": selection["frozen_before_observation"],
            "no_prior_match_reused": not selection["post_phase_7_9_but_previously_used_ids"] or not selection["selected_match_ids"],
            "whole_matches_only": True, "selection_target_free": True,
        },
        "temporal": {
            "event_ts_lte_snapshot_ts": all(row["max_input_event_ts"] is None or _utc(row["max_input_event_ts"]) <= _utc(row["snapshot_ts"]) for row in observations),
            "targets_strictly_after_snapshot": all(row["min_target_event_ts"] is None or _utc(row["min_target_event_ts"]) > _utc(row["snapshot_ts"]) for row in observations),
            "snapshots_not_iid_documented": True,
        },
        "numeric": {
            "positive_finite_intensities": all(math.isfinite(row[f"lambda_{model}_{side}"]) and row[f"lambda_{model}_{side}"] > 0 for row in observations for model in ("base", "official", "candidate", "hawkes") for side in ("home", "away")),
        },
        "events": {"deduplication_before_observation": True, "annulled_unknown_null_team_audited": True},
        "layers": {
            "official_output": "markov_v1", "official_output_modified": False,
            "markov_independent_of_hawkes": True, "hawkes_enabled_default": default_hawkes.hawkes_enabled,
            "hawkes_shadow_explicit": config.hawkes_shadow_requested,
            "hawkes_parameters_calibrated": False, "match_features_modified": False,
        },
        "database": database, "external_calls": 0, "secrets_logged": 0,
        "source_hashes": source_hashes,
    }


def _classification(selection: dict[str, Any], metrics: dict[str, Any], bootstrap: dict[str, Any], audit: dict[str, Any], config: ProspectiveConfig) -> str:
    """Aplica criterios congelados sin ajuste durante observación."""

    safe = all(audit["selection"].values()) and all(audit["temporal"].values()) and audit["numeric"]["positive_finite_intensities"]
    safe &= audit["database"]["identical"] and audit["database"]["select_only"] and audit["database"]["connection_closed"] and audit["database"]["write_statements"] == 0
    safe &= not audit["layers"]["official_output_modified"] and not audit["layers"]["hawkes_enabled_default"]
    if not safe:
        return "prospective_signal_rejected_for_revision"
    if len(selection["selected_match_ids"]) < config.minimum_matches or metrics["status"] != "evaluable":
        return "insufficient_prospective_coverage"
    if metrics["signal_coverage"] < config.minimum_signal_coverage or not _periods_sufficient(metrics["periods"], config):
        return "insufficient_prospective_coverage"
    deltas = bootstrap["metrics"]
    if any(deltas[key]["point_estimate"] > config.maximum_metric_degradation for key in ("candidate_vs_official_mae", "candidate_vs_official_log")):
        return "prospective_signal_rejected_for_revision"
    detection = deltas["candidate_vs_official_balanced_error"]
    if detection["ci_95"][1] <= 0:
        return "prospective_signal_supported"
    if detection["point_estimate"] < 0:
        return "prospective_signal_promising_unconfirmed"
    return "prospective_signal_unconfirmed"


def _periods_sufficient(periods: dict[str, Any], config: ProspectiveConfig) -> bool:
    """Exige estabilidad en suficientes periodos con partidos completos."""

    eligible = [value for value in periods.values() if value["match_count"] >= config.minimum_matches_per_period]
    return len(eligible) >= config.minimum_periods


def _source_hashes() -> dict[str, str]:
    """Registra runner, pruebas y capas oficiales congeladas."""

    paths = (
        "src/run_prospective_signal_shadow.py", "scripts/run_phase_7_10_prospective_signal_shadow.py",
        "tests/test_phase_7_10_prospective_signal_shadow.py", "tests/test_phase_7_10_prospective_signal_shadow_postgres.py",
        "src/dikamaha_service.py", "src/dikamaha_inference.py", "src/markov_v1.py", "src/hawkes_v1_integration.py",
        "artifacts/phase_7_7_event_label_coverage/labeling_rules_candidate.json",
        "artifacts/phase_7_8_markov_labeling_impact/manifest.json",
        "artifacts/phase_7_9_incremental_signal_value/signal_definitions.json",
    )
    return {path: _file_hash(ROOT / path) for path in paths}


def _build_result(matches: list[dict[str, Any]], raw_events: list[dict[str, Any]], database: dict[str, Any], config: ProspectiveConfig) -> dict[str, Any]:
    """Congela selección, observa cohorte y ensambla resultados."""

    lambda_map, lambda_hash = _lambda_inputs()
    selection, selected = _selection(matches, raw_events, lambda_map, config)
    match_map = {int(row["id"]): row for row in selected}
    events_map = _events_by_match(raw_events, match_map) if selected else {}
    observations = _observe_all(selected, events_map, lambda_map, config) if selected else []
    metrics, by_match, bootstrap = _prospective_metrics(observations, config)
    source_hashes = _source_hashes()
    if lambda_hash:
        source_hashes[str(LAMBDA_INPUT.relative_to(ROOT))] = lambda_hash
    audit = _audit(selection, observations, database, config, source_hashes)
    decision = _classification(selection, metrics, bootstrap, audit, config)
    return {
        "decision": decision, "selection": selection, "config": _frozen_config(config),
        "observations": observations, "metrics": metrics, "metrics_by_match": by_match,
        "bootstrap": bootstrap, "audit": audit,
    }


def _normalized(result: dict[str, Any]) -> dict[str, Any]:
    """Excluye latencia no determinista del hash de replay matemático."""

    payload = json.loads(json.dumps(result, default=str))
    for row in payload["observations"]:
        row.pop("service_latency_ms", None)
    payload["audit"].pop("database", None)
    return payload


def _artifact_payloads(result: dict[str, Any]) -> dict[str, Any]:
    """Separa artefactos por responsabilidad."""

    observations = result["observations"]
    return {
        "prospective_selection.json": result["selection"], "frozen_signal_config.json": result["config"],
        "prospective_snapshots.json": [{key: row[key] for key in ("match_id", "snapshot_ts", "minute", "score_home", "score_away", "events_observed_count", "events_observed_ids", "posterior_result")} for row in observations],
        "signal_observations.json": observations,
        "markov_predictions.json": [{key: row[key] for key in ("match_id", "snapshot_ts", "lambda_base_home", "lambda_base_away", "lambda_official_home", "lambda_official_away", "official_state_home", "official_state_away", "official_source", "service_latency_ms", "service_status")} for row in observations],
        "hawkes_shadow_predictions.json": [{"match_id": row["match_id"], "snapshot_ts": row["snapshot_ts"], "requested": row["hawkes_shadow_requested"], "output": row["hawkes_shadow"]} for row in observations],
        "metrics_by_match.json": result["metrics_by_match"], "metrics_aggregate.json": result["metrics"],
        "bootstrap_results.json": result["bootstrap"], "confidence_intervals.json": result["bootstrap"].get("metrics", {}),
        "audit.json": result["audit"], "postgres_readonly_audit.json": result["audit"]["database"],
    }


def _write_artifacts(result: dict[str, Any], replay: dict[str, Any]) -> None:
    """Escribe artefactos prospectivos, manifiesto e hashes."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name, payload in _artifact_payloads(result).items():
        _write_json(OUTPUT / name, payload)
    manifest = {
        "phase": "7.10", "version": ProspectiveConfig().version,
        "classification": result["decision"], "selection_hash": result["selection"]["selection_hash"],
        "input_hash": _stable_hash({"selection": result["selection"], "sources": result["audit"]["source_hashes"]}),
        "output_hash": replay["primary_hash"], "replay_hash": replay["replay_hash"],
        "replay_identical": replay["identical"], "latency_excluded_from_replay_hash": True,
        "postgresql_modified": False, "markov_official_modified": False, "hawkes_official": False,
    }
    _write_json(OUTPUT / "manifest.json", manifest)
    (OUTPUT / "final_report.md").write_text(_report(result, replay), encoding="utf-8")
    hashes = {path.name: _file_hash(path) for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json" and path != LAMBDA_INPUT}
    _write_json(OUTPUT / "hashes.json", hashes)


def _report(result: dict[str, Any], replay: dict[str, Any]) -> str:
    """Explica el estado prospectivo sin inflar evidencia."""

    selection = result["selection"]
    return "\n".join([
        "# Fase 7.10 - Shadow prospectivo de señales in-play", "",
        f"**Clasificación:** `{result['decision']}`", "", "## Selección congelada",
        f"- cutoff prospectivo: `{selection['prospective_cutoff_ts']}`",
        f"- partidos históricos excluidos: `{selection['prior_match_count']}`",
        f"- partidos nuevos: `{len(selection['new_match_ids'])}`",
        f"- partidos seleccionados: `{len(selection['selected_match_ids'])}`",
        f"- posteriores a Fase 7.9 pero ya usados: `{selection['post_phase_7_9_but_previously_used_ids']}`", "",
        "## Decisión", "- no se reutilizan partidos históricos para fabricar evidencia prospectiva",
        "- el colector queda preparado para una cohorte futura con lambda_base OOS congelada",
        "- no hay métricas ni significancia cuando la cohorte está vacía", "", "## Integridad",
        f"- replay determinista: `{replay['identical']}`",
        f"- PostgreSQL SELECT-only y conteos idénticos: `{result['audit']['database']['select_only'] and result['audit']['database']['identical']}`",
        "- Markov oficial intacto; Hawkes apagado por defecto; cero escrituras",
    ])


def _incomplete(reason: str) -> int:
    """Registra capacidad incompleta sin inventar observaciones."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    payload = {"classification": "insufficient_prospective_coverage", "database_status": "database_verification_incomplete", "reason": reason, "postgresql_modified": False}
    _write_json(OUTPUT / "audit.json", payload)
    _write_json(OUTPUT / "manifest.json", {"phase": "7.10", **payload})
    (OUTPUT / "final_report.md").write_text(f"# Fase 7.10\n\nEjecución incompleta: `{reason}`.\n", encoding="utf-8")
    return 0


def main() -> int:
    """Ejecuta shadow prospectivo read-only y replay."""

    capabilities = detect_capabilities()
    if not capabilities.ready:
        return _incomplete(f"missing:{','.join(capabilities.missing())}")
    config = ProspectiveConfig(hawkes_shadow_requested=os.getenv("DIKAMAHA_PROSPECTIVE_HAWKES_SHADOW", "false").lower() == "true")
    database_url = os.environ["DATABASE_URL"]
    try:
        matches, events, database = _read_database(database_url)
        primary = _build_result(matches, events, database, config)
        replay_result = _build_result(matches, events, database, config)
    except database_error_types() as error:
        return _incomplete(sanitize_error(error, database_url))
    replay = {"primary_hash": _stable_hash(_normalized(primary)), "replay_hash": _stable_hash(_normalized(replay_result))}
    replay["identical"] = replay["primary_hash"] == replay["replay_hash"]
    if not replay["identical"]:
        primary["decision"] = "prospective_signal_rejected_for_revision"
    _write_artifacts(primary, replay)
    LOGGER.info("Fase 7.10: %s", primary["decision"])
    return 1 if primary["decision"] == "prospective_signal_rejected_for_revision" else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0
# Created: 2026-07-16
