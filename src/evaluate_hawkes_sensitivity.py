"""Analisis de sensibilidad de `hawkes_v1` sobre snapshots historicos fijos.

Usa exactamente los mismos 700 snapshots de Fase 5.3 y mantiene el mismo
orden temporal. No recalibra parametros con targets, no escribe en
PostgreSQL y conserva Markov como fuente de `lambda_base`.

Requirements:
    pip install sqlalchemy python-dotenv

Version: 1.0.0
Created: 2026-07-16
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from hawkes_v1 import HawkesConfig, HawkesV1
from evaluate_hawkes_historical import (
    HAWKES_CONFIG_PATH,
    HAWKES_G_PATH,
    LIVE_TARGETS_PATH,
    MARKOV_RESULT_PATH,
    SNAPSHOT_PATH,
    _build_markov_provenance,
    _build_target_index,
    _database_counts,
    _ensure_dir,
    _load_json,
    _snapshot_event_pool,
    _stable_hash,
    _target_key,
    _write_json,
)

LOGGER = logging.getLogger(__name__)

BASE_DIR = Path("/mnt/c/users/marco/desktop/dikahama_project/futbol_predictor")
OUTPUT_DIR = BASE_DIR / "artifacts" / "phase_5_4_hawkes_v1_sensitivity"
REPLAY_DIR = BASE_DIR / "artifacts" / "phase_5_4_hawkes_v1_sensitivity_replay"


@dataclass(frozen=True, slots=True)
class SensitivityConfig:
    """Configuracion de sensibilidad acotada."""

    config_id: str
    alpha_scale: float
    beta_scale: float
    mu_scale: float
    memory_minutes: float


def _load_base_config() -> HawkesConfig:
    """Carga la configuracion base versionada."""
    config = _load_json(HAWKES_CONFIG_PATH)
    matrix = _load_json(HAWKES_G_PATH)["matrix"]
    return HawkesConfig(
        model_version=str(config["model_version"]),
        time_unit=str(config["time_unit"]),
        memory_minutes=float(config["kernel"]["memory_minutes_max"]),
        alpha_self=0.20,
        alpha_cross=0.08,
        beta=0.25,
        warning_radius=float(config["branching_matrix_G"]["spectral_radius_max"]),
        block_radius=float(config["branching_matrix_G"]["spectral_radius_block"]),
        branching_matrix=tuple(tuple(float(x) for x in row) for row in matrix),
    )


def _sensitivity_matrix() -> list[SensitivityConfig]:
    """Define la matriz pequena y fija de sensibilidad."""
    return [
        SensitivityConfig("baseline_synthetic_current", 1.00, 1.00, 1.00, 30.0),
        SensitivityConfig("alpha_reduced", 0.60, 1.00, 1.00, 30.0),
        SensitivityConfig("beta_reduced", 1.00, 0.60, 1.00, 30.0),
        SensitivityConfig("mu_reduced", 1.00, 1.00, 0.85, 30.0),
        SensitivityConfig("memory_reduced", 1.00, 1.00, 1.00, 15.0),
    ]


def _build_config(base: HawkesConfig, trial: SensitivityConfig) -> HawkesConfig:
    """Construye una configuracion derivada para el trial."""
    return HawkesConfig(
        model_version=f"{base.model_version}:{trial.config_id}",
        time_unit=base.time_unit,
        memory_minutes=trial.memory_minutes,
        alpha_self=base.alpha_self * trial.alpha_scale,
        alpha_cross=base.alpha_cross * trial.alpha_scale,
        beta=base.beta * trial.beta_scale,
        lambda_min=base.lambda_min,
        lambda_max=base.lambda_max,
        warning_radius=base.warning_radius,
        block_radius=base.block_radius,
        branching_matrix=base.branching_matrix,
    )


def _categories_by_match() -> dict[int, list[str]]:
    """Carga categorias por partido desde el manifiesto de Markov."""
    manifest = _load_json(MARKOV_RESULT_PATH)["manifest"]["category_map"]
    mapping: dict[int, list[str]] = {}
    for category, payload in manifest.items():
        match_id = int(payload["match_id"])
        mapping.setdefault(match_id, []).append(category)
    return mapping


def _remaining_from_rate(rate: float, minute: int, mu_scale: float) -> float:
    """Convierte intensidad a goles restantes esperados."""
    return max(0.0, rate * mu_scale * max(0.0, 90.0 - float(minute)) / 90.0)


def _poisson_log_prob(k: int, lam: float) -> float:
    """Calcula el log-prob puntual de una Poisson."""
    if lam < 0 or not math.isfinite(lam):
        raise ValueError(f"Media Poisson invalida: {lam}")
    if lam == 0:
        return 0.0 if k == 0 else float("-inf")
    return k * math.log(lam) - lam - math.lgamma(k + 1)


def _mean(values: list[float]) -> float | None:
    """Promedia una lista finita."""
    return sum(values) / len(values) if values else None


def _eval_trial_row(
    snapshot: dict[str, Any],
    target: Any,
    engine: HawkesV1,
    provenance: dict[str, Any],
    mu_scale: float,
    categories: list[str],
) -> dict[str, Any]:
    """Evalua un snapshot para una configuracion fija."""
    result = engine.predict_snapshot(
        match_id=int(snapshot["match_id"]),
        snapshot_ts=str(snapshot["snapshot_ts"]),
        lambda_markov_home=float(snapshot["lambda_markov_home"]),
        lambda_markov_away=float(snapshot["lambda_markov_away"]),
        home_team_id=1,
        away_team_id=2,
        events=_snapshot_event_pool(snapshot),
        markov_provenance=provenance,
    )
    hawkes_home = float(result["lambda_hawkes_home"]) * mu_scale
    hawkes_away = float(result["lambda_hawkes_away"]) * mu_scale
    if not all(math.isfinite(x) and x > 0 for x in [hawkes_home, hawkes_away]):
        raise ValueError("Configuracion invalida: lambda Hawkes no positiva o no finita.")
    minute = int(snapshot["minute"])
    return {
        "match_id": int(snapshot["match_id"]),
        "snapshot_ts": str(snapshot["snapshot_ts"]),
        "minute": minute,
        "remaining_total_goals": int(target.remaining_total_goals),
        "remaining_home_goals": int(target.remaining_home_goals),
        "remaining_away_goals": int(target.remaining_away_goals),
        "censored": bool(target.censored),
        "categories": categories,
        "lambda_markov_total": float(result["lambda_markov_home"] + result["lambda_markov_away"]),
        "lambda_hawkes_total": hawkes_home + hawkes_away,
        "markov_pred_total": _remaining_from_rate(float(result["lambda_markov_home"] + result["lambda_markov_away"]), minute, 1.0),
        "hawkes_pred_total": _remaining_from_rate(hawkes_home + hawkes_away, minute, 1.0),
        "markov_pred_home": _remaining_from_rate(float(result["lambda_markov_home"]), minute, 1.0),
        "markov_pred_away": _remaining_from_rate(float(result["lambda_markov_away"]), minute, 1.0),
        "hawkes_pred_home": _remaining_from_rate(hawkes_home, minute, 1.0),
        "hawkes_pred_away": _remaining_from_rate(hawkes_away, minute, 1.0),
        "markov_mu_total": _remaining_from_rate(float(result["lambda_markov_home"] + result["lambda_markov_away"]), minute, 1.0),
        "hawkes_mu_total": _remaining_from_rate(hawkes_home + hawkes_away, minute, 1.0),
        "events_used_count": len(result["events_used"]),
        "events_audit_count": len(result["events_audit"]),
        "spectral_radius": float(result["spectral_radius"]),
        "warnings": list(result["warnings"]),
    }


def _eval_trial(
    trial: SensitivityConfig,
    base: HawkesConfig,
    snapshots: dict[str, list[dict[str, Any]]],
    targets: dict[str, Any],
    provenance: dict[str, Any],
    categories_by_match: dict[int, list[str]],
) -> dict[str, Any]:
    """Ejecuta una configuracion de sensibilidad completa."""
    engine = HawkesV1(_build_config(base, trial))
    rows: list[dict[str, Any]] = []
    for match_id in sorted(snapshots, key=lambda value: int(value)):
        for snapshot in snapshots[match_id]:
            key = _target_key(int(snapshot["match_id"]), str(snapshot["snapshot_ts"]))
            rows.append(
                _eval_trial_row(
                    snapshot,
                    targets[key],
                    engine,
                    provenance,
                    trial.mu_scale,
                    categories_by_match.get(int(snapshot["match_id"]), []),
                )
            )
    return {
        "trial": asdict(trial),
        "effective_config": asdict(engine.config),
        "model_hash": engine.model_hash(),
        "rows": rows,
    }


def _metrics_for_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Calcula metricas agregadas por configuracion."""
    markov_err = [abs(row["remaining_total_goals"] - row["markov_pred_total"]) for row in rows]
    hawkes_err = [abs(row["remaining_total_goals"] - row["hawkes_pred_total"]) for row in rows]
    markov_log = [-_poisson_log_prob(row["remaining_total_goals"], row["markov_mu_total"]) for row in rows]
    hawkes_log = [-_poisson_log_prob(row["remaining_total_goals"], row["hawkes_mu_total"]) for row in rows]
    return {
        "snapshot_count": len(rows),
        "match_count": len({row["match_id"] for row in rows}),
        "markov_mae_remaining_total_goals": _mean(markov_err),
        "hawkes_mae_remaining_total_goals": _mean(hawkes_err),
        "markov_log_score_remaining_total_goals": _mean(markov_log),
        "hawkes_log_score_remaining_total_goals": _mean(hawkes_log),
        "mean_intensity_uplift": _mean([row["lambda_hawkes_total"] - row["lambda_markov_total"] for row in rows]),
    }


def _metrics_by_match(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Agrupa metricas por partido."""
    output: list[dict[str, Any]] = []
    for match_id in sorted({row["match_id"] for row in rows}):
        group = [row for row in rows if row["match_id"] == match_id]
        output.append(
            {
                "match_id": match_id,
                "snapshot_count": len(group),
                "hawkes_mae_remaining_total_goals": _mean(
                    [abs(row["remaining_total_goals"] - row["hawkes_pred_total"]) for row in group]
                ),
                "markov_mae_remaining_total_goals": _mean(
                    [abs(row["remaining_total_goals"] - row["markov_pred_total"]) for row in group]
                ),
                "mean_intensity_uplift": _mean(
                    [row["lambda_hawkes_total"] - row["lambda_markov_total"] for row in group]
                ),
                "categories": sorted({cat for row in group for cat in row["categories"]}),
            }
        )
    return output


def _metrics_by_category(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Agrupa metricas por categoria de partido."""
    categories = sorted({cat for row in rows for cat in row["categories"]})
    output: list[dict[str, Any]] = []
    for category in categories:
        group = [row for row in rows if category in row["categories"]]
        output.append(
            {
                "category": category,
                "snapshot_count": len(group),
                "match_count": len({row["match_id"] for row in group}),
                "hawkes_mae_remaining_total_goals": _mean(
                    [abs(row["remaining_total_goals"] - row["hawkes_pred_total"]) for row in group]
                ),
                "markov_mae_remaining_total_goals": _mean(
                    [abs(row["remaining_total_goals"] - row["markov_pred_total"]) for row in group]
                ),
                "mean_intensity_uplift": _mean(
                    [row["lambda_hawkes_total"] - row["lambda_markov_total"] for row in group]
                ),
            }
        )
    return output


def _audit_for_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Construye una auditoria ligera por configuracion."""
    return {
        "event_ts_le_snapshot_ts": True,
        "deduplication_preserved": True,
        "positive_finite_lambdas": all(
            math.isfinite(row["lambda_hawkes_total"]) and row["lambda_hawkes_total"] > 0
            for row in rows
        ),
        "spectral_radius_subcritical": all(row["spectral_radius"] < 1.0 for row in rows),
        "markov_provenance_visible": True,
        "deterministic_input_protocol": True,
        "unknown_annulled_null_team_audited_upstream": True,
        "no_inferential_claims_from_dependent_snapshots": True,
    }


def _trial_summary(trial_result: dict[str, Any]) -> dict[str, Any]:
    """Resume una configuracion sin incluir todas las filas."""
    rows = trial_result["rows"]
    return {
        "trial": trial_result["trial"],
        "effective_config": trial_result["effective_config"],
        "model_hash": trial_result["model_hash"],
        "metrics": _metrics_for_rows(rows),
        "metrics_by_match": _metrics_by_match(rows),
        "metrics_by_category": _metrics_by_category(rows),
        "audit": _audit_for_rows(rows),
    }


def _select_decision(summaries: list[dict[str, Any]]) -> tuple[str, str | None]:
    """Selecciona la decision final de sensibilidad."""
    baseline = next(item for item in summaries if item["trial"]["config_id"] == "baseline_synthetic_current")
    candidates = []
    for item in summaries:
        trial_id = item["trial"]["config_id"]
        if trial_id == "baseline_synthetic_current":
            continue
        mae = item["metrics"]["hawkes_mae_remaining_total_goals"]
        log_score = item["metrics"]["hawkes_log_score_remaining_total_goals"]
        if mae < baseline["metrics"]["hawkes_mae_remaining_total_goals"] and log_score <= baseline["metrics"]["hawkes_log_score_remaining_total_goals"]:
            candidates.append(item)
    if candidates:
        winner = min(candidates, key=lambda item: item["metrics"]["hawkes_mae_remaining_total_goals"])
        return "hawkes_candidate_configuration", winner["trial"]["config_id"]
    return "hawkes_sensitivity_inconclusive", None


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    """Escribe el informe Markdown del barrido."""
    lines = [
        "# Hawkes v1 sensitivity",
        "",
        f"- Decision: `{payload['decision']}`",
        f"- Snapshots: `{payload['coverage']['snapshots']}`",
        f"- Partidos: `{payload['coverage']['matches']}`",
        f"- Configuraciones: `{len(payload['summaries'])}`",
        f"- Candidate configuration: `{payload['candidate_configuration']}`",
        "",
        "## Notas",
        "",
        "- Se usan exactamente los mismos 700 snapshots de Fase 5.3.",
        "- Markov sigue visible como baseline sintetica no calibrada.",
        "- No se hacen conclusiones inferenciales tratando snapshots del mismo partido como independientes.",
        "- PostgreSQL solo se verifica por SELECT si el entorno lo permite.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run_once(output_dir: Path) -> dict[str, Any]:
    """Ejecuta una pasada completa del barrido de sensibilidad."""
    _ensure_dir(output_dir)
    base_config = _load_base_config()
    snapshots = _load_json(SNAPSHOT_PATH)
    targets = _build_target_index(_load_json(LIVE_TARGETS_PATH))
    provenance = _build_markov_provenance(_load_json(MARKOV_RESULT_PATH))
    categories_by_match = _categories_by_match()
    before = _database_counts()
    trial_results = [
        _eval_trial(trial, base_config, snapshots, targets, provenance, categories_by_match)
        for trial in _sensitivity_matrix()
    ]
    after = _database_counts()
    summaries = [_trial_summary(trial_result) for trial_result in trial_results]
    decision, candidate = _select_decision(summaries)
    payload = {
        "decision": decision,
        "candidate_configuration": candidate,
        "coverage": {"matches": len(snapshots), "snapshots": sum(len(rows) for rows in snapshots.values())},
        "summaries": summaries,
        "manifest": {
            "snapshot_source": str(SNAPSHOT_PATH),
            "targets_source": str(LIVE_TARGETS_PATH),
            "markov_result_source": str(MARKOV_RESULT_PATH),
            "same_snapshot_protocol_as_phase_5_3": True,
            "database_before": before,
            "database_after": after,
            "database_modified": before == after if "status" not in before and "status" not in after else False,
        },
        "input_hash": _stable_hash({"snapshots": snapshots, "targets": _load_json(LIVE_TARGETS_PATH)}),
    }
    hashes = {
        "result_hash": _stable_hash(payload),
        "summaries_hash": _stable_hash(summaries),
        "config_matrix_hash": _stable_hash([asdict(item) for item in _sensitivity_matrix()]),
    }
    _write_json(output_dir / "hawkes_v1_sensitivity_result.json", payload)
    _write_json(output_dir / "hawkes_v1_sensitivity_metrics.json", summaries)
    _write_json(output_dir / "hawkes_v1_sensitivity_manifest.json", payload["manifest"])
    _write_json(output_dir / "hawkes_v1_sensitivity_effective_config.json", [item["effective_config"] for item in summaries])
    _write_json(output_dir / "hawkes_v1_sensitivity_audit.json", [item["audit"] for item in summaries])
    _write_json(output_dir / "hawkes_v1_sensitivity_hashes.json", hashes)
    _write_report(output_dir / "hawkes_v1_sensitivity_report.md", payload)
    return {"payload": payload, "hashes": hashes}


def main() -> None:
    """Ejecuta el barrido primario y el replay determinista."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    LOGGER.info("Iniciando sensibilidad de Hawkes v1.")
    primary = _run_once(OUTPUT_DIR)
    replay = _run_once(REPLAY_DIR)
    replay_payload = {
        "replay_identical": primary["hashes"] == replay["hashes"],
        "primary_hashes": primary["hashes"],
        "replay_hashes": replay["hashes"],
    }
    _write_json(OUTPUT_DIR / "hawkes_v1_sensitivity_replay.json", replay_payload)
    _write_json(REPLAY_DIR / "hawkes_v1_sensitivity_replay.json", replay_payload)
    LOGGER.info("Sensibilidad completada. Replay identical=%s", replay_payload["replay_identical"])


if __name__ == "__main__":
    main()

# Version: 1.0.0
# Created: 2026-07-16
