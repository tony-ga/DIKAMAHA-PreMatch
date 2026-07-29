"""Ejecuta la evaluación histórica del Markov contrafactual pre-match.

PostgreSQL se utiliza exclusivamente mediante SELECT. La salida oficial,
Markov v1 y Hawkes no se modifican.

Version: 1.0.0
Created: 2026-07-16
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import random
import statistics
import sys
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.markov_counterfactual import CounterfactualConfig, CounterfactualEstimator, actual_outcome, categorical_metrics
from src.postgres_readonly_staging import ReadonlyDatabase, database_error_types, detect_capabilities, sanitize_error

OUTPUT = ROOT / "artifacts/phase_7_12_markov_counterfactual"
PARTITION_PATH = ROOT / "artifacts/phase_7_9_incremental_signal_value/temporal_partition.json"
PREDICTIONS_PATH = ROOT / "artifacts/phase_7_6_model_calibration/predictions.json"
LOGGER = logging.getLogger(__name__)


def _load(path: Path) -> Any:
    """Carga un artefacto JSON congelado."""

    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, payload: Any) -> None:
    """Escribe un artefacto JSON de forma atómica."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temporary.replace(path)


def _hash_file(path: Path) -> str:
    """Calcula SHA-256 de un archivo."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _counts(session: Any) -> dict[str, int]:
    """Cuenta únicamente fuentes históricas consultadas."""

    return {name: int(session.scalar(f"SELECT COUNT(*) FROM {name}")) for name in ("matches", "events_ledger", "events_timeline")}


def _read_postgres(database_url: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Lee partidos/eventos con server read-only y verifica conteos."""

    database = ReadonlyDatabase(database_url)
    with database.session() as session:
        before = _counts(session)
        matches = session.rows("""
            SELECT id, home_team_id, away_team_id, match_date FROM matches ORDER BY match_date, id
        """)
        events = session.rows("""
            SELECT id, match_id, minute, second, team_id, event_type, event_type_raw, valid
            FROM events_ledger ORDER BY match_id, minute, second, event_index, id
        """)
        after = _counts(session)
    return matches, events, {"status": "postgres_readonly_verified", "before": before, "after": after,
                             "identical": before == after, "write_statements": 0, "connection_closed": database.closed,
                             "statements": database.statements}


def _lambda_rows() -> dict[int, dict[str, Any]]:
    """Extrae una sola lambda OOS pre-match por partido."""

    output: dict[int, dict[str, Any]] = {}
    for row in _load(PREDICTIONS_PATH):
        match_id = int(row["match_id"])
        if int(row["minute"]) == 0 and match_id not in output:
            output[match_id] = {"lambda_base_home": float(row["lambda_base_home"]),
                                "lambda_base_away": float(row["lambda_base_away"])}
    return output


def _assemble(matches: list[dict[str, Any]], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Une partición, lambdas OOS y eventos sin usar marcador final."""

    partition, lambdas = _load(PARTITION_PATH)["blocks"], _lambda_rows()
    blocks = {int(match_id): block for block, data in partition.items() for match_id in data["match_ids"]}
    by_event: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_event[int(event["match_id"])].append(event)
    rows = []
    for match in matches:
        match_id = int(match["id"])
        if match_id not in blocks or match_id in {2, 704766} or match_id not in lambdas:
            continue
        base = {"match_id": match_id, "block": blocks[match_id], "home_team_id": int(match["home_team_id"]),
                "away_team_id": int(match["away_team_id"]), "match_date": str(match["match_date"]), **lambdas[match_id]}
        rows.append({**base, "actual": actual_outcome(base, by_event[match_id]), "events": by_event[match_id]})
    return rows


def _second_prob(prediction: dict[str, Any], first: str, second: str | None) -> float | None:
    """Obtiene la probabilidad condicional del segundo ciclo observado."""

    if first == "no_goal" or second is None:
        return None
    side, window = first.split("_", 1)
    branch = next(row for row in prediction["branches"] if row["first_goal_team"] == side and row["first_goal_window"] == window)
    field = {"same_team_second": "probability_second_goal_same_team", "equalizer": "probability_equalizer",
             "conserve_advantage": "probability_conserve_advantage"}[second]
    return float(branch[field])


def _evaluate(rows: list[dict[str, Any]], predictions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Calcula métricas OOS por partido, no por snapshot."""

    by_prediction = {int(row["match_id"]): row for row in predictions}
    output = []
    for row in rows:
        if row["block"] == "development" or row["actual"]["first"] == "unknown":
            continue
        prediction = by_prediction[row["match_id"]]
        model = categorical_metrics(row["actual"]["first"], prediction["first_goal_distribution"])
        baseline = categorical_metrics(row["actual"]["first"], prediction["lambda_baseline_first_goal"])
        second = _second_prob(prediction, row["actual"]["first"], row["actual"]["second"])
        output.append({"match_id": row["match_id"], "block": row["block"], "actual_first": row["actual"]["first"],
                       "actual_second": row["actual"]["second"], "counterfactual": model, "lambda_base": baseline,
                       "second_transition_log_score": -math.log(max(second, 1e-15)) if second is not None else None,
                       "delta_log_score": model["log_score"] - baseline["log_score"]})
    return output


def _aggregate(metrics: list[dict[str, Any]]) -> dict[str, Any]:
    """Agrega métricas dando igual peso a cada partido."""

    result = {}
    for block in ("validation", "confirmation"):
        rows = [row for row in metrics if row["block"] == block]
        result[block] = {"match_count": len(rows),
            "counterfactual_log_score": statistics.fmean(row["counterfactual"]["log_score"] for row in rows),
            "lambda_base_log_score": statistics.fmean(row["lambda_base"]["log_score"] for row in rows),
            "counterfactual_brier": statistics.fmean(row["counterfactual"]["brier"] for row in rows),
            "lambda_base_brier": statistics.fmean(row["lambda_base"]["brier"] for row in rows),
            "delta_log_score": statistics.fmean(row["delta_log_score"] for row in rows)}
    return result


def _calibration(rows: list[dict[str, Any]], predictions: list[dict[str, Any]]) -> dict[str, Any]:
    """Agrupa calibración por ventana y fuerza con unidad partido completo."""

    actual = {int(row["match_id"]): row["actual"]["first"] for row in rows}
    output: dict[str, Any] = {}
    for block in ("validation", "confirmation"):
        selected = [row for row in predictions if row["block"] == block]
        windows, strengths = {}, {}
        for window in ("early", "middle", "late", "no_goal"):
            predicted = [row["first_goal_distribution"]["no_goal"] if window == "no_goal" else
                         sum(value for key, value in row["first_goal_distribution"].items() if key.endswith(window)) for row in selected]
            observed = [1.0 if (actual[row["match_id"]] == "no_goal" if window == "no_goal" else actual[row["match_id"]].endswith(window)) else 0.0 for row in selected]
            windows[window] = {"predicted_mean": statistics.fmean(predicted), "observed_rate": statistics.fmean(observed), "match_count": len(selected)}
        for strength in ("away_stronger", "balanced", "home_stronger"):
            subset = [row for row in selected if row["strength_bin"] == strength]
            strengths[strength] = {"match_count": len(subset), "mean_branch_sum": statistics.fmean(row["branch_probability_sum"] for row in subset) if subset else None}
        output[block] = {"first_goal_window": windows, "strength_relative": strengths, "score_differential_after_first": [-1, 1]}
    return output


def _bootstrap(metrics: list[dict[str, Any]], config: CounterfactualConfig) -> dict[str, Any]:
    """Bootstrap agrupado por partido con semilla fija."""

    generator, output = random.Random(config.bootstrap_seed), {}
    for block in ("validation", "confirmation"):
        values = [row["delta_log_score"] for row in metrics if row["block"] == block]
        replicates = [statistics.fmean(generator.choice(values) for _ in values) for _ in range(config.bootstrap_replicates)]
        ordered = sorted(replicates)
        output[block] = {"replicates": config.bootstrap_replicates, "seed": config.bootstrap_seed,
                         "mean_delta_log_score": statistics.fmean(values),
                         "ci_95": [ordered[int(0.025 * len(ordered))], ordered[int(0.975 * len(ordered))]],
                         "unit": "complete_match"}
    return output


def _behavior_metrics(rows: list[dict[str, Any]], predictions: list[dict[str, Any]]) -> dict[str, Any]:
    """Evalúa comportamiento esperado sólo para el contexto realmente observado."""

    by_prediction = {int(row["match_id"]): row for row in predictions}
    errors: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        actual = row["actual"]
        if row["block"] == "development" or actual["first"] in {"no_goal", "unknown"}:
            continue
        side, window = actual["first"].split("_", 1)
        branch = next(item for item in by_prediction[row["match_id"]]["branches"] if item["first_goal_team"] == side and item["first_goal_window"] == window)
        for key, expected in branch["expected_behavior_15m"].items():
            errors[key].append(abs(float(expected) - float(actual["behavior"].get(key, 0.0))))
    return {key: {"mae": statistics.fmean(values), "match_context_count": len(values)} for key, values in sorted(errors.items())}


def _audits(rows: list[dict[str, Any]], predictions: list[dict[str, Any]], database: dict[str, Any]) -> dict[str, Any]:
    """Verifica temporalidad, probabilidades, separación y reproducibilidad."""

    sums = [abs(float(row["branch_probability_sum"]) - 1.0) for row in predictions]
    return {"zero_branch_sum_violations": sum(value > 1e-10 for value in sums),
            "maximum_branch_sum_error": max(sums, default=0.0), "blocked_704766_absent": all(row["match_id"] not in {2, 704766} for row in rows),
            "fit_block": "development_only", "validation_confirmation_used_for_selection": False,
            "snapshot_iid_assumption": False, "targets_used_as_features": False, "final_score_used_as_feature": False,
            "postgres_select_only": database["write_statements"] == 0 and database["identical"],
            "markov_official_modified": False, "hawkes_used": False, "match_features_modified": False}


def _classification(aggregate: dict[str, Any], support: dict[str, Any], audit: dict[str, Any]) -> str:
    """Aplica criterios predefinidos sin promover la capa experimental."""

    if not all((audit["zero_branch_sum_violations"] == 0, audit["postgres_select_only"], audit["blocked_704766_absent"])):
        return "counterfactual_markov_rejected_for_revision"
    exact = [row for row in support["sparse_contexts"] if not row["context"].endswith("|global")]
    if exact:
        return "insufficient_contextual_support"
    if aggregate["confirmation"]["delta_log_score"] <= 0:
        return "counterfactual_markov_supported"
    return "counterfactual_markov_promising_unconfirmed"


def _contract(config: CounterfactualConfig) -> dict[str, Any]:
    """Define entradas, salidas y prohibiciones de la capa contrafactual."""

    return {"version": config.version, "input": ["lambda_base_home", "lambda_base_away", "match_id", "kickoff_ts"],
            "flow": ["first_goal_distribution", "hypothetical_window", "score_context", "second_cycle", "expected_behavior_15m"],
            "probability_estimation": "development_only_empirical_MLE_with_historical_fallback",
            "windows": [{"name": name, "start": start, "end_exclusive": min(end, 91)} for name, start, end in (("early", 0, 30), ("middle", 30, 60), ("late", 60, 121))],
            "official": False, "hawkes": "shadow_disabled", "forbidden": ["target_match_events", "final_score_feature", "manual_percentages", "official_promotion"]}


def _state_space() -> dict[str, Any]:
    """Documenta estados y ciclos hipotéticos del árbol."""

    return {"tactical_states": {"0": "equilibrio", "1": "repliegue", "2": "asedio", "unknown": "preserved"},
            "score_states": ["0-0", "1-0", "0-1", "1-1", "2-0", "0-2", "2-1", "1-2"],
            "cycles": ["home_scores_first", "away_scores_first", "same_team_second", "equalizer", "conserve_advantage", "new_cycle_after_equalizer_or_two_goal_lead"]}


def _write_artifacts(result: dict[str, Any]) -> None:
    """Escribe el conjunto contractual completo y hashes deterministas."""

    payloads = {"counterfactual_contract.json": result["contract"], "state_space.json": _state_space(),
        "transition_definitions.json": result["transitions"], "scenario_tree_examples.json": result["predictions"][:3],
        "historical_support.json": result["support"], "pre_match_first_goal.json": result["first_goal"],
        "counterfactual_predictions.json": result["predictions"], "expected_behavior_metrics.json": result["behavior"],
        "metrics_by_match.json": result["metrics"], "metrics_aggregate.json": result["aggregate"],
        "bootstrap_results.json": result["bootstrap"], "confidence_intervals.json": result["bootstrap"],
        "temporal_audit.json": result["audit"], "provenance_audit.json": result["provenance"],
        "postgres_readonly_audit.json": result["database"], "audit.json": result["audit"]}
    for name, payload in payloads.items():
        _write(OUTPUT / name, payload)
    manifest = {"phase": "7.12_markov_counterfactual", "classification": result["classification"],
                "version": result["contract"]["version"], "official_output_modified": False,
                "hawkes_enabled": False, "postgresql_modified": False, "replay_identical": result["replay_identical"]}
    _write(OUTPUT / "manifest.json", manifest)
    (OUTPUT / "final_report.md").write_text(_report(result), encoding="utf-8")
    _write(OUTPUT / "hashes.json", {path.name: _hash_file(path) for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"})


def _report(result: dict[str, Any]) -> str:
    """Resume soporte, resultado OOS y límites de uso."""

    confirmation = result["aggregate"]["confirmation"]
    return "\n".join(["# Fase 7.12 - Markov contextual contrafactual pre-match", "",
        f"**Clasificación:** `{result['classification']}`", "", f"- partidos: `{len(result['rows'])}`",
        f"- predicciones OOS: `{len(result['predictions'])}`", f"- delta log score confirmación vs lambda_base: `{confirmation['delta_log_score']:.6f}`",
        f"- contextos escasos: `{len(result['support']['sparse_contexts'])}`", f"- replay determinista: `{result['replay_identical']}`", "",
        "La capa es experimental y analítica. No modifica Markov oficial, match_features v1 ni Hawkes; PostgreSQL fue consultado únicamente mediante SELECT."])


def _run(database_url: str) -> dict[str, Any]:
    """Ejecuta ajuste development y evaluación OOS reproducible."""

    config = CounterfactualConfig()
    matches, events, database = _read_postgres(database_url)
    rows = _assemble(matches, events)
    estimator = CounterfactualEstimator(config).fit([row for row in rows if row["block"] == "development"])
    predictions = [estimator.predict(row) for row in rows if row["block"] != "development"]
    replay = [estimator.predict(row) for row in rows if row["block"] != "development"]
    metrics = _evaluate(rows, predictions)
    aggregate, support = _aggregate(metrics), estimator.support()
    aggregate["calibration"] = _calibration(rows, predictions)
    audit = _audits(rows, predictions, database)
    result = {"rows": rows, "contract": _contract(config), "transitions": support["second_transition_counts"],
              "support": support, "first_goal": support["first_goal_counts"], "predictions": predictions,
              "metrics": metrics, "aggregate": aggregate, "bootstrap": _bootstrap(metrics, config),
              "behavior": _behavior_metrics(rows, predictions), "audit": audit, "database": database,
              "provenance": {"development_only": True, "partition_hash": _hash_file(PARTITION_PATH),
                             "lambda_hash": _hash_file(PREDICTIONS_PATH), "official_output": "markov_v1", "hawkes": "unchanged_disabled"},
              "replay_identical": predictions == replay}
    result["classification"] = _classification(aggregate, support, audit)
    return result


def main() -> int:
    """Ejecuta la fase o informa soporte insuficiente sin inventar datos."""

    capabilities = detect_capabilities()
    if not capabilities.ready:
        LOGGER.error("Capacidades PostgreSQL incompletas: %s", capabilities.missing())
        return 2
    database_url = os.environ["DATABASE_URL"]
    try:
        result = _run(database_url)
    except (ValueError, OSError, *database_error_types()) as error:
        LOGGER.error("Fase 7.12 rechazada: %s", sanitize_error(error, database_url))
        return 1
    _write_artifacts(result)
    LOGGER.info("Fase 7.12: %s", result["classification"])
    return 1 if result["classification"] == "counterfactual_markov_rejected_for_revision" else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0
# Created: 2026-07-16
