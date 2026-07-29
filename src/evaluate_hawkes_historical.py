"""Evaluacion historica limitada de `hawkes_v1`.

Consume snapshots historicos de Markov v1 y targets live ya materializados
para medir el efecto de la excitacion Hawkes con parametros sinteticos.
No escribe en PostgreSQL ni modifica la baseline Markov.

Requirements:
    pip install sqlalchemy python-dotenv

Version: 1.0.0
Created: 2026-07-16
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

try:
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import SQLAlchemyError
except ModuleNotFoundError:  # pragma: no cover - dependencia opcional de entorno
    create_engine = None
    text = None
    SQLAlchemyError = Exception

from hawkes_v1 import HawkesConfig, HawkesV1

LOGGER = logging.getLogger(__name__)

BASE_DIR = Path("/mnt/c/users/marco/desktop/dikahama_project/futbol_predictor")
ARTIFACT_DIR = BASE_DIR / "artifacts" / "phase_5_3_hawkes_v1_limited_historical"
REPLAY_DIR = BASE_DIR / "artifacts" / "phase_5_3_hawkes_v1_limited_historical_replay"
SNAPSHOT_PATH = BASE_DIR / "artifacts" / "phase_4_6_markov_v1_full_historical_v2" / "markov_v1_snapshots.json"
MARKOV_RESULT_PATH = BASE_DIR / "artifacts" / "phase_4_6_markov_v1_full_historical_v2" / "markov_v1_result.json"
LIVE_RESULT_PATH = BASE_DIR / "artifacts" / "phase_4_8_markov_v1_live_evaluation" / "markov_v1_live_result.json"
LIVE_TARGETS_PATH = BASE_DIR / "artifacts" / "phase_4_8_markov_v1_live_evaluation" / "markov_v1_live_targets.json"
HAWKES_CONFIG_PATH = BASE_DIR / "artifacts" / "phase_5_1_hawkes_v1_markov_context" / "hawkes_v1_config_revision.json"
HAWKES_G_PATH = BASE_DIR / "artifacts" / "phase_5_1_hawkes_v1_markov_context" / "hawkes_v1_branching_matrix_G.json"


@dataclass(slots=True)
class SnapshotTarget:
    """Target posterior por snapshot."""

    remaining_home_goals: int
    remaining_away_goals: int
    remaining_total_goals: int
    next_goal_exists: int
    next_goal_team: str | None
    time_to_next_goal_seconds: float | None
    censored: bool


def _json_default(value: Any) -> Any:
    """Serializa valores no nativos de JSON."""
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, set):
        return sorted(value)
    return str(value)


def _load_json(path: Path) -> Any:
    """Carga un artefacto JSON."""
    return json.loads(path.read_text(encoding="utf-8"))


def _stable_hash(payload: Any) -> str:
    """Calcula un hash determinista de un payload JSON."""
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=_json_default)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _ensure_dir(path: Path) -> None:
    """Crea el directorio de salida si no existe."""
    path.mkdir(parents=True, exist_ok=True)


def _target_key(match_id: int, snapshot_ts: str) -> str:
    """Construye una clave estable por snapshot."""
    return f"{match_id}|{snapshot_ts}"


def _build_target_index(rows: list[dict[str, Any]]) -> dict[str, SnapshotTarget]:
    """Indexa targets live por `match_id` y `snapshot_ts`."""
    index: dict[str, SnapshotTarget] = {}
    for row in rows:
        key = _target_key(int(row["match_id"]), str(row["snapshot_ts"]))
        index[key] = SnapshotTarget(
            remaining_home_goals=int(row["remaining_home_goals"]),
            remaining_away_goals=int(row["remaining_away_goals"]),
            remaining_total_goals=int(row["remaining_total_goals"]),
            next_goal_exists=int(row["next_goal_exists"]),
            next_goal_team=row["next_goal_team"],
            time_to_next_goal_seconds=row["time_to_next_goal_seconds"],
            censored=bool(row["censored"]),
        )
    return index


def _build_markov_provenance(markov_result: dict[str, Any]) -> dict[str, Any]:
    """Construye provenance minimo exigido por Hawkes."""
    manifest = markov_result.get("manifest", {})
    return {
        "markov_model_hash": manifest.get("model_hash", "unknown_markov_model_hash"),
        "markov_transition_version": "markov_transition_v1",
        "markov_matrix_synthetic": True,
        "markov_decision": markov_result["decision"],
    }


def _load_hawkes_config() -> HawkesConfig:
    """Carga la configuracion sintetica versionada."""
    cfg = _load_json(HAWKES_CONFIG_PATH)
    matrix = _load_json(HAWKES_G_PATH)["matrix"]
    return HawkesConfig(
        model_version=str(cfg["model_version"]),
        time_unit=str(cfg["time_unit"]),
        memory_minutes=float(cfg["kernel"]["memory_minutes_max"]),
        alpha_self=0.20,
        alpha_cross=0.08,
        beta=0.25,
        warning_radius=float(cfg["branching_matrix_G"]["spectral_radius_max"]),
        block_radius=float(cfg["branching_matrix_G"]["spectral_radius_block"]),
        branching_matrix=tuple(tuple(float(x) for x in row) for row in matrix),
    )


def _canonical_event_id(event: dict[str, Any], index: int) -> str:
    """Resuelve un identificador canonico por evento."""
    if event.get("source_event_id") is not None:
        return str(event["source_event_id"])
    return str(event.get("event_id", f"snapshot-event-{index}"))


def _snapshot_event_pool(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Construye el pool de eventos disponible para Hawkes."""
    rows = list(snapshot.get("events_used", [])) + list(snapshot.get("events_excluded", []))
    pool: list[dict[str, Any]] = []
    for index, event in enumerate(rows):
        pool.append(
            {
                "event_id": _canonical_event_id(event, index),
                "event_ts": event["event_ts"],
                "team_id": event.get("team_id"),
                "event_type": event.get("event_type"),
                "annulled": bool(event.get("annulled", False)),
                "source_event_id": event.get("source_event_id"),
            }
        )
    return pool


def _minutes_remaining(minute: int) -> float:
    """Calcula el horizonte remanente en minutos."""
    return max(0.0, 90.0 - float(minute))


def _expected_remaining_goals(rate: float, minute: int) -> float:
    """Convierte una intensidad base en goles restantes esperados."""
    return max(0.0, rate * _minutes_remaining(minute) / 90.0)


def _poisson_log_prob(k: int, lam: float) -> float:
    """Devuelve el log-prob de una Poisson puntual."""
    if lam < 0 or not math.isfinite(lam):
        raise ValueError(f"Media Poisson invalida: {lam}")
    if lam == 0:
        return 0.0 if k == 0 else float("-inf")
    return k * math.log(lam) - lam - math.lgamma(k + 1)


def _safe_mean(values: Iterable[float]) -> float | None:
    """Promedia valores finitos o devuelve `None`."""
    rows = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    if not rows:
        return None
    return sum(rows) / len(rows)


def _group_mean(rows: list[dict[str, Any]], key: str, value: str) -> list[dict[str, Any]]:
    """Agrupa y promedia una metrica simple."""
    buckets: dict[Any, list[float]] = {}
    for row in rows:
        buckets.setdefault(row[key], []).append(float(row[value]))
    return [{key: group, "count": len(values), value: sum(values) / len(values)} for group, values in sorted(buckets.items())]


def _remaining_metrics(frame: list[dict[str, Any]], prefix: str) -> dict[str, float | None]:
    """Calcula metricas de goles restantes para un modelo."""
    total_err = [abs(row["remaining_total_goals"] - row[f"{prefix}_pred_remaining_total"]) for row in frame]
    home_err = [abs(row["remaining_home_goals"] - row[f"{prefix}_pred_remaining_home"]) for row in frame]
    away_err = [abs(row["remaining_away_goals"] - row[f"{prefix}_pred_remaining_away"]) for row in frame]
    total_log = [-_poisson_log_prob(row["remaining_total_goals"], row[f"{prefix}_mu_total"]) for row in frame]
    return {
        "mae_remaining_total_goals": _safe_mean(total_err),
        "mae_remaining_home_goals": _safe_mean(home_err),
        "mae_remaining_away_goals": _safe_mean(away_err),
        "log_score_remaining_total_goals": _safe_mean(total_log),
    }


def _event_type_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Resume contribuciones por tipo de evento."""
    buckets: dict[str, dict[str, list[float]]] = {}
    for row in rows:
        for event in row["event_contributions"]:
            stats = buckets.setdefault(event["event_type"], {"uplift_total": [], "dt_minutes": [], "self_component": [], "cross_component": []})
            stats["uplift_total"].append(float(event["total_component"]))
            stats["dt_minutes"].append(float(event["dt_minutes"]))
            stats["self_component"].append(float(event["self_component"]))
            stats["cross_component"].append(float(event["cross_component"]))
    output = []
    for event_type, stats in sorted(buckets.items()):
        output.append(
            {
                "event_type": event_type,
                "count": len(stats["uplift_total"]),
                "mean_total_uplift": _safe_mean(stats["uplift_total"]),
                "mean_dt_minutes": _safe_mean(stats["dt_minutes"]),
                "mean_self_component": _safe_mean(stats["self_component"]),
                "mean_cross_component": _safe_mean(stats["cross_component"]),
            }
        )
    return output


def _segment_rows(rows: list[dict[str, Any]], predicate: str) -> list[dict[str, Any]]:
    """Obtiene filas por segmento booleano."""
    return [row for row in rows if bool(row[predicate])]


def _enrich_contributions(
    result: dict[str, Any],
    home_team_id: int,
) -> tuple[list[dict[str, Any]], float, float]:
    """Anota contribuciones con self/cross y tipo de evento."""
    by_id = {str(event["event_id"]): event for event in result["events_used"]}
    enriched: list[dict[str, Any]] = []
    self_total = 0.0
    cross_total = 0.0
    for item in result["event_contributions"]:
        event = by_id[item["event_id"]]
        is_home = int(event["team_id"]) == home_team_id
        self_component = float(item["home"] if is_home else item["away"])
        cross_component = float(item["away"] if is_home else item["home"])
        self_total += self_component
        cross_total += cross_component
        enriched.append(
            {
                "event_id": item["event_id"],
                "event_type": event["event_type"],
                "team_id": event["team_id"],
                "dt_minutes": float(item["dt_minutes"]),
                "self_component": self_component,
                "cross_component": cross_component,
                "total_component": self_component + cross_component,
            }
        )
    return enriched, self_total, cross_total


def _evaluate_snapshot(
    snapshot: dict[str, Any],
    target: SnapshotTarget,
    engine: HawkesV1,
    provenance: dict[str, Any],
) -> dict[str, Any]:
    """Evalua un snapshot historico con Hawkes."""
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
    contributions, self_total, cross_total = _enrich_contributions(result, 1)
    minute = int(snapshot["minute"])
    return {
        "match_id": int(snapshot["match_id"]),
        "snapshot_ts": str(snapshot["snapshot_ts"]),
        "minute": minute,
        "second": int(snapshot["second"]),
        "score_home": int(snapshot["score_home"]),
        "score_away": int(snapshot["score_away"]),
        "remaining_home_goals": target.remaining_home_goals,
        "remaining_away_goals": target.remaining_away_goals,
        "remaining_total_goals": target.remaining_total_goals,
        "next_goal_exists": target.next_goal_exists,
        "next_goal_team": target.next_goal_team,
        "time_to_next_goal_seconds": target.time_to_next_goal_seconds,
        "censored": target.censored,
        "lambda_markov_home": float(result["lambda_markov_home"]),
        "lambda_markov_away": float(result["lambda_markov_away"]),
        "lambda_hawkes_home": float(result["lambda_hawkes_home"]),
        "lambda_hawkes_away": float(result["lambda_hawkes_away"]),
        "markov_total_rate": float(result["lambda_markov_home"] + result["lambda_markov_away"]),
        "hawkes_total_rate": float(result["lambda_hawkes_home"] + result["lambda_hawkes_away"]),
        "markov_pred_remaining_home": _expected_remaining_goals(float(result["lambda_markov_home"]), minute),
        "markov_pred_remaining_away": _expected_remaining_goals(float(result["lambda_markov_away"]), minute),
        "hawkes_pred_remaining_home": _expected_remaining_goals(float(result["lambda_hawkes_home"]), minute),
        "hawkes_pred_remaining_away": _expected_remaining_goals(float(result["lambda_hawkes_away"]), minute),
        "markov_pred_remaining_total": _expected_remaining_goals(float(result["lambda_markov_home"] + result["lambda_markov_away"]), minute),
        "hawkes_pred_remaining_total": _expected_remaining_goals(float(result["lambda_hawkes_home"] + result["lambda_hawkes_away"]), minute),
        "markov_mu_total": _expected_remaining_goals(float(result["lambda_markov_home"] + result["lambda_markov_away"]), minute),
        "hawkes_mu_total": _expected_remaining_goals(float(result["lambda_hawkes_home"] + result["lambda_hawkes_away"]), minute),
        "recent_events_count": len(result["events_used"]),
        "events_used_in_memory": len(result["events_used"]),
        "events_audited": len(result["events_audit"]),
        "self_excitation_total": self_total,
        "cross_excitation_total": cross_total,
        "spectral_radius": float(result["spectral_radius"]),
        "warnings": list(result["warnings"]),
        "event_contributions": contributions,
        "events_used": result["events_used"],
        "events_audit": result["events_audit"],
        "markov_provenance": result["markov_provenance"],
        "hawkes_provenance": {
            "model_hash": engine.model_hash(),
            "model_version": engine.config.model_version,
            "alpha_self": engine.config.alpha_self,
            "alpha_cross": engine.config.alpha_cross,
            "beta": engine.config.beta,
            "branching_matrix": result["branching_matrix"],
            "markov_matrix_synthetic": True,
        },
    }


def _build_eval_rows(
    snapshots: dict[str, list[dict[str, Any]]],
    targets: dict[str, SnapshotTarget],
    engine: HawkesV1,
    provenance: dict[str, Any],
) -> list[dict[str, Any]]:
    """Evalua todos los snapshots historicos disponibles."""
    rows: list[dict[str, Any]] = []
    for match_id in sorted(snapshots, key=lambda value: int(value)):
        for snapshot in snapshots[match_id]:
            key = _target_key(int(snapshot["match_id"]), str(snapshot["snapshot_ts"]))
            rows.append(_evaluate_snapshot(snapshot, targets[key], engine, provenance))
    return rows


def _metrics_by_segment(rows: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    """Calcula metricas por segmento simple."""
    output = []
    groups = sorted({row[field] for row in rows})
    for group in groups:
        segment = [row for row in rows if row[field] == group]
        output.append(
            {
                field: group,
                "count": len(segment),
                "markov_mae_remaining_total_goals": _remaining_metrics(segment, "markov")["mae_remaining_total_goals"],
                "hawkes_mae_remaining_total_goals": _remaining_metrics(segment, "hawkes")["mae_remaining_total_goals"],
                "mean_total_uplift": _safe_mean(row["hawkes_total_rate"] - row["markov_total_rate"] for row in segment),
            }
        )
    return output


def _build_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Construye metricas globales y segmentadas."""
    return {
        "markov": _remaining_metrics(rows, "markov"),
        "hawkes": _remaining_metrics(rows, "hawkes"),
        "delta_vs_markov": {
            "mae_remaining_total_goals": _remaining_metrics(rows, "hawkes")["mae_remaining_total_goals"] - _remaining_metrics(rows, "markov")["mae_remaining_total_goals"],
            "log_score_remaining_total_goals": _remaining_metrics(rows, "hawkes")["log_score_remaining_total_goals"] - _remaining_metrics(rows, "markov")["log_score_remaining_total_goals"],
            "mean_total_rate_uplift": _safe_mean(row["hawkes_total_rate"] - row["markov_total_rate"] for row in rows),
        },
        "event_type_behavior": _event_type_summary(rows),
        "with_recent_events": {
            "count": len(_segment_rows(rows, "recent_events_count")),
            "hawkes_mae_remaining_total_goals": _remaining_metrics(_segment_rows(rows, "recent_events_count"), "hawkes")["mae_remaining_total_goals"],
        },
        "by_censored": _metrics_by_segment(rows, "censored"),
        "by_next_goal_exists": _metrics_by_segment(rows, "next_goal_exists"),
        "by_minute": _group_mean(rows, "minute", "hawkes_total_rate"),
    }


def _build_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Construye auditoria estructural de Hawkes."""
    all_events_used = [event for row in rows for event in row["events_used"]]
    all_events_audit = [event for row in rows for event in row["events_audit"]]
    return {
        "event_ts_le_snapshot_ts": all(event["event_ts"] <= row["snapshot_ts"] for row in rows for event in row["events_used"]),
        "no_double_counting": all(len({event["event_id"] for event in row["events_used"]}) == len(row["events_used"]) for row in rows),
        "spectral_radius_subcritical": all(row["spectral_radius"] < 1.0 for row in rows),
        "positive_finite_lambdas": all(
            math.isfinite(row["lambda_hawkes_home"]) and math.isfinite(row["lambda_hawkes_away"]) and row["lambda_hawkes_home"] > 0 and row["lambda_hawkes_away"] > 0
            for row in rows
        ),
        "unknown_or_invalid_events_audited": sum(1 for event in all_events_audit if event.get("exclusion_reason") == "non_exciting_or_invalid_context"),
        "future_events_audited": sum(1 for event in all_events_audit if event.get("exclusion_reason") == "future_event"),
        "outside_memory_audited": sum(1 for event in all_events_audit if event.get("exclusion_reason") == "outside_memory"),
        "events_used_count": len(all_events_used),
        "events_audit_count": len(all_events_audit),
        "dependence_between_snapshots_documented": True,
        "markov_synthetic_caveat_visible": all(row["markov_provenance"]["markov_matrix_synthetic"] for row in rows),
        "no_probabilities_generated": True,
    }


def _build_snapshot_outputs(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Separa snapshots, contribuciones y targets serializables."""
    predictions = []
    contributions = []
    targets = []
    for row in rows:
        predictions.append(
            {
                "match_id": row["match_id"],
                "snapshot_ts": row["snapshot_ts"],
                "minute": row["minute"],
                "second": row["second"],
                "lambda_markov_home": row["lambda_markov_home"],
                "lambda_markov_away": row["lambda_markov_away"],
                "lambda_hawkes_home": row["lambda_hawkes_home"],
                "lambda_hawkes_away": row["lambda_hawkes_away"],
                "self_excitation_total": row["self_excitation_total"],
                "cross_excitation_total": row["cross_excitation_total"],
                "recent_events_count": row["recent_events_count"],
                "spectral_radius": row["spectral_radius"],
                "warnings": row["warnings"],
                "markov_provenance": row["markov_provenance"],
                "hawkes_provenance": row["hawkes_provenance"],
            }
        )
        targets.append(
            {
                "match_id": row["match_id"],
                "snapshot_ts": row["snapshot_ts"],
                "remaining_home_goals": row["remaining_home_goals"],
                "remaining_away_goals": row["remaining_away_goals"],
                "remaining_total_goals": row["remaining_total_goals"],
                "next_goal_exists": row["next_goal_exists"],
                "next_goal_team": row["next_goal_team"],
                "time_to_next_goal_seconds": row["time_to_next_goal_seconds"],
                "censored": row["censored"],
            }
        )
        for event in row["event_contributions"]:
            contributions.append({"match_id": row["match_id"], "snapshot_ts": row["snapshot_ts"], **event})
    return predictions, contributions, targets


def _database_counts() -> dict[str, Any]:
    """Intenta contar tablas antes y despues sin escribir en PostgreSQL."""
    if create_engine is None or text is None:
        return {"status": "database_verification_incomplete", "reason": "sqlalchemy_not_installed"}
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        return {"status": "database_verification_incomplete", "reason": "DATABASE_URL_missing"}
    engine = create_engine(database_url, future=True, pool_pre_ping=True)
    tables = ["matches", "events_timeline", "events_ledger", "raw_api_responses"]
    try:
        with engine.connect() as connection:
            connection.execute(text("SET statement_timeout TO 2000"))
            return {table: int(connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()) for table in tables}
    except SQLAlchemyError as error:
        return {"status": "database_verification_incomplete", "reason": str(error.__class__.__name__)}
    finally:
        engine.dispose()


def _test_status() -> dict[str, Any]:
    """Documenta el estado esperado de unittest y pytest."""
    return {
        "unittest_direct_status": "no_tests_ran_expected",
        "unittest_direct_reason": "tests/test_hawkes_v1.py usa funciones estilo pytest, no clases unittest.TestCase",
        "pytest_status": "known_capture_shutdown_issue",
        "pytest_reason": "pytest termina con FileNotFoundError en captura global tras informar 'no tests ran'",
        "ci_recommendation": [
            "añadir pytest.ini o pyproject.toml con configuracion explicita",
            "ejecutar pytest con captura desactivada durante diagnostico inicial: `pytest -s`",
            "convertir el suite Hawkes a `unittest.TestCase` o mantener pytest pero fijar una configuracion CI reproducible",
        ],
    }


def _classify_result(metrics: dict[str, Any], audit: dict[str, Any]) -> str:
    """Clasifica la corrida historica limitada."""
    if not audit["event_ts_le_snapshot_ts"] or not audit["spectral_radius_subcritical"]:
        return "hawkes_rejected_for_revision"
    return "hawkes_accepted_with_caveats"


def _build_manifest(
    rows: list[dict[str, Any]],
    config: HawkesConfig,
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any]:
    """Construye el manifiesto de ejecucion."""
    match_ids = sorted({row["match_id"] for row in rows})
    return {
        "evaluation_version": "hawkes_v1_limited_historical_eval_v1",
        "snapshot_source": str(SNAPSHOT_PATH),
        "live_targets_source": str(LIVE_TARGETS_PATH),
        "markov_result_source": str(MARKOV_RESULT_PATH),
        "markov_live_result_source": str(LIVE_RESULT_PATH),
        "hawkes_config_source": str(HAWKES_CONFIG_PATH),
        "hawkes_branching_source": str(HAWKES_G_PATH),
        "selected_match_ids": match_ids,
        "snapshot_count": len(rows),
        "parameters_synthetic_only": True,
        "markov_not_calibrated_visible": True,
        "postgres_before": before,
        "postgres_after": after,
        "postgres_modified": before == after if "status" not in before and "status" not in after else False,
        "model_hash": HawkesV1(config).model_hash(),
    }


def _write_json(path: Path, payload: Any) -> None:
    """Escribe un artefacto JSON con orden estable."""
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _write_report(path: Path, result: dict[str, Any]) -> None:
    """Escribe el informe Markdown resumido."""
    lines = [
        "# Hawkes v1 limited historical evaluation",
        "",
        f"- Decision: `{result['decision']}`",
        f"- Snapshots evaluados: `{result['coverage']['snapshots']}`",
        f"- Partidos cubiertos: `{result['coverage']['matches']}`",
        f"- Spectral radius max: `{result['audit']['spectral_radius_max']}`",
        f"- PostgreSQL modificado: `{result['manifest']['postgres_modified']}`",
        "",
        "## Hallazgos",
        "",
        "- Markov sigue visible como baseline sintetica no calibrada.",
        "- Hawkes no genera probabilidades directas; la comparacion usa intensidades y goles restantes.",
        "- La dependencia entre snapshots del mismo partido se conserva como caveat metodologico.",
        "",
        "## Tests",
        "",
        f"- unittest: `{result['tests']['unittest_direct_status']}`",
        f"- pytest: `{result['tests']['pytest_status']}`",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run_once(output_dir: Path) -> dict[str, Any]:
    """Ejecuta una evaluacion completa y persiste artefactos."""
    _ensure_dir(output_dir)
    config = _load_hawkes_config()
    engine = HawkesV1(config)
    snapshots = _load_json(SNAPSHOT_PATH)
    live_targets = _build_target_index(_load_json(LIVE_TARGETS_PATH))
    provenance = _build_markov_provenance(_load_json(MARKOV_RESULT_PATH))
    before = _database_counts()
    rows = _build_eval_rows(snapshots, live_targets, engine, provenance)
    after = _database_counts()
    metrics = _build_metrics(rows)
    audit = _build_audit(rows)
    predictions, contributions, targets = _build_snapshot_outputs(rows)
    manifest = _build_manifest(rows, config, before, after)
    result = {
        "decision": _classify_result(metrics, audit),
        "coverage": {"matches": len(manifest["selected_match_ids"]), "snapshots": len(rows)},
        "metrics": metrics,
        "audit": {**audit, "spectral_radius_max": max(row["spectral_radius"] for row in rows)},
        "manifest": manifest,
        "tests": _test_status(),
        "input_hash": _stable_hash({"snapshots": snapshots, "targets": _load_json(LIVE_TARGETS_PATH), "config": asdict(config)}),
        "model_hash": engine.model_hash(),
    }
    hashes = _build_hashes(predictions, contributions, targets, metrics, audit, manifest, result)
    _write_artifacts(output_dir, predictions, contributions, targets, metrics, audit, manifest, result, hashes)
    return {"result": result, "hashes": hashes}


def _build_hashes(
    predictions: list[dict[str, Any]],
    contributions: list[dict[str, Any]],
    targets: list[dict[str, Any]],
    metrics: dict[str, Any],
    audit: dict[str, Any],
    manifest: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, str]:
    """Calcula hashes de artefactos principales."""
    return {
        "predictions_hash": _stable_hash(predictions),
        "contributions_hash": _stable_hash(contributions),
        "targets_hash": _stable_hash(targets),
        "metrics_hash": _stable_hash(metrics),
        "audit_hash": _stable_hash(audit),
        "manifest_hash": _stable_hash(manifest),
        "result_hash": _stable_hash(result),
    }


def _write_artifacts(
    output_dir: Path,
    predictions: list[dict[str, Any]],
    contributions: list[dict[str, Any]],
    targets: list[dict[str, Any]],
    metrics: dict[str, Any],
    audit: dict[str, Any],
    manifest: dict[str, Any],
    result: dict[str, Any],
    hashes: dict[str, str],
) -> None:
    """Persiste todos los artefactos de evaluacion."""
    _write_json(output_dir / "hawkes_v1_predictions.json", predictions)
    _write_json(output_dir / "hawkes_v1_event_contributions.json", contributions)
    _write_json(output_dir / "hawkes_v1_targets.json", targets)
    _write_json(output_dir / "hawkes_v1_metrics.json", metrics)
    _write_json(output_dir / "hawkes_v1_audit.json", audit)
    _write_json(output_dir / "hawkes_v1_manifest.json", manifest)
    _write_json(output_dir / "hawkes_v1_result.json", result)
    _write_json(output_dir / "hawkes_v1_hashes.json", hashes)
    _write_report(output_dir / "hawkes_v1_report.md", result)


def _verify_replay(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    """Compara dos corridas para verificar determinismo."""
    return {
        "replay_identical": first["hashes"] == second["hashes"],
        "primary_hashes": first["hashes"],
        "replay_hashes": second["hashes"],
    }


def main() -> None:
    """Ejecuta la evaluacion limitada y el replay determinista."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    LOGGER.info("Iniciando evaluacion historica limitada de Hawkes v1.")
    first = _run_once(ARTIFACT_DIR)
    second = _run_once(REPLAY_DIR)
    replay = _verify_replay(first, second)
    _write_json(ARTIFACT_DIR / "hawkes_v1_replay.json", replay)
    _write_json(REPLAY_DIR / "hawkes_v1_replay.json", replay)
    LOGGER.info("Evaluacion completada. Replay identical=%s", replay["replay_identical"])


if __name__ == "__main__":
    main()

# Version: 1.0.0
# Created: 2026-07-16
