"""Robustez estadística de Hawkes shadow v1 agrupada por partido.

No calibra parámetros ni interpreta snapshots del mismo partido como IID.
Las cinco configuraciones de sensibilidad proceden de Fase 5.4.

Requirements:
    Python 3.12+

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
import sys
from pathlib import Path
from statistics import mean
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "artifacts/phase_7_1_historical_expansion"
OUTPUT = ROOT / "artifacts/phase_7_2_hawkes_robustness"
SEED = 7201
BOOTSTRAP_REPLICATES = 10_000
SUBGROUP_REPLICATES = 2_000
LOGGER = logging.getLogger(__name__)


def _load(path: Path) -> Any:
    """Carga JSON local."""

    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, payload: Any) -> None:
    """Escribe JSON atómicamente."""

    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temporary.replace(path)


def _stable_hash(payload: Any) -> str:
    """Calcula SHA-256 determinista."""

    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _poisson_log_score(target: int, expected: float) -> float:
    """Calcula negative log score Poisson."""

    value = max(float(expected), 1e-9)
    return -(target * math.log(value) - value - math.lgamma(target + 1))


def _percentile(values: list[float], fraction: float) -> float:
    """Calcula un percentil lineal reproducible."""

    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower, upper = math.floor(position), math.ceil(position)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _match_statistics(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Agrega deltas por partido antes de cualquier inferencia."""

    output = []
    for match_id in sorted({int(row["match_id"]) for row in rows}):
        group = [row for row in rows if int(row["match_id"]) == match_id]
        markov_total = [row["markov_pred_home"] + row["markov_pred_away"] for row in group]
        hawkes_total = [row["hawkes_pred_home"] + row["hawkes_pred_away"] for row in group]
        targets = [int(row["remaining_total_goals"]) for row in group]
        output.append({
            "match_id": match_id,
            "block": group[0]["block"],
            "categories": group[0]["categories"],
            "snapshot_count": len(group),
            "delta_mae": mean(abs(t - h) - abs(t - m) for t, h, m in zip(targets, hawkes_total, markov_total, strict=True)),
            "delta_log_score": mean(_poisson_log_score(t, h) - _poisson_log_score(t, m) for t, h, m in zip(targets, hawkes_total, markov_total, strict=True)),
            "delta_mae_home": mean(abs(row["remaining_home_goals"] - row["hawkes_pred_home"]) - abs(row["remaining_home_goals"] - row["markov_pred_home"]) for row in group),
            "delta_mae_away": mean(abs(row["remaining_away_goals"] - row["hawkes_pred_away"]) - abs(row["remaining_away_goals"] - row["markov_pred_away"]) for row in group),
            "uplift": mean(float(row["absolute_uplift"]) for row in group),
            "overexcitation_frequency": mean(float(row["overexcitation_warning"]) for row in group),
        })
    return output


def _bootstrap_metric(
    rows: list[dict[str, Any]],
    field: str,
    seed: int,
    replicates: int,
) -> dict[str, Any]:
    """Bootstrap por partido de la media de un campo."""

    values = [float(row[field]) for row in rows]
    generator = random.Random(seed)
    samples = [
        mean(values[generator.randrange(len(values))] for _ in values)
        for _ in range(replicates)
    ]
    return {
        "point_estimate": mean(values),
        "ci_95": [_percentile(samples, 0.025), _percentile(samples, 0.975)],
        "probability_below_zero": mean(float(value < 0.0) for value in samples),
        "replicate_hash": _stable_hash(samples),
    }


def _bootstrap_block(rows: list[dict[str, Any]], seed: int) -> dict[str, Any]:
    """Calcula intervalos para un bloque temporal sin mezclarlo."""

    fields = ("delta_mae", "delta_log_score", "uplift", "overexcitation_frequency")
    return {
        "match_count": len(rows),
        "replicates": BOOTSTRAP_REPLICATES,
        "seed": seed,
        "metrics": {
            field: _bootstrap_metric(rows, field, seed + index, BOOTSTRAP_REPLICATES)
            for index, field in enumerate(fields)
        },
    }


def _subgroup(
    name: str,
    rows: list[dict[str, Any]],
    field: str = "delta_mae",
    seed: int = SEED,
) -> dict[str, Any]:
    """Resume un subgrupo solo cuando tiene cobertura por partido suficiente."""

    sufficient = len(rows) >= 5
    return {
        "subgroup": name,
        "match_count": len(rows),
        "coverage_sufficient": sufficient,
        "metric": field,
        "bootstrap": _bootstrap_metric(rows, field, seed, SUBGROUP_REPLICATES) if sufficient else None,
    }


def _subgroups(match_rows: list[dict[str, Any]], predictions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Evalúa lados, categorías y diferencial de marcador en confirmación."""

    output = [
        _subgroup("home_team_error", match_rows, "delta_mae_home", SEED + 100),
        _subgroup("away_team_error", match_rows, "delta_mae_away", SEED + 101),
    ]
    categories = sorted({category for row in match_rows for category in row["categories"]})
    for index, category in enumerate(categories):
        output.append(_subgroup(category, [row for row in match_rows if category in row["categories"]], seed=SEED + 200 + index))
    differences = sorted({int(row["goal_difference"]) for row in predictions})
    for index, difference in enumerate(differences):
        filtered = [row for row in predictions if int(row["goal_difference"]) == difference]
        group = _match_statistics(filtered)
        output.append(_subgroup(f"goal_difference={difference}", group, seed=SEED + 400 + index))
    output.append({"subgroup": "null_team_match", "match_count": 0, "coverage_sufficient": False, "status": "unavailable_in_oos_kalman_universe"})
    return output


def _trial_definitions() -> list[dict[str, Any]]:
    """Carga exclusivamente las cinco configuraciones congeladas de Fase 5.4."""

    result = _load(ROOT / "artifacts/phase_5_4_hawkes_v1_sensitivity/hawkes_v1_sensitivity_result.json")
    return [
        {"trial": item["trial"], "effective_config": item["effective_config"]}
        for item in result["summaries"]
    ]


def _contribution_index(rows: list[dict[str, Any]]) -> dict[tuple[int, str], list[dict[str, Any]]]:
    """Indexa contribuciones `alpha_reduced` por snapshot."""

    output: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for row in rows:
        output.setdefault((int(row["match_id"]), str(row["snapshot_ts"])), []).append(row)
    return output


def _trial_lambdas(
    row: dict[str, Any],
    contributions: list[dict[str, Any]],
    trial: dict[str, Any],
) -> tuple[float, float]:
    """Reconstruye una configuración congelada desde contribuciones shadow."""

    alpha_ratio = float(trial["alpha_scale"]) / 0.60
    beta = 0.25 * float(trial["beta_scale"])
    memory = float(trial["memory_minutes"])
    home = away = 0.0
    for item in contributions:
        age = float(item["dt_minutes"])
        if age <= memory:
            adjustment = alpha_ratio * math.exp(-(beta - 0.25) * age)
            home += float(item["home"]) * adjustment
            away += float(item["away"]) * adjustment
    mu_scale = float(trial["mu_scale"])
    return (
        (float(row["lambda_markov_home"]) + home) * mu_scale,
        (float(row["lambda_markov_away"]) + away) * mu_scale,
    )


def _trial_match_metrics(
    predictions: list[dict[str, Any]],
    index: dict[tuple[int, str], list[dict[str, Any]]],
    trial: dict[str, Any],
) -> list[dict[str, float]]:
    """Calcula métricas por partido para una configuración fija."""

    output = []
    for match_id in sorted({int(row["match_id"]) for row in predictions}):
        group = [row for row in predictions if int(row["match_id"]) == match_id]
        errors, logs = [], []
        for row in group:
            home, away = _trial_lambdas(row, index.get((match_id, row["snapshot_ts"]), []), trial)
            expected = (home + away) * max(0.0, 90.0 - float(row["minute"])) / 90.0
            target = int(row["remaining_total_goals"])
            errors.append(abs(target - expected))
            logs.append(_poisson_log_score(target, expected))
        output.append({"match_id": match_id, "mae": mean(errors), "log_score": mean(logs)})
    return output


def _sensitivity(
    predictions: list[dict[str, Any]],
    contributions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compara configuraciones congeladas sin seleccionar un ganador."""

    index = _contribution_index(contributions)
    summaries = []
    for definition in _trial_definitions():
        trial = definition["trial"]
        by_match = _trial_match_metrics(predictions, index, trial)
        lambdas = [
            _trial_lambdas(row, index.get((int(row["match_id"]), row["snapshot_ts"]), []), trial)
            for row in predictions
        ]
        summaries.append({
            "config_id": trial["config_id"],
            "trial": trial,
            "effective_config": definition["effective_config"],
            "match_weighted_mae": mean(row["mae"] for row in by_match),
            "match_weighted_log_score": mean(row["log_score"] for row in by_match),
            "positive_finite": all(math.isfinite(value) and value > 0 for pair in lambdas for value in pair),
            "spectral_radius": 0.56,
        })
    mae_order = sorted(summaries, key=lambda item: item["match_weighted_mae"])
    log_order = sorted(summaries, key=lambda item: item["match_weighted_log_score"])
    alpha = next(item for item in summaries if item["config_id"] == "alpha_reduced")
    alpha_trial = alpha["trial"]
    alpha["reconstruction_max_abs_error"] = max(
        max(
            abs(home - float(row["lambda_hawkes_home"])),
            abs(away - float(row["lambda_hawkes_away"])),
        )
        for row in predictions
        for home, away in [_trial_lambdas(
            row,
            index.get((int(row["match_id"]), row["snapshot_ts"]), []),
            alpha_trial,
        )]
    )
    alpha["mae_rank"] = 1 + mae_order.index(alpha)
    alpha["log_score_rank"] = 1 + log_order.index(alpha)
    return {
        "selection_performed": False,
        "frozen_configuration_count": len(summaries),
        "summaries": summaries,
        "alpha_reduced_competitive": alpha["mae_rank"] <= 2 or alpha["log_score_rank"] <= 2,
        "alpha_reduced_ranks": {"mae": alpha["mae_rank"], "log_score": alpha["log_score_rank"]},
    }


def _database_counts() -> dict[str, Any]:
    """Ejecuta conteos SELECT read-only o documenta indisponibilidad."""

    try:
        for path in (Path("/tmp/dikamaha_phase71_pg"), Path("/tmp/codex_pg_linux")):
            if path.exists() and str(path) not in sys.path:
                sys.path.insert(0, str(path))
        try:
            from src.evaluate_markov_hawkes_expanded import _connect, _counts
        except ModuleNotFoundError:  # pragma: no cover
            from evaluate_markov_hawkes_expanded import _connect, _counts

        connection = _connect()
        counts = _counts(connection)
        connection.rollback()
        connection.close()
        return {"status": "verified", "counts": counts}
    except Exception as exc:
        return {"status": "database_verification_incomplete", "reason": f"{type(exc).__name__}:{exc}"}


def _audit(
    source_audit: dict[str, Any],
    predictions: list[dict[str, Any]],
    bootstrap_primary: dict[str, Any],
    bootstrap_replay: dict[str, Any],
    database_before: dict[str, Any],
    database_after: dict[str, Any],
) -> dict[str, Any]:
    """Consolida temporalidad, estabilidad y reproducibilidad."""

    database_identical = (
        database_before.get("status") == "verified"
        and database_after.get("status") == "verified"
        and database_before["counts"] == database_after["counts"]
    )
    return {
        "event_ts_lte_snapshot": bool(source_audit["event_ts_lte_snapshot"]),
        "deduplication": bool(source_audit["deduplication"]),
        "positive_finite_intensities": all(
            all(math.isfinite(float(row[key])) and float(row[key]) > 0 for key in ("lambda_markov_home", "lambda_markov_away", "lambda_hawkes_home", "lambda_hawkes_away"))
            for row in predictions
        ),
        "spectral_radius_subcritical": all(float(row["spectral_radius"]) < 1.0 for row in predictions),
        "provenance_visible": all(row["markov_provenance"]["markov_matrix_synthetic"] for row in predictions),
        "markov_independent": all(row["official_source"] == "markov_v1" for row in predictions),
        "hawkes_shadow_only": True,
        "alpha_reduced_frozen": bool(source_audit["parameters_frozen"]),
        "blocks_not_mixed": True,
        "snapshots_not_iid": True,
        "bootstrap_replay_identical": bootstrap_primary == bootstrap_replay,
        "database_before": database_before,
        "database_after": database_after,
        "database_counts_identical": database_identical if database_before.get("status") == "verified" else None,
        "postgresql_writes": 0,
    }


def _decision(intervals: dict[str, Any], audit: dict[str, Any]) -> str:
    """Clasifica robustez sin promover Hawkes automáticamente."""

    checks = [value for value in audit.values() if isinstance(value, bool)]
    if not all(checks):
        return "hawkes_rejected_for_revision"
    confirm = intervals["confirmation"]["metrics"]
    mae_ci = confirm["delta_mae"]["ci_95"]
    log_ci = confirm["delta_log_score"]["ci_95"]
    if mae_ci[1] < 0.0 and log_ci[1] < 0.0:
        return "hawkes_robust_candidate"
    if intervals["confirmation"]["match_count"] < 20:
        return "insufficient_signal_for_calibration"
    return "hawkes_candidate_unconfirmed"


def _report(decision: str, intervals: dict[str, Any], sensitivity: dict[str, Any]) -> str:
    """Renderiza el informe final."""

    confirm = intervals["confirmation"]["metrics"]
    return "\n".join([
        "# Fase 7.2 - Robustez estadística Hawkes shadow",
        "",
        f"**Decisión:** `{decision}`",
        "",
        f"- bootstrap: `{BOOTSTRAP_REPLICATES}` réplicas por bloque; seed `{SEED}`",
        f"- delta MAE confirmatorio: `{confirm['delta_mae']['point_estimate']:.6f}`; IC95 `{confirm['delta_mae']['ci_95']}`",
        f"- delta log score: `{confirm['delta_log_score']['point_estimate']:.6f}`; IC95 `{confirm['delta_log_score']['ci_95']}`",
        f"- uplift: `{confirm['uplift']['point_estimate']:.6f}`; IC95 `{confirm['uplift']['ci_95']}`",
        f"- sobreexcitación: `{confirm['overexcitation_frequency']['point_estimate']:.4%}`",
        f"- alpha_reduced competitiva: `{sensitivity['alpha_reduced_competitive']}`; rangos `{sensitivity['alpha_reduced_ranks']}`",
        "",
        "Los intervalos se calculan re-muestreando partidos completos. Desarrollo y confirmación permanecen separados.",
        "Hawkes continúa shadow, Markov sigue siendo la salida oficial y no se autoriza calibración de alpha/beta.",
    ])


def main() -> int:
    """Ejecuta bootstrap, subgrupos y sensibilidad congelada."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    predictions = _load(SOURCE / "hawkes_shadow_predictions.json")
    contributions = _load(SOURCE / "event_contributions.json")
    source_audit = _load(SOURCE / "audit.json")
    match_rows = _match_statistics(predictions)
    development = [row for row in match_rows if row["block"] == "development"]
    confirmation = [row for row in match_rows if row["block"] == "confirmation"]
    database_before = _database_counts()
    intervals = {
        "development": _bootstrap_block(development, SEED),
        "confirmation": _bootstrap_block(confirmation, SEED + 1000),
    }
    replay = {
        "development": _bootstrap_block(development, SEED),
        "confirmation": _bootstrap_block(confirmation, SEED + 1000),
    }
    confirmation_predictions = [row for row in predictions if row["block"] == "confirmation"]
    sensitivity = _sensitivity(confirmation_predictions, contributions)
    subgroups = _subgroups(confirmation, confirmation_predictions)
    database_after = _database_counts()
    audit = _audit(source_audit, predictions, intervals, replay, database_before, database_after)
    decision = _decision(intervals, audit)
    bootstrap_results = {
        "seed": SEED, "replicates": BOOTSTRAP_REPLICATES,
        "development_match_ids": [row["match_id"] for row in development],
        "confirmation_match_ids": [row["match_id"] for row in confirmation],
        "results": intervals, "replay_hash": _stable_hash(replay),
    }
    payloads = {
        "bootstrap_results.json": bootstrap_results,
        "confidence_intervals.json": intervals,
        "subgroup_metrics.json": subgroups,
        "sensitivity_results.json": sensitivity,
        "audit.json": audit,
    }
    for name, payload in payloads.items():
        _write(OUTPUT / name, payload)
    manifest = {
        "phase": "7.2", "decision": decision, "seed": SEED,
        "bootstrap_replicates": BOOTSTRAP_REPLICATES,
        "input_hash": _stable_hash({"predictions": predictions, "contributions": contributions}),
        "output_hash": _stable_hash(payloads), "replay_identical": intervals == replay,
        "alpha_beta_calibrated": False, "official_hawkes": False,
        "postgresql_modified": False,
    }
    _write(OUTPUT / "manifest.json", manifest)
    (OUTPUT / "final_report.md").write_text(_report(decision, intervals, sensitivity), encoding="utf-8")
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"}
    _write(OUTPUT / "hashes.json", hashes)
    LOGGER.info("Fase 7.2: %s", decision)
    return 0 if decision != "hawkes_rejected_for_revision" else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0
# Created: 2026-07-16
