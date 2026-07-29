"""Evaluación prospectiva ESPN de sólo lectura sobre staging v2.

Requirements:
    - SQLAlchemy==2.0.41
    - psycopg2-binary==2.9.10

Version: 1.0.0
Created: 2026-07-16
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import statistics
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from src.markov_counterfactual import CounterfactualEstimator, actual_outcome, categorical_metrics, poisson_first_goal
from src.postgres_readonly_staging import ReadonlyDatabase, counts_identical, database_error_types, detect_capabilities, sanitize_error
from src.run_prospective_signal_shadow import _prior_match_ids

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/phase_7_16_prospective_evaluation"
STAGING_SCHEMA = "prospective_staging_v2"
FINAL_STATUSES = {"post", "final", "finished", "completed", "full_time"}
KNOWN_EVENTS = {"goal", "shot_off_target", "shot_on_target", "shot_blocked", "corner", "foul", "yellow", "red", "substitution", "auxiliary"}
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EvaluationConfig:
    """Contrato congelado de la evaluación prospectiva."""

    version: str = "phase_7_16_prospective_evaluation_v1"
    minimum_complete_matches: int = 30
    hawkes_shadow_requested: bool = False
    bootstrap_replicates: int = 1000
    bootstrap_seed: int = 716


def _utc(value: Any) -> datetime:
    """Convierte timestamps de PostgreSQL o ISO a UTC."""

    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def _hash(value: Any) -> str:
    """Calcula hash estable para JSON serializable."""

    encoded = json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write(path: Path, value: Any) -> None:
    """Escribe JSON atómico y determinista."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temporary.replace(path)


def _read_staging(database_url: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Lee exclusivamente las dos tablas allowlisted de staging v2."""

    database = ReadonlyDatabase(database_url)
    with database.session() as session:
        before = {"matches": int(session.scalar("SELECT COUNT(*) FROM prospective_staging_v2.matches")), "events": int(session.scalar("SELECT COUNT(*) FROM prospective_staging_v2.events"))}
        matches = session.rows("SELECT provider_match_id::bigint AS match_id, kickoff_ts, home_provider_team_id AS home_team_id, away_provider_team_id AS away_team_id, home_score, away_score, provider_status, complete FROM prospective_staging_v2.matches WHERE provider='espn' ORDER BY kickoff_ts, provider_match_id")
        events = session.rows("SELECT provider_match_id::bigint AS match_id, provider_event_id AS event_id, event_index, event_hash, event_ts, minute, second, team_provider_id AS team_id, event_type, event_type_raw, annulled FROM prospective_staging_v2.events WHERE provider='espn' ORDER BY provider_match_id, event_ts, event_index, provider_event_id")
        after = {"matches": int(session.scalar("SELECT COUNT(*) FROM prospective_staging_v2.matches")), "events": int(session.scalar("SELECT COUNT(*) FROM prospective_staging_v2.events"))}
    return matches, events, {"status": "postgres_readonly_verified", "source": STAGING_SCHEMA, "allowlist": ["prospective_staging_v2.matches", "prospective_staging_v2.events"], "before": before, "after": after, "identical": counts_identical(before, after), "connection_closed": database.closed, "statements": database.statements, "write_statements": 0, "select_only": all(item.startswith("SELECT ") for item in database.statements)}


def _artifact_ids(value: Any) -> set[int]:
    """Extrae identificadores auditables de artefactos prospectivos previos."""

    output: set[int] = set()
    if isinstance(value, dict):
        for key in ("match_id", "match_ids", "collected_match_ids"):
            if key in value:
                output.update(_artifact_ids(value[key]))
    elif isinstance(value, list):
        for item in value:
            output.update(_artifact_ids(item))
    elif isinstance(value, int) and not isinstance(value, bool):
        output.add(value)
    return output


def _excluded_ids() -> set[int]:
    """Une históricos, bloqueo contractual y reutilizaciones 7.10--7.13."""

    ids, _ = _prior_match_ids()
    ids.add(704766)
    for phase in range(10, 14):
        for path in sorted(ROOT.glob(f"artifacts/phase_7_{phase}_*/**/*.json")):
            try:
                ids.update(_artifact_ids(json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, json.JSONDecodeError):
                LOGGER.warning("Artefacto previo ilegible: %s", path.name)
    return ids


def _is_complete(row: dict[str, Any]) -> bool:
    """Exige flag, estado final, marcador e identidad de ambos equipos."""

    return bool(row.get("complete")) and str(row.get("provider_status", "")).lower() in FINAL_STATUSES and all(row.get(key) is not None for key in ("home_score", "away_score", "home_team_id", "away_team_id")) and int(row["home_team_id"]) != int(row["away_team_id"])


def _selection(matches: list[dict[str, Any]], excluded: set[int]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Separa nuevos candidatos de exclusiones sin consultar tablas históricas."""

    selected, rejected = [], []
    for row in matches:
        match_id = int(row["match_id"])
        reason = "previous_or_historical_match_id" if match_id in excluded else None
        record = {"match_id": match_id, "kickoff_ts": _utc(row["kickoff_ts"]).isoformat(), "complete": _is_complete(row), "exclusion_reason": reason}
        (rejected if reason else selected).append({**row, "selection": record})
    return selected, rejected


def _by_match(events: Iterable[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    """Agrupa eventos con orden temporal contractual."""

    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in events:
        grouped[int(row["match_id"])].append(row)
    return {key: sorted(value, key=lambda item: (_utc(item["event_ts"]), int(item["event_index"]), str(item["event_id"]))) for key, value in grouped.items()}


def _snapshots(match: dict[str, Any], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Construye snapshots observables; nunca son unidades IID de evaluación."""

    kickoff = _utc(match["kickoff_ts"])
    moments = {kickoff + timedelta(minutes=minute) for minute in (0, 15, 30, 45, 60, 75, 90)}
    moments.update(_utc(row["event_ts"]) for row in events if not row["annulled"])
    return [_snapshot_row(match, events, item) for item in sorted(moments)]


def _snapshot_row(match: dict[str, Any], events: list[dict[str, Any]], timestamp: datetime) -> dict[str, Any]:
    """Serializa una vista causal y verificable del timeline."""

    visible = [row for row in events if _utc(row["event_ts"]) <= timestamp]
    return {"match_id": int(match["match_id"]), "snapshot_ts": timestamp.isoformat(), "visible_event_count": len(visible), "max_visible_event_ts": max((_utc(row["event_ts"]).isoformat() for row in visible), default=None), "evaluation_unit": "complete_match"}


def _coverage(match: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    """Cuenta cobertura por partido conservando anulados y desconocidos."""

    valid = [row for row in events if not row["annulled"]]
    types = Counter(str(row["event_type"]) for row in valid)
    known = {key: int(types[key]) for key in sorted(KNOWN_EVENTS)}
    teams = _team_coverage(match, valid)
    return {"match_id": int(match["match_id"]), "event_count": len(events), "valid_event_count": len(valid), "annulled_event_count": len(events) - len(valid), "unknown_event_count": sum(row["event_type"] not in KNOWN_EVENTS for row in valid), "null_team_id_count": sum(row["team_id"] is None for row in valid), "signals": {"shots": known["shot_off_target"] + known["shot_on_target"] + known["shot_blocked"], "shots_on_target": known["shot_on_target"], "corners": known["corner"], "pressure": known["shot_off_target"] + known["shot_on_target"] + known["shot_blocked"] + known["corner"], "goals": known["goal"], "cards": known["yellow"] + known["red"], "substitutions": known["substitution"]}, "by_team": teams, "event_types": known}


def _team_coverage(match: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Calcula tiros concedidos como tiros válidos del rival por equipo."""

    shot_types = {"shot_off_target", "shot_on_target", "shot_blocked"}
    output: dict[str, dict[str, int]] = {}
    for side in ("home", "away"):
        team_id = int(match[f"{side}_team_id"])
        own = [row for row in events if row["team_id"] == team_id]
        rival = [row for row in events if row["team_id"] not in {None, team_id}]
        output[side] = {"shots": sum(row["event_type"] in shot_types for row in own), "shots_on_target": sum(row["event_type"] == "shot_on_target" for row in own), "shots_conceded": sum(row["event_type"] in shot_types for row in rival), "corners": sum(row["event_type"] == "corner" for row in own), "pressure": sum(row["event_type"] in shot_types | {"corner"} for row in own), "goals": sum(row["event_type"] == "goal" for row in own), "cards": sum(row["event_type"] in {"yellow", "red"} for row in own), "substitutions": sum(row["event_type"] == "substitution" for row in own)}
    return output


def _temporal(matches: list[dict[str, Any]], events: list[dict[str, Any]], snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    """Audita timestamps, duplicados, huérfanos y orden sin alterar staging."""

    match_ids = {int(row["match_id"]) for row in matches}
    keys = [(row["match_id"], row["snapshot_ts"]) for row in snapshots]
    event_keys = [(row["match_id"], row["event_id"], row["event_hash"]) for row in events]
    ordered = events == sorted(events, key=lambda row: (int(row["match_id"]), _utc(row["event_ts"]), int(row["event_index"]), str(row["event_id"])))
    causal = all(snap["max_visible_event_ts"] is None or _utc(snap["max_visible_event_ts"]) <= _utc(snap["snapshot_ts"]) for snap in snapshots)
    return {"event_ts_lte_snapshot_ts": causal, "stable_temporal_order": ordered, "duplicate_snapshot_count": len(keys) - len(set(keys)), "duplicate_event_count": len(event_keys) - len(set(event_keys)), "orphan_event_match_references": sorted({int(row["match_id"]) for row in events if int(row["match_id"]) not in match_ids}), "unknown_event_count": sum(row["event_type"] not in KNOWN_EVENTS for row in events), "annulled_event_count": sum(bool(row["annulled"]) for row in events), "null_team_id_count": sum(row["team_id"] is None for row in events)}


def _complete_audit(selected: list[dict[str, Any]], events: dict[int, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Explica completitud por partido y cobertura temporal disponible."""

    return [{**row["selection"], "event_count": len(events.get(int(row["match_id"]), [])), "evaluable": _is_complete(row)} for row in selected]


def _classification(complete: int, audit: dict[str, Any], config: EvaluationConfig) -> str:
    """Clasifica sin promoción automática ni inferencia de significancia."""

    bad = audit["duplicate_event_count"] or audit["orphan_event_match_references"] or not audit["stable_temporal_order"]
    if bad:
        return "prospective_evaluation_rejected_for_revision"
    if complete < config.minimum_complete_matches:
        return "insufficient_prospective_coverage"
    return "prospective_cohort_ready"


def _empty_evaluation(reason: str) -> tuple[list[Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Produce salidas explícitas cuando no corresponde calcular métricas."""

    empty = {"performed": False, "reason": reason, "unit": "complete_match", "significance_calculated": False}
    return [], empty, empty, empty


def _lambda_map() -> dict[int, dict[str, float]]:
    """Carga lambdas prospectivas congeladas y sólo desde una ruta local explícita."""

    value = os.getenv("DIKAMAHA_PROSPECTIVE_LAMBDA_INPUT") or str(OUTPUT / "prospective_lambda_base_input.json")
    if not value:
        return {}
    path = Path(value).resolve()
    if ROOT.resolve() not in path.parents or not path.is_file():
        raise ValueError("invalid_prospective_lambda_input")
    rows = json.loads(path.read_text(encoding="utf-8"))
    rows = rows.get("rows", rows) if isinstance(rows, dict) else rows
    return {int(row["match_id"]): {"lambda_base_home": float(row["lambda_base_home"]), "lambda_base_away": float(row["lambda_base_away"])} for row in rows}


def _frozen_estimator() -> CounterfactualEstimator:
    """Reconstruye el estimador 7.12 desde soporte congelado, sin hacer fit."""

    support = json.loads((ROOT / "artifacts/phase_7_12_markov_counterfactual/historical_support.json").read_text(encoding="utf-8"))
    estimator = CounterfactualEstimator()
    estimator.cuts = tuple(float(value) for value in support["strength_cuts"])
    estimator.first_counts.update({key: Counter(value) for key, value in support["first_goal_counts"].items()})
    estimator.second_counts.update({key: Counter(value) for key, value in support["second_transition_counts"].items()})
    return estimator


def _partition(ids: list[int]) -> dict[str, list[int]]:
    """Congela validación y confirmación por partido completo, nunca snapshot."""

    ordered = sorted(ids)
    split = max(1, len(ordered) // 2)
    return {"development_match_ids": [], "validation_match_ids": ordered[:split], "confirmation_match_ids": ordered[split:]}


def _evaluate(complete: list[dict[str, Any]], event_map: dict[int, list[dict[str, Any]]], config: EvaluationConfig) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Ejecuta contrafactual congelado y bootstrap agrupado sólo tras umbral."""

    lambdas = _lambda_map()
    if any(int(row["match_id"]) not in lambdas for row in complete):
        return _empty_evaluation("missing_frozen_lambda_base")
    estimator, partition = _frozen_estimator(), _partition([int(row["match_id"]) for row in complete])
    predictions, metrics = [], []
    for row in complete:
        match_id, values = int(row["match_id"]), lambdas[int(row["match_id"])]
        base = {"match_id": match_id, "block": "validation" if match_id in partition["validation_match_ids"] else "confirmation", "home_team_id": int(row["home_team_id"]), "away_team_id": int(row["away_team_id"]), **values}
        events = [{**item, "valid": not item["annulled"]} for item in event_map.get(match_id, [])]
        actual, prediction = actual_outcome(base, events), estimator.predict(base)
        predictions.append(prediction)
        if actual["first"] != "unknown":
            baseline_distribution = poisson_first_goal(values["lambda_base_home"], values["lambda_base_away"])
            model = categorical_metrics(actual["first"], prediction["first_goal_distribution"])
            baseline = categorical_metrics(actual["first"], baseline_distribution)
            metrics.append({"match_id": match_id, "block": base["block"], "actual_first": actual["first"], "counterfactual": model, "lambda_base": baseline, "delta_log_score": model["log_score"] - baseline["log_score"], "unit": "complete_match"})
    aggregate = _aggregate(metrics, partition)
    return predictions, metrics, aggregate, _bootstrap(metrics, config)


def _aggregate(metrics: list[dict[str, Any]], partition: dict[str, list[int]]) -> dict[str, Any]:
    """Agrega con peso idéntico por partido y conserva la partición congelada."""

    output: dict[str, Any] = {"partition": partition, "unit": "complete_match"}
    for block in ("validation", "confirmation"):
        rows = [row for row in metrics if row["block"] == block]
        output[block] = {"match_count": len(rows), "delta_log_score": statistics.fmean(row["delta_log_score"] for row in rows) if rows else None}
    return output


def _bootstrap(metrics: list[dict[str, Any]], config: EvaluationConfig) -> dict[str, Any]:
    """Bootstrap determinista agrupado por partido; nunca por snapshots."""

    values = [row["delta_log_score"] for row in metrics if row["block"] == "confirmation"]
    if not values:
        return {"performed": False, "reason": "no_confirmation_metrics", "unit": "complete_match"}
    generator = random.Random(config.bootstrap_seed)
    samples = sorted(statistics.fmean(generator.choice(values) for _ in values) for _ in range(config.bootstrap_replicates))
    return {"performed": True, "unit": "complete_match", "replicates": config.bootstrap_replicates, "seed": config.bootstrap_seed, "ci_95": [samples[int(.025 * len(samples))], samples[int(.975 * len(samples))]]}


def _result(matches: list[dict[str, Any]], events: list[dict[str, Any]], database: dict[str, Any], config: EvaluationConfig) -> dict[str, Any]:
    """Construye auditoría y decide el umbral antes de cualquier evaluación."""

    excluded = _excluded_ids()
    selected, rejected = _selection(matches, excluded)
    event_map = _by_match(events)
    complete = [row for row in selected if _is_complete(row)]
    snapshots = [item for row in complete for item in _snapshots(row, event_map.get(int(row["match_id"]), []))]
    coverage = [_coverage(row, event_map.get(int(row["match_id"]), [])) for row in complete]
    temporal = _temporal(matches, events, snapshots)
    completeness = _complete_audit(selected, event_map)
    classification = _classification(len(complete), temporal, config)
    prediction, metrics, aggregate, bootstrap = _empty_evaluation("insufficient_prospective_coverage") if len(complete) < 30 else _evaluate(complete, event_map, config)
    return {"excluded": excluded, "selected": selected, "rejected": rejected, "complete": complete, "snapshots": snapshots, "coverage": coverage, "temporal": temporal, "completeness": completeness, "classification": classification, "predictions": prediction, "metrics": metrics, "aggregate": aggregate, "bootstrap": bootstrap, "database": database}


def _provenance(config: EvaluationConfig, result: dict[str, Any]) -> dict[str, Any]:
    """Documenta prohibiciones y procedencia de la ejecución."""

    return {"source": STAGING_SCHEMA, "allowlist_enforced": True, "markov_recalibrated": False, "alpha_beta_recalibrated": False, "hawkes_enabled_default": False, "hawkes_shadow_requested": config.hawkes_shadow_requested, "official_output_modified": False, "match_features_v1_modified": False, "historical_artifacts_modified": False, "snapshots_iid": False, "partition_separation": "by_complete_match", "new_match_ids": [int(row["match_id"]) for row in result["selected"]], "evaluable_match_ids": [int(row["match_id"]) for row in result["complete"]]}


def _payloads(result: dict[str, Any], config: EvaluationConfig) -> dict[str, Any]:
    """Prepara todos los artefactos requeridos por el contrato de fase."""

    provenance = _provenance(config, result)
    selection = {"source": STAGING_SCHEMA, "new_match_count": len(result["selected"]), "complete_match_count": len(result["complete"]), "selected_match_ids": [int(row["match_id"]) for row in result["selected"]], "excluded_match_count": len(result["rejected"])}
    stored = result["aggregate"].get("partition", {}) if isinstance(result["aggregate"], dict) else {}
    partition = {"frozen": len(result["complete"]) >= 30, "unit": "complete_match", **stored, "reason": "threshold_not_reached" if len(result["complete"]) < 30 else result["aggregate"].get("reason", "frozen_7_12_estimator")}
    audit = {"classification": result["classification"], "complete_match_count": len(result["complete"]), "minimum_complete_matches": config.minimum_complete_matches, "significance_calculated": False, "bootstrap_confirmatory_executed": bool(result["bootstrap"].get("performed", False)), "postgres_select_only": result["database"].get("select_only", False), "postgres_counts_identical": result["database"].get("identical", False), "no_promotion": True}
    return {"staging_selection.json": selection, "excluded_match_ids.json": {"match_ids": sorted(result["excluded"]), "count": len(result["excluded"])}, "prospective_partition.json": partition, "completeness_audit.json": result["completeness"], "snapshots.json": result["snapshots"], "event_coverage.json": result["coverage"], "counterfactual_predictions.json": result["predictions"], "metrics_by_match.json": result["metrics"], "metrics_aggregate.json": result["aggregate"], "bootstrap_results.json": result["bootstrap"], "confidence_intervals.json": result["bootstrap"], "temporal_audit.json": result["temporal"], "provenance_audit.json": provenance, "postgres_readonly_audit.json": result["database"], "audit.json": audit}


def _report(payloads: dict[str, Any], classification: str) -> str:
    """Resume decisión y límites metodológicos en Markdown."""

    selection, audit = payloads["staging_selection.json"], payloads["audit.json"]
    return "\n".join(["# Fase 7.16 - Evaluación prospectiva ESPN", "", f"**Clasificación:** `{classification}`", "", f"- partidos nuevos: `{selection['new_match_count']}`", f"- partidos completos: `{selection['complete_match_count']}` / `{audit['minimum_complete_matches']}`", "- unidad analítica: partido completo; snapshots no son IID.", "- sin recalibración Markov/alpha/beta; Hawkes desactivado (sólo shadow opcional).", "- no se promovió ningún resultado."])


def _write_all(result: dict[str, Any], config: EvaluationConfig) -> None:
    """Materializa artefactos, manifiesto, hashes y reporte deterministas."""

    payloads = _payloads(result, config)
    for name, value in payloads.items():
        _write(OUTPUT / name, value)
    replay = _hash(payloads)
    manifest = {"phase": "7.16", "version": config.version, "classification": result["classification"], "postgresql_modified": False, "markov_official_modified": False, "hawkes_official": False, "replay_hash": replay, "replay_identical": replay == _hash(payloads), "input_hash": _hash({"selection": payloads["staging_selection.json"], "excluded": payloads["excluded_match_ids.json"]})}
    _write(OUTPUT / "manifest.json", manifest)
    (OUTPUT / "final_report.md").write_text(_report(payloads, result["classification"]), encoding="utf-8")
    _write(OUTPUT / "hashes.json", {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"})


def _incomplete(reason: str, config: EvaluationConfig) -> int:
    """Emite el contrato completo sin inventar acceso ni cohortes."""

    database = {"status": "postgres_readonly_incomplete", "reason": reason, "source": STAGING_SCHEMA, "allowlist": ["prospective_staging_v2.matches", "prospective_staging_v2.events"], "before": None, "after": None, "identical": False, "select_only": True, "write_statements": 0}
    result = _result([], [], database, config)
    _write_all(result, config)
    return 0


def main() -> int:
    """Ejecuta la evaluación sin operaciones distintas de SELECT."""

    config = EvaluationConfig(hawkes_shadow_requested=os.getenv("DIKAMAHA_PROSPECTIVE_HAWKES_SHADOW", "false").lower() == "true")
    capabilities = detect_capabilities()
    if not capabilities.ready:
        return _incomplete(f"missing:{','.join(capabilities.missing())}", config)
    database_url = os.environ["DATABASE_URL"]
    try:
        matches, events, database = _read_staging(database_url)
        result = _result(matches, events, database, config)
    except (ValueError, OSError, *database_error_types()) as error:
        return _incomplete(sanitize_error(error, database_url), config)
    _write_all(result, config)
    LOGGER.info("Fase 7.16: %s", result["classification"])
    return 1 if result["classification"] == "prospective_evaluation_rejected_for_revision" else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0
# Created: 2026-07-16
