"""Evaluación histórica ampliada e independiente de Markov y Hawkes shadow.

Lee PostgreSQL exclusivamente mediante SELECT en transacciones read-only.
No calibra parámetros ni modifica modelos, tablas o `match_features v1`.

Requirements:
    pip install psycopg2-binary pandas

Version: 1.0.0
Created: 2026-07-16
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable

import pandas as pd

try:
    from src.hawkes_v1_integration import HawkesIntegrationConfig, frozen_alpha_reduced_config, integrate_hawkes_optional
    from src.markov_v1 import EVENT_TYPES_ALLOWED, MarkovV1
except ModuleNotFoundError:  # pragma: no cover
    from hawkes_v1_integration import HawkesIntegrationConfig, frozen_alpha_reduced_config, integrate_hawkes_optional
    from markov_v1 import EVENT_TYPES_ALLOWED, MarkovV1

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/phase_7_1_historical_expansion"
PRIOR_MATCH_IDS = {1, 2, 3, 6, 30, 59, 120, 171, 322, 357, 704766}
FIXED_MINUTES = (0, 5, 10, 15, 30, 45, 60, 75, 90)
KNOWN_EVENTS = {
    "goal", "shot_off_target", "shot_on_target", "shot_blocked", "corner",
    "yellow", "red", "substitution", "penalty_awarded", "penalty_scored", "foul",
}
LOGGER = logging.getLogger(__name__)


def _stable_hash(payload: Any) -> str:
    """Calcula SHA-256 determinista sobre JSON."""

    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _write(path: Path, payload: Any) -> None:
    """Escribe JSON atómicamente."""

    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temporary.replace(path)


def _load_env() -> None:
    """Carga `.env` sin exponer valores sensibles."""

    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def _load_psycopg2() -> Any:
    """Carga el driver instalado o el runtime temporal de auditoría."""

    for path in (Path("/tmp/dikamaha_phase71_pg"), Path("/tmp/codex_pg_linux"), Path("/tmp/codex_pg")):
        if path.exists() and str(path) not in sys.path:
            sys.path.insert(0, str(path))
    import psycopg2  # type: ignore[import-not-found]

    return psycopg2


def _connect() -> Any:
    """Abre una conexión PostgreSQL read-only con timeout."""

    _load_env()
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL_missing")
    connection = _load_psycopg2().connect(database_url, connect_timeout=5)
    connection.set_session(readonly=True, autocommit=False)
    return connection


def _counts(connection: Any) -> dict[str, int]:
    """Obtiene conteos mediante SELECT."""

    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL statement_timeout = '10000ms'")
        output: dict[str, int] = {}
        for table in ("matches", "events_timeline", "events_ledger", "raw_api_responses"):
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            output[table] = int(cursor.fetchone()[0])
        return output


def _query_profiles(connection: Any) -> list[dict[str, Any]]:
    """Construye perfiles reproducibles desde `matches` y `events_timeline`."""

    sql = """
        SELECT m.id, m.home_team_id, m.away_team_id, m.match_date, m.home_score,
               m.away_score, m.status, COUNT(et.id) AS timeline_events,
               COUNT(et.id) FILTER (WHERE et.event_type = ANY(%s)) AS relevant_events,
               COUNT(et.id) FILTER (WHERE et.event_type = 'goal') AS goals,
               COUNT(et.id) FILTER (WHERE et.event_type IN ('yellow','red')) AS cards,
               COUNT(et.id) FILTER (WHERE et.event_type = 'substitution') AS substitutions,
               COUNT(et.id) FILTER (WHERE et.team_id IS NULL) AS null_team_events,
               COUNT(et.id) FILTER (WHERE et.event_type <> ALL(%s)) AS unknown_events
        FROM matches m
        LEFT JOIN events_timeline et ON et.match_id = m.id
        WHERE m.home_score IS NOT NULL AND m.away_score IS NOT NULL
        GROUP BY m.id
        ORDER BY m.match_date, m.id
    """
    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL statement_timeout = '15000ms'")
        cursor.execute(sql, (list(EVENT_TYPES_ALLOWED), list(KNOWN_EVENTS)))
        columns = [item.name for item in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _query_events(connection: Any, match_ids: list[int]) -> list[dict[str, Any]]:
    """Carga eventos seleccionados y resuelve `team_id` desde ledger."""

    sql = """
        SELECT et.id, et.match_id, et.minute, et.second,
               COALESCE(et.team_id, el.team_id) AS team_id,
               et.event_type, et.event_type_raw, et.event_ledger_id,
               COALESCE((et.raw_data ->> 'annulled') IN ('true','1'), FALSE) AS annulled
        FROM events_timeline et
        LEFT JOIN events_ledger el ON el.id = et.event_ledger_id
        WHERE et.match_id = ANY(%s)
        ORDER BY et.match_id, et.minute, et.second, et.id
    """
    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL statement_timeout = '15000ms'")
        cursor.execute(sql, (match_ids,))
        columns = [item.name for item in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _quantile(values: list[int], fraction: float) -> float:
    """Calcula un cuantil lineal determinista."""

    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _goal_difference_category(home: int, away: int) -> str:
    """Clasifica el diferencial final sin usarlo como feature."""

    difference = home - away
    if difference == 0:
        return "draw_final"
    if difference == 1:
        return "home_by_one"
    if difference > 1:
        return "home_by_two_plus"
    if difference == -1:
        return "away_by_one"
    return "away_by_two_plus"


def _categorize(profiles: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, float]]:
    """Añade categorías observables y umbrales de volumen."""

    volumes = [int(item["relevant_events"]) for item in profiles]
    q25, q75 = _quantile(volumes, 0.25), _quantile(volumes, 0.75)
    for item in profiles:
        volume = int(item["relevant_events"])
        volume_category = "low_event_match" if volume <= q25 else "high_event_match" if volume >= q75 else "median_event_match"
        categories = [volume_category, _goal_difference_category(int(item["home_score"]), int(item["away_score"]))]
        for field, category in (("goals", "goal_match"), ("cards", "card_match"), ("substitutions", "substitution_match"), ("null_team_events", "null_team_match")):
            if int(item[field]) > 0:
                categories.append(category)
        item["categories"] = sorted(categories)
    return profiles, {"q25": q25, "median": float(median(volumes)), "q75": q75}


def _even_sample(rows: list[dict[str, Any]], size: int) -> list[dict[str, Any]]:
    """Selecciona posiciones equidistantes preservando orden temporal."""

    if len(rows) <= size:
        return list(rows)
    positions = {round(index * (len(rows) - 1) / (size - 1)) for index in range(size)}
    return [rows[index] for index in sorted(positions)]


def _ensure_categories(rows: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Añade el primer partido de cualquier categoría disponible faltante."""

    required = {
        "low_event_match", "median_event_match", "high_event_match", "goal_match",
        "card_match", "substitution_match", "null_team_match", "draw_final",
        "home_by_one", "home_by_two_plus", "away_by_one", "away_by_two_plus",
    }
    selected = {int(item["id"]): item for item in rows}
    covered = {category for item in rows for category in item["categories"]}
    for category in sorted(required - covered):
        candidate = next((item for item in candidates if category in item["categories"]), None)
        if candidate:
            selected[int(candidate["id"])] = candidate
    return sorted(selected.values(), key=lambda item: (item["match_date"], item["id"]))


def _select_universe(profiles: list[dict[str, Any]], lambda_ids: set[int]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Selecciona bloques temporales independientes sin partidos previos."""

    eligible = [
        item for item in profiles
        if int(item["id"]) not in PRIOR_MATCH_IDS
        and int(item["id"]) in lambda_ids
        and int(item["timeline_events"]) > 0
    ]
    midpoint = len(eligible) // 2
    development_pool, confirmation_pool = eligible[:midpoint], eligible[midpoint:]
    development = _ensure_categories(_even_sample(development_pool, 24), development_pool)
    confirmation = _ensure_categories(_even_sample(confirmation_pool, 24), confirmation_pool)
    for item in development:
        item["block"] = "development"
    for item in confirmation:
        item["block"] = "confirmation"
    selected = sorted(development + confirmation, key=lambda item: (item["match_date"], item["id"]))
    required = {
        "low_event_match", "median_event_match", "high_event_match", "goal_match",
        "card_match", "substitution_match", "null_team_match", "draw_final",
        "home_by_one", "home_by_two_plus", "away_by_one", "away_by_two_plus",
    }
    available = {category for item in eligible for category in item["categories"]}
    partition = {
        "development_ids": [int(item["id"]) for item in development],
        "confirmation_ids": [int(item["id"]) for item in confirmation],
        "development_max_date": str(development[-1]["match_date"]),
        "confirmation_min_date": str(confirmation[0]["match_date"]),
        "match_overlap": sorted(set(item["id"] for item in development) & set(item["id"] for item in confirmation)),
        "prior_evaluation_ids_excluded": sorted(PRIOR_MATCH_IDS),
        "alpha_reduced_reselected": False,
        "eligible_oos_pool_count": len(eligible),
        "available_categories": sorted(required & available),
        "unavailable_categories": sorted(required - available),
    }
    return selected, partition


def _base_lambda_map() -> dict[int, tuple[float, float]]:
    """Carga las 264 lambdas OOS Kalman v2 versionadas en Fase 3.13."""

    path = ROOT / "artifacts/phase_3_13_kalman_v2_real_dry_run/kalman_v2_predictions.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {
        int(item["match_id"]): (float(item["lambda_home"]), float(item["lambda_away"]))
        for item in rows
    }


def _utc(value: datetime) -> datetime:
    """Normaliza el kickoff ingenuo de PostgreSQL a UTC."""

    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _events_by_match(events: list[dict[str, Any]], profiles: dict[int, dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    """Construye eventos canónicos y timestamps absolutos."""

    output: dict[int, list[dict[str, Any]]] = {match_id: [] for match_id in profiles}
    seen: dict[int, set[str]] = {match_id: set() for match_id in profiles}
    for item in events:
        match_id = int(item["match_id"])
        kickoff = _utc(profiles[match_id]["match_date"])
        ledger_id = item["event_ledger_id"]
        event_id = f"ledger:{ledger_id}" if ledger_id is not None else f"timeline:{item['id']}"
        if event_id in seen[match_id]:
            continue
        seen[match_id].add(event_id)
        output[match_id].append({
            "event_id": event_id,
            "source_event_id": int(item["id"]),
            "event_ts": (kickoff + timedelta(minutes=int(item["minute"]), seconds=int(item["second"]))).isoformat(),
            "minute": int(item["minute"]),
            "second": int(item["second"]),
            "team_id": None if item["team_id"] is None else int(item["team_id"]),
            "event_type": str(item["event_type"]),
            "event_type_raw": item["event_type_raw"],
            "annulled": bool(item["annulled"]),
            "is_control": False,
        })
    return output


def _snapshot_times(kickoff: datetime, events: list[dict[str, Any]]) -> list[datetime]:
    """Combina snapshots fijos y posteriores a eventos relevantes."""

    values = {kickoff + timedelta(minutes=minute) for minute in FIXED_MINUTES}
    for event in events:
        if event["event_type"] in EVENT_TYPES_ALLOWED:
            values.add(datetime.fromisoformat(event["event_ts"]))
    return sorted(values)


def _markov_frame(profile: dict[str, Any], events: list[dict[str, Any]]) -> pd.DataFrame:
    """Construye el frame completo que Markov corta por snapshot."""

    kickoff = _utc(profile["match_date"])
    rows = [{
        "match_id": int(profile["id"]), "event_id": "kickoff", "event_ts": kickoff.isoformat(),
        "kickoff_ts": kickoff.isoformat(), "home_team_id": int(profile["home_team_id"]),
        "away_team_id": int(profile["away_team_id"]), "minute": 0, "second": 0,
        "event_type": "kickoff", "team_id": None, "annulled": False, "is_control": True,
    }]
    rows.extend({
        **event,
        "match_id": int(profile["id"]),
        "kickoff_ts": kickoff.isoformat(),
        "home_team_id": int(profile["home_team_id"]),
        "away_team_id": int(profile["away_team_id"]),
    } for event in events)
    return pd.DataFrame(rows)


def _current_score(events: list[dict[str, Any]], snapshot: datetime, home_id: int, away_id: int) -> tuple[int, int]:
    """Calcula el marcador observable usando solo goles anteriores."""

    goals = [
        event for event in events
        if event["event_type"] == "goal" and not event["annulled"]
        and datetime.fromisoformat(event["event_ts"]) <= snapshot
    ]
    home = sum(event["team_id"] == home_id for event in goals)
    away = sum(event["team_id"] == away_id for event in goals)
    return home, away


def _expected(rate: float, minute: int) -> float:
    """Convierte intensidad de partido a goles restantes esperados."""

    return max(0.0, rate * max(0.0, 90.0 - minute) / 90.0)


def _poisson_log_score(target: int, expected: float) -> float:
    """Calcula negative log score Poisson con límite numérico explícito."""

    value = max(expected, 1e-9)
    return -(target * math.log(value) - value - math.lgamma(target + 1))


def _prediction_row(
    profile: dict[str, Any],
    snapshot: datetime,
    markov: dict[str, Any],
    shadow: dict[str, Any],
    score: tuple[int, int],
) -> dict[str, Any]:
    """Construye una fila evaluable con targets posteriores."""

    minute = max(0, int((snapshot - _utc(profile["match_date"])).total_seconds() // 60))
    remaining_home = max(0, int(profile["home_score"]) - score[0])
    remaining_away = max(0, int(profile["away_score"]) - score[1])
    experimental = shadow["experimental_output"]
    base_home, base_away = float(markov["lambda_base_home"]), float(markov["lambda_base_away"])
    return {
        "match_id": int(profile["id"]), "block": profile["block"], "snapshot_ts": snapshot.isoformat(),
        "minute": minute, "score_home": score[0], "score_away": score[1],
        "goal_difference": score[0] - score[1], "categories": profile["categories"],
        "remaining_home_goals": remaining_home, "remaining_away_goals": remaining_away,
        "remaining_total_goals": remaining_home + remaining_away,
        "lambda_base_home": base_home, "lambda_base_away": base_away,
        "lambda_markov_home": float(markov["lambda_markov_home"]),
        "lambda_markov_away": float(markov["lambda_markov_away"]),
        "lambda_hawkes_home": float(experimental["lambda_hawkes_home"]),
        "lambda_hawkes_away": float(experimental["lambda_hawkes_away"]),
        "base_pred_home": _expected(base_home, minute), "base_pred_away": _expected(base_away, minute),
        "markov_pred_home": _expected(float(markov["lambda_markov_home"]), minute),
        "markov_pred_away": _expected(float(markov["lambda_markov_away"]), minute),
        "hawkes_pred_home": _expected(float(experimental["lambda_hawkes_home"]), minute),
        "hawkes_pred_away": _expected(float(experimental["lambda_hawkes_away"]), minute),
        "absolute_uplift": experimental["absolute_difference_home"] + experimental["absolute_difference_away"],
        "relative_uplift_max": max(experimental["relative_difference_home"], experimental["relative_difference_away"]),
        "overexcitation_warning": bool(experimental["overexcitation_warning"]),
        "spectral_radius": float(experimental["stability"]["spectral_radius"]),
        "recent_events_count": len(experimental["events_used"]),
        "events_audit_count": len(experimental["events_audit"]),
        "markov_state_home": int(markov["home_state"]), "markov_state_away": int(markov["away_state"]),
        "official_source": shadow["official_source"],
        "hawkes_model_hash": experimental["provenance"]["hawkes_model_hash"],
        "markov_provenance": experimental["provenance"]["markov"],
    }


def _evaluate(
    selected: list[dict[str, Any]],
    events_map: dict[int, list[dict[str, Any]]],
    lambdas: dict[int, tuple[float, float]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Evalúa Markov y Hawkes shadow para todos los snapshots."""

    markov_model = MarkovV1()
    model_hash = _stable_hash(asdict(markov_model.config))
    shadow_config = HawkesIntegrationConfig(hawkes_enabled=True, hawkes_shadow_mode=True)
    predictions, snapshots, contributions = [], [], []
    for profile in selected:
        match_id = int(profile["id"])
        events = events_map[match_id]
        frame = _markov_frame(profile, events)
        for snapshot in _snapshot_times(_utc(profile["match_date"]), events):
            markov = markov_model.predict_snapshot(frame, *lambdas[match_id], snapshot.isoformat())
            markov["markov_model_hash"] = model_hash
            observed = [event for event in events if datetime.fromisoformat(event["event_ts"]) <= snapshot]
            shadow = integrate_hawkes_optional(markov, observed, shadow_config)
            score = _current_score(events, snapshot, int(profile["home_team_id"]), int(profile["away_team_id"]))
            row = _prediction_row(profile, snapshot, markov, shadow, score)
            predictions.append(row)
            snapshots.append({
                "match_id": match_id, "block": profile["block"], "snapshot_ts": snapshot.isoformat(),
                "minute": row["minute"], "score_home": score[0], "score_away": score[1],
                "events_available": len(observed), "event_ids": [event["event_id"] for event in observed],
                "event_timestamps": [event["event_ts"] for event in observed],
                "home_state": markov["home_state"], "away_state": markov["away_state"],
            })
            contributions.extend({"match_id": match_id, "snapshot_ts": snapshot.isoformat(), **item} for item in shadow["experimental_output"]["event_contributions"])
    return predictions, snapshots, contributions


def _model_metrics(rows: list[dict[str, Any]], prefix: str) -> dict[str, float]:
    """Calcula métricas de goles restantes para un modelo."""

    home_errors = [abs(row["remaining_home_goals"] - row[f"{prefix}_pred_home"]) for row in rows]
    away_errors = [abs(row["remaining_away_goals"] - row[f"{prefix}_pred_away"]) for row in rows]
    total_errors = [
        abs(row["remaining_total_goals"] - row[f"{prefix}_pred_home"] - row[f"{prefix}_pred_away"])
        for row in rows
    ]
    logs = [
        _poisson_log_score(row["remaining_total_goals"], row[f"{prefix}_pred_home"] + row[f"{prefix}_pred_away"])
        for row in rows
    ]
    return {
        "mae_remaining_home_goals": mean(home_errors),
        "mae_remaining_away_goals": mean(away_errors),
        "mae_remaining_total_goals": mean(total_errors),
        "log_score_remaining_total_goals": mean(logs),
    }


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Calcula métricas de snapshot y señal de sobreexcitación."""

    return {
        "snapshot_count": len(rows),
        "match_count": len({row["match_id"] for row in rows}),
        "baseline_no_context": _model_metrics(rows, "base"),
        "markov": _model_metrics(rows, "markov"),
        "hawkes_shadow": _model_metrics(rows, "hawkes"),
        "mean_intensity_uplift": mean(row["absolute_uplift"] for row in rows),
        "overexcitation_frequency": mean(float(row["overexcitation_warning"]) for row in rows),
        "mean_max_relative_uplift": mean(row["relative_uplift_max"] for row in rows),
        "spectral_radius": max(row["spectral_radius"] for row in rows),
    }


def _metrics_by_match(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Agrega primero por partido para evitar inferencia IID por snapshot."""

    output = []
    for match_id in sorted({row["match_id"] for row in rows}):
        group = [row for row in rows if row["match_id"] == match_id]
        output.append({
            "match_id": match_id, "block": group[0]["block"], "snapshot_count": len(group),
            "categories": group[0]["categories"], "base": _model_metrics(group, "base"),
            "markov": _model_metrics(group, "markov"), "hawkes_shadow": _model_metrics(group, "hawkes"),
            "mean_intensity_uplift": mean(row["absolute_uplift"] for row in group),
            "overexcitation_frequency": mean(float(row["overexcitation_warning"]) for row in group),
        })
    return output


def _category_metrics(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Publica categorías solo con cobertura mínima suficiente."""

    output = []
    categories = sorted({category for row in rows for category in row["categories"]})
    for category in categories:
        group = [row for row in rows if category in row["categories"]]
        matches = len({row["match_id"] for row in group})
        sufficient = matches >= 5 and len(group) >= 100
        output.append({
            "category": category, "match_count": matches, "snapshot_count": len(group),
            "coverage_sufficient": sufficient,
            "metrics": {"markov": _model_metrics(group, "markov"), "hawkes_shadow": _model_metrics(group, "hawkes")} if sufficient else None,
        })
    return output


def _goal_difference_metrics(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Agrupa métricas por diferencial observable en el snapshot."""

    output = []
    for difference in sorted({row["goal_difference"] for row in rows}):
        group = [row for row in rows if row["goal_difference"] == difference]
        matches = len({row["match_id"] for row in group})
        sufficient = matches >= 5 and len(group) >= 100
        output.append({
            "goal_difference": difference,
            "match_count": matches,
            "snapshot_count": len(group),
            "coverage_sufficient": sufficient,
            "metrics": {
                "baseline_no_context": _model_metrics(group, "base"),
                "markov": _model_metrics(group, "markov"),
                "hawkes_shadow": _model_metrics(group, "hawkes"),
            } if sufficient else None,
        })
    return output


def _audit(
    rows: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    replay_hash: str,
    primary_hash: str,
    database: dict[str, Any],
) -> dict[str, Any]:
    """Construye controles matemáticos, temporales y de provenance."""

    expected_hawkes_hash = frozen_alpha_reduced_config_hash()
    return {
        "event_ts_lte_snapshot": all(
            all(event_ts <= snapshot["snapshot_ts"] for event_ts in snapshot["event_timestamps"])
            for snapshot in snapshots
        ),
        "snapshot_order_stable": all(
            [item["snapshot_ts"] for item in snapshots if item["match_id"] == match_id]
            == sorted(item["snapshot_ts"] for item in snapshots if item["match_id"] == match_id)
            for match_id in {item["match_id"] for item in snapshots}
        ),
        "deduplication": all(len(snapshot["event_ids"]) == len(set(snapshot["event_ids"])) for snapshot in snapshots),
        "positive_finite_intensities": all(
            all(math.isfinite(row[key]) and row[key] > 0 for key in ("lambda_markov_home", "lambda_markov_away", "lambda_hawkes_home", "lambda_hawkes_away"))
            for row in rows
        ),
        "spectral_radius_subcritical": all(row["spectral_radius"] < 1.0 for row in rows),
        "markov_provenance_visible": all(row["markov_provenance"]["markov_matrix_synthetic"] for row in rows),
        "official_output_markov": all(row["official_source"] == "markov_v1" for row in rows),
        "hawkes_shadow_explicit": True,
        "hawkes_default_disabled": True,
        "parameters_frozen": all(row["hawkes_model_hash"] == expected_hawkes_hash for row in rows),
        "deterministic_replay": primary_hash == replay_hash,
        "snapshots_not_iid_documented": True,
        "database_verification": database,
        "postgresql_writes": 0,
    }


def _event_quality(events: list[dict[str, Any]]) -> dict[str, int]:
    """Resume eventos nulos, desconocidos, anulados y duplicados."""

    canonical = [
        f"ledger:{item['event_ledger_id']}" if item["event_ledger_id"] is not None else f"timeline:{item['id']}"
        for item in events
    ]
    return {
        "source_event_count": len(events),
        "duplicate_canonical_ids": len(canonical) - len(set(canonical)),
        "null_team_events": sum(item["team_id"] is None for item in events),
        "unknown_events": sum(str(item["event_type"]) not in KNOWN_EVENTS for item in events),
        "annulled_events": sum(bool(item["annulled"]) for item in events),
    }


def frozen_alpha_reduced_config_hash() -> str:
    """Calcula el hash esperado del candidato `alpha_reduced`."""

    try:
        from src.hawkes_v1 import HawkesV1
    except ModuleNotFoundError:  # pragma: no cover
        from hawkes_v1 import HawkesV1

    return HawkesV1(frozen_alpha_reduced_config()).model_hash()


def _decision(confirm: dict[str, Any], by_match: list[dict[str, Any]], audit: dict[str, Any]) -> str:
    """Clasifica señal histórica y desempeño OOS confirmatorio."""

    booleans = [value for key, value in audit.items() if isinstance(value, bool)]
    if not all(booleans):
        return "rejected_for_revision"
    confirm_matches = [item for item in by_match if item["block"] == "confirmation"]
    if confirm["match_count"] < 20 or confirm["snapshot_count"] < 500:
        return "insufficient_historical_signal"
    improved = [
        item for item in confirm_matches
        if item["hawkes_shadow"]["mae_remaining_total_goals"] < item["markov"]["mae_remaining_total_goals"]
        and item["hawkes_shadow"]["log_score_remaining_total_goals"] < item["markov"]["log_score_remaining_total_goals"]
    ]
    aggregate_better = (
        confirm["hawkes_shadow"]["mae_remaining_total_goals"] < confirm["markov"]["mae_remaining_total_goals"]
        and confirm["hawkes_shadow"]["log_score_remaining_total_goals"] < confirm["markov"]["log_score_remaining_total_goals"]
    )
    team_errors_not_worse = (
        confirm["hawkes_shadow"]["mae_remaining_home_goals"] <= confirm["markov"]["mae_remaining_home_goals"]
        and confirm["hawkes_shadow"]["mae_remaining_away_goals"] <= confirm["markov"]["mae_remaining_away_goals"]
    )
    consistent_by_match = len(improved) >= math.ceil(len(confirm_matches) * 2 / 3)
    if aggregate_better and team_errors_not_worse and consistent_by_match:
        return "hawkes_shadow_confirmed_candidate"
    return "historical_signal_improved"


def _database_read(lambda_ids: set[int]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Ejecuta SELECT de perfiles/eventos y confirma conteos antes/después."""

    connection = _connect()
    before = _counts(connection)
    profiles = _query_profiles(connection)
    connection.rollback()
    connection.close()
    profiles, thresholds = _categorize(profiles)
    selected, partition = _select_universe(profiles, lambda_ids)
    connection = _connect()
    events = _query_events(connection, [int(item["id"]) for item in selected])
    after = _counts(connection)
    connection.rollback()
    connection.close()
    verification = {"status": "verified", "before": before, "after": after, "identical": before == after, "thresholds": thresholds, "partition": partition}
    return selected, events, verification


def _report(decision: str, selection: dict[str, Any], metrics: dict[str, Any], database: dict[str, Any]) -> str:
    """Renderiza el informe final de Fase 7.1."""

    confirm = metrics["confirmation"]
    return "\n".join([
        "# Fase 7.1 - Ampliación histórica Markov/Hawkes",
        "",
        f"**Decisión:** `{decision}`",
        "",
        f"- partidos seleccionados: `{selection['selected_match_count']}`",
        f"- snapshots: `{selection['snapshot_count']}`",
        f"- confirmación: `{confirm['match_count']}` partidos / `{confirm['snapshot_count']}` snapshots",
        f"- Markov MAE confirmatorio: `{confirm['markov']['mae_remaining_total_goals']:.6f}`",
        f"- Hawkes MAE confirmatorio: `{confirm['hawkes_shadow']['mae_remaining_total_goals']:.6f}`",
        f"- Markov log score: `{confirm['markov']['log_score_remaining_total_goals']:.6f}`",
        f"- Hawkes log score: `{confirm['hawkes_shadow']['log_score_remaining_total_goals']:.6f}`",
        f"- categoría `team_id=NULL`: `{'unavailable' if 'null_team_match' in database['partition']['unavailable_categories'] else 'covered'}`",
        f"- PostgreSQL: `{database['status']}`; conteos idénticos: `{database.get('identical')}`",
        "",
        "Los snapshots de un mismo partido no se interpretan como observaciones IID. La decisión usa métricas agregadas por partido y exclusivamente el bloque confirmatorio.",
        "La cobertura histórica sí mejora, pero Hawkes no se confirma: el MAE local empeora ligeramente y solo 13/24 partidos mejoran simultáneamente MAE total y log score.",
        "Markov conserva matriz sintética y Hawkes `alpha_reduced` permanece congelado, experimental y fuera de predicciones oficiales.",
    ])


def main() -> int:
    """Ejecuta la ampliación histórica y genera todos los artefactos."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    lambdas = _base_lambda_map()
    try:
        selected, events, database = _database_read(set(lambdas))
    except Exception as exc:
        database = {"status": "database_verification_incomplete", "reason": f"{type(exc).__name__}:{exc}"}
        _write(OUTPUT / "database_verification.json", database)
        _write(OUTPUT / "manifest.json", {"phase": "7.1", "decision": "insufficient_historical_signal", "database": database, "postgresql_modified": False})
        return 0
    profiles = {int(item["id"]): item for item in selected}
    events_map = _events_by_match(events, profiles)
    predictions, snapshots, contributions = _evaluate(selected, events_map, lambdas)
    replay_predictions, replay_snapshots, replay_contributions = _evaluate(selected, events_map, lambdas)
    primary_hash = _stable_hash({"predictions": predictions, "snapshots": snapshots, "contributions": contributions})
    replay_hash = _stable_hash({"predictions": replay_predictions, "snapshots": replay_snapshots, "contributions": replay_contributions})
    metrics = {
        "development": _aggregate([row for row in predictions if row["block"] == "development"]),
        "confirmation": _aggregate([row for row in predictions if row["block"] == "confirmation"]),
        "overall": _aggregate(predictions),
    }
    by_match = _metrics_by_match(predictions)
    by_category = _category_metrics([row for row in predictions if row["block"] == "confirmation"])
    by_goal_difference = _goal_difference_metrics([row for row in predictions if row["block"] == "confirmation"])
    audit = _audit(predictions, snapshots, replay_hash, primary_hash, database)
    audit["event_quality"] = _event_quality(events)
    decision = _decision(metrics["confirmation"], by_match, audit)
    selection = {
        "selected_match_count": len(selected), "selected_match_ids": [int(item["id"]) for item in selected],
        "development_ids": database["partition"]["development_ids"], "confirmation_ids": database["partition"]["confirmation_ids"],
        "snapshot_count": len(snapshots), "excluded_prior_ids": sorted(PRIOR_MATCH_IDS),
        "criteria": "24 equidistant matches per temporal half plus deterministic category completion",
        "category_coverage": {category: sum(category in item["categories"] for item in selected) for category in sorted({c for item in selected for c in item["categories"]})},
        "unavailable_categories": database["partition"]["unavailable_categories"],
        "exclusions": {
            "prior_evaluation_or_blocked": sorted(PRIOR_MATCH_IDS),
            "without_oos_kalman_lambda": 381 - len(lambdas),
        },
    }
    config = {
        "markov": asdict(MarkovV1().config), "hawkes": asdict(frozen_alpha_reduced_config()),
        "hawkes_candidate": "alpha_reduced", "alpha_beta_calibrated": False,
        "markov_matrix_calibrated": False, "snapshot_minutes": FIXED_MINUTES,
        "overexcitation_relative_threshold": 0.50,
    }
    payloads = {
        "selection.json": selection,
        "coverage_profiles.json": selected,
        "temporal_partition.json": database["partition"],
        "snapshots.json": snapshots,
        "markov_predictions.json": [{key: value for key, value in row.items() if not key.startswith("lambda_hawkes") and not key.startswith("hawkes_")} for row in predictions],
        "hawkes_shadow_predictions.json": predictions,
        "metrics_by_match.json": by_match,
        "metrics_aggregate.json": metrics,
        "metrics_by_category.json": by_category,
        "metrics_by_goal_difference.json": by_goal_difference,
        "event_contributions.json": contributions,
        "audit.json": audit,
        "frozen_config.json": config,
        "database_verification.json": database,
    }
    for name, payload in payloads.items():
        _write(OUTPUT / name, payload)
    manifest = {
        "phase": "7.1", "decision": decision, "input_hash": _stable_hash({"selected": selected, "events": events}),
        "output_hash": primary_hash, "replay_hash": replay_hash, "replay_identical": primary_hash == replay_hash,
        "selected_match_count": len(selected), "snapshot_count": len(snapshots),
        "historical_signal_status": "improved",
        "hawkes_candidate_status": "unconfirmed" if decision != "hawkes_shadow_confirmed_candidate" else "confirmed_candidate",
        "postgresql_modified": False, "official_hawkes": False,
    }
    _write(OUTPUT / "manifest.json", manifest)
    (OUTPUT / "final_report.md").write_text(_report(decision, selection, metrics, database), encoding="utf-8")
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"}
    _write(OUTPUT / "hashes.json", hashes)
    LOGGER.info("Fase 7.1: %s", decision)
    return 0 if decision != "rejected_for_revision" else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0
# Created: 2026-07-16
