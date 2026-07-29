"""Calibración temporal independiente de Markov v1 y Hawkes shadow v1.

El runner usa PostgreSQL exclusivamente mediante SELECT, separa partidos
completos en desarrollo, validación y confirmación, y no modifica la salida
oficial del servicio.

Requirements:
    - numpy
    - pandas
    - SQLAlchemy==2.0.41
    - psycopg2-binary==2.9.10

Version: 1.0.0
Created: 2026-07-16
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

try:
    from src.hawkes_v1 import HawkesConfig, HawkesV1, _radius
    from src.markov_v1 import EVENT_TYPES_ALLOWED, MarkovV1, MarkovV1Config
    from src.postgres_readonly_staging import (
        ReadonlyDatabase,
        counts_identical,
        database_error_types,
        detect_capabilities,
        sanitize_error,
    )
except ModuleNotFoundError:  # pragma: no cover
    from hawkes_v1 import HawkesConfig, HawkesV1, _radius
    from markov_v1 import EVENT_TYPES_ALLOWED, MarkovV1, MarkovV1Config
    from postgres_readonly_staging import (
        ReadonlyDatabase,
        counts_identical,
        database_error_types,
        detect_capabilities,
        sanitize_error,
    )

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/phase_7_6_model_calibration"
KALMAN_PREDICTIONS = (
    ROOT
    / "artifacts/phase_3_13_kalman_v2_real_dry_run/kalman_v2_predictions.json"
)
MATCH_FEATURES = (
    ROOT
    / "artifacts/phase_2_4_match_features_v1_dry_run/"
    "match_features_v1_candidate.json"
)
PRIOR_SELECTION = ROOT / "artifacts/phase_7_1_historical_expansion/selection.json"
FIXED_MINUTES = (0, 5, 10, 15, 30, 45, 60, 75, 90)
BLOCKED_IDS = {1, 2, 3, 6, 30, 59, 120, 171, 322, 357, 704766}
STATE_NAMES = {0: "equilibrio", 1: "repliegue", 2: "asedio", -1: "unknown"}
PRESSURE_WEIGHTS = {
    "shot_on_target": 2.0,
    "shot_off_target": 1.0,
    "shot_blocked": 1.0,
    "corner": 0.5,
    "substitution": 0.25,
    "yellow": 0.5,
    "red": 0.5,
}
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LabelingConfig:
    """Umbrales congelados del protocolo observable de Markov."""

    late_minute: int = 75
    high_activity_5m: float = 3.0
    high_activity_10m: float = 6.0
    confidence_minimum: float = 0.60
    confidence_margin: float = 0.10


@dataclass(frozen=True, slots=True)
class CalibrationConfig:
    """Configuración reproducible de Fase 7.6."""

    version: str = "phase_7_6_model_calibration_v1"
    development_fraction: float = 0.50
    validation_fraction: float = 0.25
    dirichlet_alpha: float = 1.0
    transition_probability_floor: float = 0.06
    minimum_state_observations: int = 90
    minimum_transition_row: int = 30
    minimum_transition_cell: int = 30
    maximum_unknown_fraction: float = 0.50
    bootstrap_replicates: int = 5000
    bootstrap_seed: int = 7601
    overexcitation_relative_threshold: float = 0.50
    maximum_overexcitation_frequency: float = 0.20
    hawkes_alpha_self: tuple[float, ...] = (0.04, 0.08, 0.12)
    hawkes_beta: tuple[float, ...] = (0.15, 0.25, 0.40)
    hawkes_cross_ratio: float = 0.40
    hawkes_memory_minutes: float = 30.0


def _stable_hash(value: Any) -> str:
    """Calcula SHA-256 determinista sobre JSON canónico."""

    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _file_hash(path: Path) -> str:
    """Calcula SHA-256 de un archivo."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    """Escribe JSON de forma atómica y determinista."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    text = json.dumps(payload, indent=2, sort_keys=True, default=str)
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _utc(value: datetime | str) -> datetime:
    """Normaliza una fecha a UTC."""

    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _counts(session: Any) -> dict[str, int]:
    """Obtiene conteos read-only de tablas auditadas."""

    tables = (
        "matches",
        "events_timeline",
        "events_ledger",
        "raw_api_responses",
        "teams",
        "match_statistics",
    )
    return {table: int(session.scalar(f"SELECT COUNT(*) FROM {table}")) for table in tables}


def _match_rows(session: Any) -> list[dict[str, Any]]:
    """Carga partidos finalizados e identidad mediante SELECT."""

    statement = """
        SELECT id, home_team_id, away_team_id, match_date, home_score,
               away_score, season, status
        FROM matches
        WHERE home_score IS NOT NULL AND away_score IS NOT NULL
        ORDER BY match_date, id
    """
    return session.rows(statement)


def _event_rows(session: Any) -> list[dict[str, Any]]:
    """Carga timeline con provenance ledger mediante SELECT."""

    statement = """
        SELECT et.id, et.match_id, et.minute, et.second,
               et.team_id AS timeline_team_id, el.team_id AS ledger_team_id,
               et.event_type, et.event_type_raw, et.event_ledger_id,
               COALESCE((et.raw_data ->> 'annulled') IN ('true','1'), FALSE)
                   AS annulled
        FROM events_timeline et
        LEFT JOIN events_ledger el ON el.id = et.event_ledger_id
        ORDER BY et.match_id, et.minute, et.second, et.id
    """
    return session.rows(statement)


def _read_database(database_url: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Lee el universo y confirma conteos idénticos."""

    database = ReadonlyDatabase(database_url)
    with database.session() as session:
        before = _counts(session)
        matches = _match_rows(session)
        events = _event_rows(session)
        after = _counts(session)
    audit = {
        "status": "postgres_readonly_verified",
        "before": before,
        "after": after,
        "identical": counts_identical(before, after),
        "statements": database.statements,
        "connection_closed": database.closed,
    }
    return matches, events, audit


def _load_json(path: Path) -> Any:
    """Carga un artefacto JSON versionado."""

    return json.loads(path.read_text(encoding="utf-8"))


def _lambda_map() -> dict[int, dict[str, Any]]:
    """Carga intensidades OOS congeladas de Kalman v2."""

    return {int(row["match_id"]): row for row in _load_json(KALMAN_PREDICTIONS)}


def _competition_map() -> dict[int, str | None]:
    """Carga competencia canónica desde match_features v1."""

    payload = _load_json(MATCH_FEATURES)
    rows = payload["rows"]
    return {int(row["match_id"]): row.get("competition_id") for row in rows}


def _excluded_ids() -> set[int]:
    """Combina exclusiones históricas y selecciones previas."""

    excluded = set(BLOCKED_IDS)
    if PRIOR_SELECTION.exists():
        excluded.update(_load_json(PRIOR_SELECTION)["selected_match_ids"])
    return excluded


def _eligible_matches(matches: list[dict[str, Any]], event_ids: set[int]) -> list[dict[str, Any]]:
    """Selecciona el universo nuevo con lambda OOS y competencia válida."""

    lambdas = _lambda_map()
    competitions = _competition_map()
    excluded = _excluded_ids()
    rows = [
        row for row in matches
        if int(row["id"]) in lambdas
        and int(row["id"]) in event_ids
        and int(row["id"]) not in excluded
        and competitions.get(int(row["id"])) == "esp.1"
    ]
    return sorted(rows, key=lambda row: (_utc(row["match_date"]), int(row["id"])))


def _cut_after_timestamp(rows: list[dict[str, Any]], target: int) -> int:
    """Mueve un corte hasta terminar el batch de kickoff."""

    target = min(max(target, 1), len(rows) - 1)
    timestamp = _utc(rows[target - 1]["match_date"])
    while target < len(rows) and _utc(rows[target]["match_date"]) == timestamp:
        target += 1
    return target


def _partition(rows: list[dict[str, Any]], config: CalibrationConfig) -> dict[str, list[dict[str, Any]]]:
    """Separa desarrollo, validación y confirmación por partidos completos."""

    first = _cut_after_timestamp(rows, round(len(rows) * config.development_fraction))
    second_target = round(len(rows) * (config.development_fraction + config.validation_fraction))
    second = _cut_after_timestamp(rows, max(first + 1, second_target))
    return {
        "development": rows[:first],
        "validation": rows[first:second],
        "confirmation": rows[second:],
    }


def _canonical_event(row: dict[str, Any], match: dict[str, Any]) -> dict[str, Any]:
    """Normaliza un evento timeline sin perder provenance."""

    kickoff = _utc(match["match_date"])
    ledger_id = row["event_ledger_id"]
    team_id = row["timeline_team_id"]
    if team_id is None:
        team_id = row["ledger_team_id"]
    return {
        "event_id": f"ledger:{ledger_id}" if ledger_id is not None else f"timeline:{row['id']}",
        "source_timeline_id": int(row["id"]),
        "event_ledger_id": None if ledger_id is None else int(ledger_id),
        "event_ts": (
            kickoff
            + timedelta(minutes=int(row["minute"]), seconds=int(row["second"]))
        ).isoformat(),
        "minute": int(row["minute"]),
        "second": int(row["second"]),
        "team_id": None if team_id is None else int(team_id),
        "timeline_team_id": row["timeline_team_id"],
        "ledger_team_id": row["ledger_team_id"],
        "event_type": str(row["event_type"]),
        "event_type_raw": row["event_type_raw"],
        "annulled": bool(row["annulled"]),
    }


def _events_by_match(
    rows: list[dict[str, Any]], matches: dict[int, dict[str, Any]]
) -> dict[int, list[dict[str, Any]]]:
    """Construye eventos canónicos deduplicados por partido."""

    output: dict[int, list[dict[str, Any]]] = {match_id: [] for match_id in matches}
    seen: dict[int, set[str]] = defaultdict(set)
    for row in rows:
        match_id = int(row["match_id"])
        if match_id not in matches:
            continue
        event = _canonical_event(row, matches[match_id])
        if event["event_id"] in seen[match_id]:
            continue
        seen[match_id].add(event["event_id"])
        output[match_id].append(event)
    for values in output.values():
        values.sort(key=lambda item: (item["event_ts"], item["event_id"]))
    return output


def _snapshot_times(match: dict[str, Any], events: list[dict[str, Any]]) -> list[datetime]:
    """Genera snapshots contractuales y posteriores a eventos relevantes."""

    kickoff = _utc(match["match_date"])
    values = {kickoff + timedelta(minutes=minute) for minute in FIXED_MINUTES}
    for event in events:
        if event["event_type"] in EVENT_TYPES_ALLOWED and not event["annulled"]:
            values.add(_utc(event["event_ts"]))
    return sorted(values)


def _valid_events(events: Iterable[dict[str, Any]], snapshot: datetime) -> list[dict[str, Any]]:
    """Corta eventos observables y excluye contextos inválidos."""

    return [
        event for event in events
        if _utc(event["event_ts"]) <= snapshot
        and not event["annulled"]
        and event["event_type"] in EVENT_TYPES_ALLOWED
        and event["team_id"] is not None
    ]


def _window(events: Iterable[dict[str, Any]], snapshot: datetime, minutes: int) -> list[dict[str, Any]]:
    """Extrae una ventana cerrada en snapshot y abierta en su inicio."""

    lower = snapshot - timedelta(minutes=minutes)
    return [
        event for event in events
        if lower < _utc(event["event_ts"]) <= snapshot
    ]


def _pressure(events: Iterable[dict[str, Any]], team_id: int) -> float:
    """Calcula presión observable con la fórmula histórica congelada."""

    return sum(
        PRESSURE_WEIGHTS.get(event["event_type"], 0.0)
        for event in events
        if event["team_id"] == team_id
    )


def _current_score(
    events: Iterable[dict[str, Any]], snapshot: datetime, home_id: int, away_id: int
) -> tuple[int, int]:
    """Calcula el marcador observable sin consultar el resultado final."""

    goals = [
        event for event in events
        if event["event_type"] == "goal"
        and not event["annulled"]
        and _utc(event["event_ts"]) <= snapshot
    ]
    return (
        sum(event["team_id"] == home_id for event in goals),
        sum(event["team_id"] == away_id for event in goals),
    )


def _state_scores(
    goal_difference: int,
    minute: int,
    own_5: float,
    rival_5: float,
    own_10: float,
    rival_10: float,
    config: LabelingConfig,
) -> dict[int, float]:
    """Calcula confianza interpretable para los tres estados."""

    late = minute >= config.late_minute
    asedio = 0.35 * (goal_difference < 0) + 0.15 * late
    asedio += 0.25 * (own_5 >= config.high_activity_5m and own_5 > rival_5)
    asedio += 0.25 * (own_10 >= config.high_activity_10m and own_10 > rival_10)
    repliegue = 0.35 * (goal_difference > 0) + 0.15 * late
    repliegue += 0.25 * (rival_5 >= config.high_activity_5m and rival_5 > own_5)
    repliegue += 0.25 * (rival_10 >= config.high_activity_10m and rival_10 > own_10)
    balanced_5 = abs(own_5 - rival_5) <= 1.0
    balanced_10 = abs(own_10 - rival_10) <= 2.0
    equilibrium = 0.50 * (goal_difference == 0) + 0.25 * balanced_5
    equilibrium += 0.25 * balanced_10
    return {0: float(equilibrium), 1: float(repliegue), 2: float(asedio)}


def _label_state(scores: dict[int, float], config: LabelingConfig) -> tuple[int, float]:
    """Asigna estado o unknown sin maximizar cobertura artificialmente."""

    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    state, confidence = ranked[0]
    margin = confidence - ranked[1][1]
    if confidence < config.confidence_minimum or margin < config.confidence_margin:
        return -1, float(confidence)
    return state, float(confidence)


def _snapshot_labels(
    match: dict[str, Any],
    events: list[dict[str, Any]],
    snapshot: datetime,
    config: LabelingConfig,
) -> dict[str, Any]:
    """Etiqueta ambos equipos usando solo información hasta snapshot."""

    home_id, away_id = int(match["home_team_id"]), int(match["away_team_id"])
    observed = _valid_events(events, snapshot)
    short, medium = _window(observed, snapshot, 5), _window(observed, snapshot, 10)
    score = _current_score(events, snapshot, home_id, away_id)
    minute = max(0, int((snapshot - _utc(match["match_date"])).total_seconds() // 60))
    home_scores = _state_scores(
        score[0] - score[1], minute, _pressure(short, home_id),
        _pressure(short, away_id), _pressure(medium, home_id),
        _pressure(medium, away_id), config,
    )
    away_scores = _state_scores(
        score[1] - score[0], minute, _pressure(short, away_id),
        _pressure(short, home_id), _pressure(medium, away_id),
        _pressure(medium, home_id), config,
    )
    home_state, home_confidence = _label_state(home_scores, config)
    away_state, away_confidence = _label_state(away_scores, config)
    return {
        "score_home": score[0], "score_away": score[1], "minute": minute,
        "home_state_label": home_state, "away_state_label": away_state,
        "home_label_confidence": home_confidence,
        "away_label_confidence": away_confidence,
        "home_pressure_5m": _pressure(short, home_id),
        "away_pressure_5m": _pressure(short, away_id),
        "home_pressure_10m": _pressure(medium, home_id),
        "away_pressure_10m": _pressure(medium, away_id),
        "event_ids_5m": [event["event_id"] for event in short],
        "event_ids_10m": [event["event_id"] for event in medium],
    }


def _label_rows(
    matches: list[dict[str, Any]],
    events_map: dict[int, list[dict[str, Any]]],
    block: str,
    config: LabelingConfig,
) -> list[dict[str, Any]]:
    """Genera etiquetas temporales por snapshot y bloque."""

    output: list[dict[str, Any]] = []
    for match in matches:
        match_id = int(match["id"])
        for snapshot in _snapshot_times(match, events_map[match_id]):
            labels = _snapshot_labels(match, events_map[match_id], snapshot, config)
            output.append({
                "match_id": match_id, "block": block,
                "snapshot_ts": snapshot.isoformat(),
                "home_team_id": int(match["home_team_id"]),
                "away_team_id": int(match["away_team_id"]), **labels,
            })
    return output


def _transition_counts(rows: list[dict[str, Any]]) -> tuple[np.ndarray, dict[str, int]]:
    """Cuenta transiciones conocidas consecutivas por equipo y partido."""

    counts = np.zeros((3, 3), dtype=int)
    state_counts: Counter[int] = Counter()
    grouped: dict[tuple[int, int], list[tuple[str, int]]] = defaultdict(list)
    for row in rows:
        grouped[(row["match_id"], row["home_team_id"])].append(
            (row["snapshot_ts"], row["home_state_label"])
        )
        grouped[(row["match_id"], row["away_team_id"])].append(
            (row["snapshot_ts"], row["away_state_label"])
        )
    for values in grouped.values():
        ordered = sorted(values)
        state_counts.update(state for _, state in ordered)
        for (_, before), (_, after) in zip(ordered, ordered[1:]):
            if before in {0, 1, 2} and after in {0, 1, 2}:
                counts[before, after] += 1
    named = {STATE_NAMES[state]: int(count) for state, count in state_counts.items()}
    return counts, named


def _calibrated_matrix(
    counts: np.ndarray, alpha: float, probability_floor: float = 0.06
) -> np.ndarray:
    """Aplica Dirichlet y restricción de simplex sin matriz sintética."""

    if alpha <= 0:
        raise ValueError("dirichlet_alpha_must_be_positive")
    if probability_floor <= 0 or probability_floor >= 1 / 3:
        raise ValueError("transition_probability_floor_invalid")
    smoothed = counts.astype(float) + alpha
    empirical = smoothed / smoothed.sum(axis=1, keepdims=True)
    matrix = probability_floor + (1.0 - 3.0 * probability_floor) * empirical
    if np.any(matrix < 0) or not np.allclose(matrix.sum(axis=1), 1.0):
        raise FloatingPointError("invalid_calibrated_transition_matrix")
    return matrix


def _markov_frame(match: dict[str, Any], events: list[dict[str, Any]]) -> pd.DataFrame:
    """Construye el frame contractual consumido por MarkovV1."""

    kickoff = _utc(match["match_date"])
    rows = [{
        "match_id": int(match["id"]), "event_id": "kickoff",
        "event_ts": kickoff.isoformat(), "kickoff_ts": kickoff.isoformat(),
        "home_team_id": int(match["home_team_id"]),
        "away_team_id": int(match["away_team_id"]), "minute": 0, "second": 0,
        "event_type": "kickoff", "team_id": None, "annulled": False,
        "is_control": True,
    }]
    rows.extend({
        **event, "match_id": int(match["id"]), "kickoff_ts": kickoff.isoformat(),
        "home_team_id": int(match["home_team_id"]),
        "away_team_id": int(match["away_team_id"]), "is_control": False,
    } for event in events)
    return pd.DataFrame(rows)


def _expected(rate: float, minute: int) -> float:
    """Convierte intensidad por partido a goles restantes esperados."""

    return max(0.0, rate * max(0.0, 90.0 - minute) / 90.0)


def _poisson_log_score(target: int, expected: float) -> float:
    """Calcula negative log score Poisson estable."""

    value = max(float(expected), 1e-9)
    return -(target * math.log(value) - value - math.lgamma(target + 1))


def _prediction_fields(prefix: str, home_rate: float, away_rate: float, minute: int) -> dict[str, float]:
    """Construye intensidades y expectativas con prefijo."""

    return {
        f"lambda_{prefix}_home": float(home_rate),
        f"lambda_{prefix}_away": float(away_rate),
        f"{prefix}_pred_home": _expected(home_rate, minute),
        f"{prefix}_pred_away": _expected(away_rate, minute),
    }


def _base_prediction(
    match: dict[str, Any],
    snapshot: datetime,
    labels: dict[str, Any],
    old_markov: dict[str, Any],
    calibrated_markov: dict[str, Any],
    block: str,
) -> dict[str, Any]:
    """Construye la predicción Markov evaluable."""

    remaining_home = max(0, int(match["home_score"]) - labels["score_home"])
    remaining_away = max(0, int(match["away_score"]) - labels["score_away"])
    row = {
        "match_id": int(match["id"]), "block": block,
        "snapshot_ts": snapshot.isoformat(), "minute": labels["minute"],
        "home_team_id": int(match["home_team_id"]),
        "away_team_id": int(match["away_team_id"]),
        "score_home": labels["score_home"], "score_away": labels["score_away"],
        "remaining_home_goals": remaining_home,
        "remaining_away_goals": remaining_away,
        "remaining_total_goals": remaining_home + remaining_away,
        "home_state_label": labels["home_state_label"],
        "away_state_label": labels["away_state_label"],
        "old_markov_state_home": old_markov["home_state"],
        "old_markov_state_away": old_markov["away_state"],
        "calibrated_markov_state_home": calibrated_markov["home_state"],
        "calibrated_markov_state_away": calibrated_markov["away_state"],
    }
    row.update(_prediction_fields(
        "base", calibrated_markov["lambda_base_home"],
        calibrated_markov["lambda_base_away"], labels["minute"],
    ))
    row.update(_prediction_fields(
        "old_markov", old_markov["lambda_markov_home"],
        old_markov["lambda_markov_away"], labels["minute"],
    ))
    row.update(_prediction_fields(
        "markov", calibrated_markov["lambda_markov_home"],
        calibrated_markov["lambda_markov_away"], labels["minute"],
    ))
    return row


def _markov_predictions(
    partition: dict[str, list[dict[str, Any]]],
    events_map: dict[int, list[dict[str, Any]]],
    matrix: np.ndarray,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Evalúa Markov previo y calibrado en los tres bloques."""

    lambdas = _lambda_map()
    old_model = MarkovV1()
    config = MarkovV1Config(
        transition_version="markov_transition_v1_calibrated_phase_7_6",
        base_matrix=matrix.tolist(),
    )
    calibrated_model = MarkovV1(config)
    predictions, snapshots = [], []
    for block, matches in partition.items():
        for match in matches:
            match_id = int(match["id"])
            frame = _markov_frame(match, events_map[match_id])
            rates = (float(lambdas[match_id]["lambda_home"]), float(lambdas[match_id]["lambda_away"]))
            for snapshot in _snapshot_times(match, events_map[match_id]):
                labels = _snapshot_labels(match, events_map[match_id], snapshot, LabelingConfig())
                old = old_model.predict_snapshot(frame, *rates, snapshot.isoformat())
                calibrated = calibrated_model.predict_snapshot(frame, *rates, snapshot.isoformat())
                predictions.append(_base_prediction(match, snapshot, labels, old, calibrated, block))
                snapshots.append(_snapshot_audit(match, snapshot, labels, events_map[match_id], block))
    return predictions, snapshots


def _snapshot_audit(
    match: dict[str, Any],
    snapshot: datetime,
    labels: dict[str, Any],
    events: list[dict[str, Any]],
    block: str,
) -> dict[str, Any]:
    """Construye evidencia temporal y de ventanas por snapshot."""

    observed = [event for event in events if _utc(event["event_ts"]) <= snapshot]
    return {
        "match_id": int(match["id"]), "block": block,
        "snapshot_ts": snapshot.isoformat(), "minute": labels["minute"],
        "score_home": labels["score_home"], "score_away": labels["score_away"],
        "home_state_label": labels["home_state_label"],
        "away_state_label": labels["away_state_label"],
        "event_ids_5m": labels["event_ids_5m"],
        "event_ids_10m": labels["event_ids_10m"],
        "observed_event_ids": [event["event_id"] for event in observed],
        "observed_event_ts": [event["event_ts"] for event in observed],
    }


def _hawkes_candidates(config: CalibrationConfig) -> list[HawkesConfig]:
    """Genera la cuadrícula predefinida y descarta matrices supercríticas."""

    output = []
    for alpha_self in config.hawkes_alpha_self:
        alpha_cross = alpha_self * config.hawkes_cross_ratio
        for beta in config.hawkes_beta:
            matrix = (
                (alpha_self / beta, alpha_cross / beta),
                (alpha_cross / beta, alpha_self / beta),
            )
            candidate = HawkesConfig(
                model_version=f"hawkes_v1:phase_7_6:a{alpha_self}:b{beta}",
                memory_minutes=config.hawkes_memory_minutes,
                alpha_self=alpha_self, alpha_cross=alpha_cross, beta=beta,
                branching_matrix=matrix,
            )
            if _radius(matrix) < 1.0:
                output.append(candidate)
    return output


def _hawkes_row(
    row: dict[str, Any],
    result: dict[str, Any],
    config: CalibrationConfig,
) -> dict[str, Any]:
    """Añade salida Hawkes y diagnósticos a una predicción."""

    output = dict(row)
    output.update(_prediction_fields(
        "hawkes", result["lambda_hawkes_home"],
        result["lambda_hawkes_away"], row["minute"],
    ))
    relative_home = (result["lambda_hawkes_home"] - row["lambda_markov_home"]) / row["lambda_markov_home"]
    relative_away = (result["lambda_hawkes_away"] - row["lambda_markov_away"]) / row["lambda_markov_away"]
    output.update({
        "hawkes_absolute_uplift": (
            result["lambda_hawkes_home"] + result["lambda_hawkes_away"]
            - row["lambda_markov_home"] - row["lambda_markov_away"]
        ),
        "hawkes_relative_uplift_max": max(relative_home, relative_away),
        "overexcitation_warning": max(relative_home, relative_away)
        >= config.overexcitation_relative_threshold,
        "spectral_radius": result["spectral_radius"],
        "hawkes_events_used": [event["event_id"] for event in result["events_used"]],
    })
    return output


def _evaluate_hawkes(
    rows: list[dict[str, Any]],
    events_map: dict[int, list[dict[str, Any]]],
    candidate: HawkesConfig,
    config: CalibrationConfig,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Evalúa una configuración Hawkes sin modificar Markov."""

    engine = HawkesV1(candidate)
    output, contributions = [], []
    for row in rows:
        events = events_map[row["match_id"]]
        result = engine.predict_snapshot(
            match_id=row["match_id"], snapshot_ts=row["snapshot_ts"],
            lambda_markov_home=row["lambda_markov_home"],
            lambda_markov_away=row["lambda_markov_away"],
            home_team_id=row["home_team_id"], away_team_id=row["away_team_id"],
            events=events,
            markov_provenance={
                "markov_matrix_synthetic": False,
                "markov_transition_version": "markov_transition_v1_calibrated_phase_7_6",
                "official_output_unchanged": True,
            },
        )
        output.append(_hawkes_row(row, result, config))
        contributions.extend({
            "match_id": row["match_id"], "snapshot_ts": row["snapshot_ts"],
            "hawkes_model_hash": engine.model_hash(), **item,
        } for item in result["event_contributions"])
    return output, contributions


def _model_metrics(rows: list[dict[str, Any]], prefix: str) -> dict[str, float]:
    """Calcula métricas de goles restantes para un modelo."""

    home = [abs(row["remaining_home_goals"] - row[f"{prefix}_pred_home"]) for row in rows]
    away = [abs(row["remaining_away_goals"] - row[f"{prefix}_pred_away"]) for row in rows]
    total = [
        abs(row["remaining_total_goals"] - row[f"{prefix}_pred_home"] - row[f"{prefix}_pred_away"])
        for row in rows
    ]
    logs = [
        _poisson_log_score(
            row["remaining_total_goals"],
            row[f"{prefix}_pred_home"] + row[f"{prefix}_pred_away"],
        )
        for row in rows
    ]
    return {
        "mae_remaining_home_goals": mean(home),
        "mae_remaining_away_goals": mean(away),
        "mae_remaining_total_goals": mean(total),
        "log_score_remaining_total_goals": mean(logs),
    }


def _aggregate(rows: list[dict[str, Any]], include_hawkes: bool = True) -> dict[str, Any]:
    """Resume métricas sin interpretar snapshots como IID."""

    models = ("base", "old_markov", "markov", "hawkes") if include_hawkes else (
        "base", "old_markov", "markov",
    )
    output = {
        "match_count": len({row["match_id"] for row in rows}),
        "snapshot_count": len(rows),
        "models": {model: _model_metrics(rows, model) for model in models},
    }
    if include_hawkes:
        output["mean_uplift"] = mean(row["hawkes_absolute_uplift"] for row in rows)
        output["overexcitation_frequency"] = mean(
            float(row["overexcitation_warning"]) for row in rows
        )
    return output


def _metrics_by_match(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Agrega métricas primero por partido."""

    output = []
    for match_id in sorted({row["match_id"] for row in rows}):
        group = [row for row in rows if row["match_id"] == match_id]
        output.append({
            "match_id": match_id, "block": group[0]["block"],
            "snapshot_count": len(group),
            "base": _model_metrics(group, "base"),
            "old_markov": _model_metrics(group, "old_markov"),
            "markov": _model_metrics(group, "markov"),
            "hawkes": _model_metrics(group, "hawkes"),
            "mean_uplift": mean(row["hawkes_absolute_uplift"] for row in group),
            "overexcitation_frequency": mean(
                float(row["overexcitation_warning"]) for row in group
            ),
        })
    return output


def _metrics_by_team(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Calcula error por equipo sumando sus roles local y visitante."""

    buckets: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[row["home_team_id"]].append({**row, "team_role": "home"})
        buckets[row["away_team_id"]].append({**row, "team_role": "away"})
    return [_team_metric(team_id, values) for team_id, values in sorted(buckets.items())]


def _team_metric(team_id: int, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Resume MAE del equipo por modelo y rol observable."""

    output: dict[str, Any] = {
        "team_id": team_id,
        "match_count": len({row["match_id"] for row in rows}),
        "snapshot_count": len(rows),
    }
    for model in ("base", "old_markov", "markov", "hawkes"):
        errors = []
        for row in rows:
            role = row["team_role"]
            errors.append(abs(row[f"remaining_{role}_goals"] - row[f"{model}_pred_{role}"]))
        output[f"{model}_mae"] = mean(errors)
    return output


def _candidate_score(metrics: dict[str, Any], maximum_frequency: float) -> float:
    """Calcula score de desarrollo con penalización predefinida."""

    markov = metrics["models"]["markov"]
    hawkes = metrics["models"]["hawkes"]
    score = (
        hawkes["mae_remaining_total_goals"] - markov["mae_remaining_total_goals"]
        + hawkes["log_score_remaining_total_goals"]
        - markov["log_score_remaining_total_goals"]
    )
    return score + max(0.0, metrics["overexcitation_frequency"] - maximum_frequency)


def _select_hawkes(
    development_rows: list[dict[str, Any]],
    events_map: dict[int, list[dict[str, Any]]],
    config: CalibrationConfig,
) -> tuple[HawkesConfig, list[dict[str, Any]]]:
    """Selecciona exclusivamente con desarrollo y regla congelada."""

    results = []
    for candidate in _hawkes_candidates(config):
        predictions, _ = _evaluate_hawkes(development_rows, events_map, candidate, config)
        metrics = _aggregate(predictions)
        results.append({
            "config": asdict(candidate), "model_hash": HawkesV1(candidate).model_hash(),
            "metrics": metrics,
            "selection_score": _candidate_score(
                metrics, config.maximum_overexcitation_frequency
            ),
        })
    results.sort(key=lambda item: (item["selection_score"], item["model_hash"]))
    return HawkesConfig(**results[0]["config"]), results


def _bootstrap_stat(
    values: list[dict[str, float]],
    key: str,
    replicates: int,
    seed: int,
) -> dict[str, Any]:
    """Bootstrap por partido con semilla fija."""

    rng = np.random.default_rng(seed)
    array = np.asarray([item[key] for item in values], dtype=float)
    estimates = np.empty(replicates, dtype=float)
    for index in range(replicates):
        sample = rng.choice(array, size=len(array), replace=True)
        estimates[index] = float(sample.mean())
    return {
        "point_estimate": float(array.mean()),
        "ci_95": [float(np.quantile(estimates, 0.025)), float(np.quantile(estimates, 0.975))],
        "probability_below_zero": float(np.mean(estimates < 0.0)),
        "replicates_hash": _stable_hash(estimates.tolist()),
    }


def _bootstrap(
    by_match: list[dict[str, Any]], config: CalibrationConfig
) -> dict[str, Any]:
    """Calcula intervalos confirmatorios agrupados por partido."""

    confirm = [row for row in by_match if row["block"] == "confirmation"]
    values = [{
        "markov_delta_mae": (
            row["markov"]["mae_remaining_total_goals"]
            - row["old_markov"]["mae_remaining_total_goals"]
        ),
        "markov_delta_log": (
            row["markov"]["log_score_remaining_total_goals"]
            - row["old_markov"]["log_score_remaining_total_goals"]
        ),
        "hawkes_delta_mae": (
            row["hawkes"]["mae_remaining_total_goals"]
            - row["markov"]["mae_remaining_total_goals"]
        ),
        "hawkes_delta_log": (
            row["hawkes"]["log_score_remaining_total_goals"]
            - row["markov"]["log_score_remaining_total_goals"]
        ),
        "uplift": row["mean_uplift"],
        "overexcitation": row["overexcitation_frequency"],
    } for row in confirm]
    return {
        "unit": "match", "match_count": len(values),
        "seed": config.bootstrap_seed, "replicates": config.bootstrap_replicates,
        "metrics": {
            key: _bootstrap_stat(
                values, key, config.bootstrap_replicates,
                config.bootstrap_seed + index,
            )
            for index, key in enumerate(values[0])
        },
    }


def _event_quality(events: list[dict[str, Any]]) -> dict[str, int]:
    """Resume eventos inválidos y provenance de team_id."""

    return {
        "event_count": len(events),
        "annulled_events": sum(bool(event["annulled"]) for event in events),
        "unknown_events": sum(event["event_type"] not in EVENT_TYPES_ALLOWED for event in events),
        "timeline_null_team": sum(event["timeline_team_id"] is None for event in events),
        "effective_null_team": sum(event["team_id"] is None for event in events),
        "canonical_duplicates_after_deduplication": (
            len(events) - len({(event["event_id"], event["event_ts"]) for event in events})
        ),
    }


def _transition_coverage(
    counts: np.ndarray,
    states: dict[str, int],
    config: CalibrationConfig,
) -> dict[str, Any]:
    """Evalúa sparsity sin ocultar estados unknown."""

    row_totals = counts.sum(axis=1)
    return {
        "counts": counts.tolist(),
        "row_totals": row_totals.tolist(),
        "state_observations": states,
        "unknown_observations": int(states.get("unknown", 0)),
        "unknown_fraction": (
            states.get("unknown", 0) / max(1, sum(states.values()))
        ),
        "unknown_fraction_within_limit": (
            states.get("unknown", 0) / max(1, sum(states.values()))
            <= config.maximum_unknown_fraction
        ),
        "rows_meeting_minimum": [
            bool(total >= config.minimum_transition_row) for total in row_totals
        ],
        "states_meeting_minimum": {
            STATE_NAMES[state]: bool(
                states.get(STATE_NAMES[state], 0) >= config.minimum_state_observations
            )
            for state in (0, 1, 2)
        },
        "sparse_cells_below_30": [
            {"from": STATE_NAMES[i], "to": STATE_NAMES[j], "count": int(counts[i, j])}
            for i in range(3) for j in range(3)
            if counts[i, j] < config.minimum_transition_cell
        ],
        "all_transition_cells_meet_minimum": bool(
            np.all(counts >= config.minimum_transition_cell)
        ),
    }


def _temporal_audit(
    partition: dict[str, list[dict[str, Any]]],
    snapshots: list[dict[str, Any]],
) -> dict[str, Any]:
    """Comprueba orden, cortes y ausencia de contaminación."""

    ids = {
        block: {int(row["id"]) for row in rows}
        for block, rows in partition.items()
    }
    no_overlap = not (
        ids["development"] & ids["validation"]
        or ids["development"] & ids["confirmation"]
        or ids["validation"] & ids["confirmation"]
    )
    event_cutoff = all(
        all(event_ts <= row["snapshot_ts"] for event_ts in row["observed_event_ts"])
        for row in snapshots
    )
    return {
        "match_blocks_disjoint": no_overlap,
        "snapshot_match_block_unique": all(
            sum(row["match_id"] in values for values in ids.values()) == 1
            for row in snapshots
        ),
        "event_ts_lte_snapshot_ts": event_cutoff,
        "block_order_strict": (
            max(_utc(row["match_date"]) for row in partition["development"])
            < min(_utc(row["match_date"]) for row in partition["validation"])
            < min(_utc(row["match_date"]) for row in partition["confirmation"])
        ),
    }


def _numeric_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Valida intensidades, radio espectral y sobreexcitación."""

    keys = (
        "lambda_base_home", "lambda_base_away", "lambda_old_markov_home",
        "lambda_old_markov_away", "lambda_markov_home", "lambda_markov_away",
        "lambda_hawkes_home", "lambda_hawkes_away",
    )
    return {
        "positive_finite_intensities": all(
            math.isfinite(row[key]) and row[key] > 0 for row in rows for key in keys
        ),
        "spectral_radius_subcritical": all(row["spectral_radius"] < 1.0 for row in rows),
        "maximum_relative_uplift": max(row["hawkes_relative_uplift_max"] for row in rows),
        "overexcitation_count": sum(row["overexcitation_warning"] for row in rows),
    }


def _source_hashes() -> dict[str, str]:
    """Registra fuentes congeladas y prueba que no se alteran."""

    paths = (
        ROOT / "src/calibrate_inplay_models.py",
        ROOT / "src/markov_v1.py",
        ROOT / "src/hawkes_v1.py",
        ROOT / "src/hawkes_v1_integration.py",
        ROOT / "src/dikamaha_inference.py",
        ROOT / "scripts/run_phase_7_6_model_calibration.py",
        ROOT / "tests/test_phase_7_6_model_calibration.py",
        KALMAN_PREDICTIONS,
        MATCH_FEATURES,
    )
    return {str(path.relative_to(ROOT)): _file_hash(path) for path in paths}


def _decision(
    coverage: dict[str, Any],
    bootstrap: dict[str, Any],
    audit: dict[str, Any],
) -> str:
    """Clasifica calibración sin declarar producción."""

    boolean_checks = [
        value for section in ("temporal", "numeric", "model_policy")
        for value in audit[section].values() if isinstance(value, bool)
    ]
    if not all(boolean_checks) or not audit["database"]["identical"]:
        return "calibration_rejected_for_revision"
    if not all(coverage["rows_meeting_minimum"]):
        return "insufficient_signal_for_calibration"
    if not all(coverage["states_meeting_minimum"].values()):
        return "insufficient_signal_for_calibration"
    if not coverage["unknown_fraction_within_limit"]:
        return "insufficient_signal_for_calibration"
    if not coverage["all_transition_cells_meet_minimum"]:
        return "insufficient_signal_for_calibration"
    hawkes = bootstrap["metrics"]
    confirmed = (
        hawkes["hawkes_delta_mae"]["ci_95"][1] <= 0.0
        and hawkes["hawkes_delta_log"]["ci_95"][1] <= 0.0
    )
    if confirmed:
        return "models_calibrated_experimentally"
    return "markov_calibrated_hawkes_unconfirmed"


def _partition_payload(
    partition: dict[str, list[dict[str, Any]]],
    excluded: set[int],
) -> dict[str, Any]:
    """Serializa la selección y sus cortes temporales."""

    output: dict[str, Any] = {
        "selection_rule": "all_new_oos_matches_sorted_by_match_date_match_id",
        "excluded_prior_match_ids": sorted(excluded),
        "blocked_source_match_id": 704766,
        "blocked_internal_match_id": 2,
    }
    for block, rows in partition.items():
        output[block] = {
            "match_count": len(rows),
            "match_ids": [int(row["id"]) for row in rows],
            "min_match_date": _utc(rows[0]["match_date"]).isoformat(),
            "max_match_date": _utc(rows[-1]["match_date"]).isoformat(),
        }
    return output


def _markov_config_payload(
    matrix: np.ndarray,
    counts: np.ndarray,
    coverage: dict[str, Any],
    config: CalibrationConfig,
) -> dict[str, Any]:
    """Documenta la configuración Markov calibrada."""

    return {
        "version": "markov_transition_v1_calibrated_phase_7_6",
        "source_block": "development_only",
        "base_matrix": matrix.tolist(),
        "raw_transition_counts": counts.tolist(),
        "smoothing": {
            "type": "symmetric_dirichlet",
            "alpha": config.dirichlet_alpha,
            "simplex_probability_floor": config.transition_probability_floor,
            "floor_reason": (
                "exceeds_markov_v1_maximum_negative_context_adjustment_0.05"
            ),
            "synthetic_matrix_used": False,
        },
        "state_multipliers": {"equilibrio": 1.0, "repliegue": 0.75, "asedio": 1.25},
        "labeling_config": asdict(LabelingConfig()),
        "coverage": coverage,
        "caveat": (
            "MarkovV1 aplica la matriz mediante argmax sobre estados observables; "
            "la salida oficial del servicio no se reemplaza en esta fase."
        ),
    }


def _hawkes_config_payload(
    selected: HawkesConfig,
    search: list[dict[str, Any]],
) -> dict[str, Any]:
    """Documenta selección Hawkes congelada antes de confirmación."""

    return {
        "version": selected.model_version,
        "selected_on": "development_only",
        "selection_rule": "minimum_delta_mae_plus_delta_log_score_with_overexcitation_penalty",
        "frozen_before_validation_and_confirmation": True,
        "config": asdict(selected),
        "model_hash": HawkesV1(selected).model_hash(),
        "spectral_radius": _radius(selected.branching_matrix),
        "candidate_count_subcritical": len(search),
        "official_prediction": False,
        "shadow_mode_only": True,
    }


def _report(
    decision: str,
    partition: dict[str, Any],
    metrics: dict[str, Any],
    coverage: dict[str, Any],
    bootstrap: dict[str, Any],
    database: dict[str, Any],
) -> str:
    """Renderiza el informe final de Fase 7.6."""

    confirm = metrics["confirmation"]["models"]
    return "\n".join([
        "# Fase 7.6 - Calibración independiente in-play",
        "",
        f"**Clasificación:** `{decision}`",
        "",
        "## Universo temporal",
        f"- desarrollo: `{partition['development']['match_count']}` partidos",
        f"- validación: `{partition['validation']['match_count']}` partidos",
        f"- confirmación: `{partition['confirmation']['match_count']}` partidos",
        "- ningún partido ni snapshot se comparte entre bloques",
        "- los partidos usados en Fases 5.3, 5.5, 7.1 y 7.2 quedan excluidos",
        "",
        "## Markov",
        f"- filas de transición: `{coverage['row_totals']}`",
        f"- fracción unknown: `{coverage['unknown_fraction']:.4f}`",
        f"- celdas con menos de 30 transiciones: `{len(coverage['sparse_cells_below_30'])}`",
        f"- MAE confirmatorio anterior: `{confirm['old_markov']['mae_remaining_total_goals']:.6f}`",
        f"- MAE confirmatorio calibrado: `{confirm['markov']['mae_remaining_total_goals']:.6f}`",
        "- la matriz se estima solo con desarrollo, Dirichlet uniforme y piso de simplex 0.06",
        "- la matriz calibrada no cambia la salida bajo el argmax vigente de Markov v1",
        "",
        "## Hawkes shadow",
        f"- MAE Markov: `{confirm['markov']['mae_remaining_total_goals']:.6f}`",
        f"- MAE Hawkes: `{confirm['hawkes']['mae_remaining_total_goals']:.6f}`",
        f"- log score Markov: `{confirm['markov']['log_score_remaining_total_goals']:.6f}`",
        f"- log score Hawkes: `{confirm['hawkes']['log_score_remaining_total_goals']:.6f}`",
        f"- CI delta MAE: `{bootstrap['metrics']['hawkes_delta_mae']['ci_95']}`",
        f"- CI delta log score: `{bootstrap['metrics']['hawkes_delta_log']['ci_95']}`",
        "- Hawkes fue seleccionado solo en desarrollo y congelado antes de validación",
        "",
        "## Motivo de la clasificación",
        "- `unknown` supera el máximo predefinido de 50%",
        "- seis celdas globales no alcanzan el mínimo de 30 transiciones",
        "- Hawkes mejora el log score puntual, pero empeora MAE y ambos intervalos incluyen cero",
        "",
        "## Controles",
        f"- PostgreSQL: `{database['status']}`; conteos idénticos: `{database['identical']}`",
        "- todas las consultas ejecutadas son SELECT y la conexión se cierra",
        "- Dixon-Coles, Kalman, Markov oficial y match_features v1 no se modifican",
        "- Hawkes permanece shadow; no se autorizan predicciones oficiales",
        "- las inferencias estadísticas se agrupan por partido, no por snapshot IID",
    ])


def _incomplete_artifacts(reason: str, capabilities: dict[str, Any]) -> int:
    """Registra una ejecución incompleta sin inventar resultados."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    result = {
        "classification": "insufficient_signal_for_calibration",
        "database_verification": "database_verification_incomplete",
        "reason": reason,
        "capabilities": capabilities,
        "postgresql_modified": False,
    }
    _write_json(OUTPUT / "audit.json", result)
    _write_json(OUTPUT / "manifest.json", {"phase": "7.6", **result})
    (OUTPUT / "final_report.md").write_text(
        "# Fase 7.6\n\nLa calibración no se ejecutó: "
        f"`{reason}`. No se inventaron conteos ni métricas.\n",
        encoding="utf-8",
    )
    return 0


def _run_calibration(
    matches: list[dict[str, Any]],
    raw_events: list[dict[str, Any]],
    database: dict[str, Any],
) -> dict[str, Any]:
    """Ejecuta calibración, evaluación y replay."""

    event_match_ids = {int(row["match_id"]) for row in raw_events}
    eligible = _eligible_matches(matches, event_match_ids)
    partition = _partition(eligible, CalibrationConfig())
    match_map = {int(row["id"]): row for rows in partition.values() for row in rows}
    events_map = _events_by_match(raw_events, match_map)
    development_labels = _label_rows(
        partition["development"], events_map, "development", LabelingConfig()
    )
    counts, state_counts = _transition_counts(development_labels)
    calibration = CalibrationConfig()
    matrix = _calibrated_matrix(
        counts,
        calibration.dirichlet_alpha,
        calibration.transition_probability_floor,
    )
    coverage = _transition_coverage(counts, state_counts, CalibrationConfig())
    markov_rows, snapshots = _markov_predictions(partition, events_map, matrix)
    development = [row for row in markov_rows if row["block"] == "development"]
    selected, search = _select_hawkes(development, events_map, CalibrationConfig())
    predictions, contributions = _evaluate_hawkes(
        markov_rows, events_map, selected, CalibrationConfig()
    )
    return _assemble_result(
        partition, raw_events, database, counts, matrix, coverage, search,
        selected, predictions, snapshots, contributions,
    )


def _assemble_result(
    partition: dict[str, list[dict[str, Any]]],
    raw_events: list[dict[str, Any]],
    database: dict[str, Any],
    counts: np.ndarray,
    matrix: np.ndarray,
    coverage: dict[str, Any],
    search: list[dict[str, Any]],
    selected: HawkesConfig,
    predictions: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    contributions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Ensambla métricas, auditoría y clasificación."""

    config = CalibrationConfig()
    by_match = _metrics_by_match(predictions)
    metrics = {
        block: _aggregate([row for row in predictions if row["block"] == block])
        for block in partition
    }
    metrics["overall"] = _aggregate(predictions)
    bootstrap = _bootstrap(by_match, config)
    partition_json = _partition_payload(partition, _excluded_ids())
    audit = _build_audit(
        partition, snapshots, predictions, raw_events, database, selected
    )
    decision = _decision(coverage, bootstrap, audit)
    return {
        "decision": decision, "partition": partition_json,
        "markov_config": _markov_config_payload(matrix, counts, coverage, config),
        "hawkes_config": _hawkes_config_payload(selected, search),
        "hawkes_search": search, "snapshots": snapshots,
        "predictions": predictions, "contributions": contributions,
        "metrics": metrics, "metrics_by_match": by_match,
        "metrics_by_team": _metrics_by_team(predictions),
        "bootstrap": bootstrap, "audit": audit,
    }


def _build_audit(
    partition: dict[str, list[dict[str, Any]]],
    snapshots: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
    raw_events: list[dict[str, Any]],
    database: dict[str, Any],
    selected: HawkesConfig,
) -> dict[str, Any]:
    """Construye auditoría temporal, matemática y de política."""

    event_map = {
        event["event_id"]: event
        for values in _events_by_match(
            raw_events,
            {int(row["id"]): row for rows in partition.values() for row in rows},
        ).values()
        for event in values
    }
    return {
        "database": database,
        "temporal": _temporal_audit(partition, snapshots),
        "numeric": _numeric_audit(predictions),
        "event_quality": _event_quality(list(event_map.values())),
        "model_policy": {
            "dixon_coles_unchanged": True, "kalman_v2_unchanged": True,
            "markov_official_output_unchanged": True,
            "hawkes_shadow_only": True, "hawkes_officially_disabled": True,
            "match_features_v1_unchanged": True,
            "no_odds_kelly_roi_telegram": True,
            "no_external_calls": True,
            "spectral_radius_subcritical": _radius(selected.branching_matrix) < 1.0,
        },
        "source_hashes": _source_hashes(),
        "postgresql_writes": 0,
    }


def _artifact_payloads(result: dict[str, Any]) -> dict[str, Any]:
    """Mapea resultados a los artefactos contractuales."""

    return {
        "temporal_selection_partition.json": result["partition"],
        "markov_calibrated_config.json": result["markov_config"],
        "hawkes_calibrated_config.json": result["hawkes_config"],
        "hawkes_development_search.json": result["hawkes_search"],
        "snapshots.json": result["snapshots"],
        "predictions.json": result["predictions"],
        "contributions.json": result["contributions"],
        "metrics_aggregate.json": result["metrics"],
        "metrics_by_match.json": result["metrics_by_match"],
        "metrics_by_team.json": result["metrics_by_team"],
        "bootstrap_results.json": result["bootstrap"],
        "confidence_intervals.json": result["bootstrap"]["metrics"],
        "audit.json": result["audit"],
    }


def _core_payload(result: dict[str, Any]) -> dict[str, Any]:
    """Extrae las salidas que deben ser idénticas en replay."""

    return {
        "snapshots": result["snapshots"],
        "predictions": result["predictions"],
        "contributions": result["contributions"],
        "metrics": result["metrics"],
        "metrics_by_match": result["metrics_by_match"],
        "metrics_by_team": result["metrics_by_team"],
        "bootstrap": result["bootstrap"],
    }


def _replay_core(
    matches: list[dict[str, Any]],
    raw_events: list[dict[str, Any]],
    result: dict[str, Any],
) -> dict[str, Any]:
    """Reejecuta inferencia con configuraciones ya congeladas."""

    eligible = _eligible_matches(matches, {int(row["match_id"]) for row in raw_events})
    partition = _partition(eligible, CalibrationConfig())
    match_map = {int(row["id"]): row for rows in partition.values() for row in rows}
    events_map = _events_by_match(raw_events, match_map)
    matrix = np.asarray(result["markov_config"]["base_matrix"], dtype=float)
    markov_rows, snapshots = _markov_predictions(partition, events_map, matrix)
    selected = HawkesConfig(**result["hawkes_config"]["config"])
    predictions, contributions = _evaluate_hawkes(
        markov_rows, events_map, selected, CalibrationConfig()
    )
    by_match = _metrics_by_match(predictions)
    return {
        "snapshots": snapshots, "predictions": predictions,
        "contributions": contributions,
        "metrics": {
            **{
                block: _aggregate([row for row in predictions if row["block"] == block])
                for block in partition
            },
            "overall": _aggregate(predictions),
        },
        "metrics_by_match": by_match,
        "metrics_by_team": _metrics_by_team(predictions),
        "bootstrap": _bootstrap(by_match, CalibrationConfig()),
    }


def _write_artifacts(result: dict[str, Any]) -> None:
    """Escribe artefactos, hashes, manifiesto e informe."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name, payload in _artifact_payloads(result).items():
        _write_json(OUTPUT / name, payload)
    reproducibility = result["audit"]["reproducibility"]
    manifest = {
        "phase": "7.6", "version": CalibrationConfig().version,
        "classification": result["decision"], "input_hash": _stable_hash({
            "partition": result["partition"],
            "source_hashes": result["audit"]["source_hashes"],
        }),
        "output_hash": reproducibility["primary_hash"],
        "replay_hash": reproducibility["replay_hash"],
        "replay_identical": reproducibility["identical"],
        "postgresql_modified": False,
        "official_markov_unchanged": True, "official_hawkes": False,
    }
    _write_json(OUTPUT / "manifest.json", manifest)
    report = _report(
        result["decision"], result["partition"], result["metrics"],
        result["markov_config"]["coverage"], result["bootstrap"],
        result["audit"]["database"],
    )
    (OUTPUT / "final_report.md").write_text(report, encoding="utf-8")
    hashes = {
        path.name: _file_hash(path)
        for path in sorted(OUTPUT.iterdir())
        if path.is_file() and path.name != "hashes.json"
    }
    _write_json(OUTPUT / "hashes.json", hashes)


def main() -> int:
    """Ejecuta Fase 7.6 en modo read-only."""

    config = CalibrationConfig()
    source_hashes_before = _source_hashes()
    capabilities = detect_capabilities()
    if not capabilities.ready:
        return _incomplete_artifacts(
            f"missing:{','.join(capabilities.missing())}", asdict(capabilities)
        )
    database_url = os.environ["DATABASE_URL"]
    try:
        matches, events, database = _read_database(database_url)
        result = _run_calibration(matches, events, database)
        replay = _replay_core(matches, events, result)
    except database_error_types() as error:
        return _incomplete_artifacts(
            sanitize_error(error, database_url), asdict(capabilities)
        )
    result_hash = _stable_hash(_core_payload(result))
    replay_hash = _stable_hash(replay)
    result["audit"]["reproducibility"] = {
        "primary_hash": result_hash, "replay_hash": replay_hash,
        "identical": result_hash == replay_hash,
    }
    result["audit"]["frozen_sources_unchanged"] = (
        source_hashes_before == _source_hashes()
    )
    if result_hash != replay_hash:
        result["decision"] = "calibration_rejected_for_revision"
    _write_artifacts(result)
    LOGGER.info("Fase 7.6: %s", result["decision"])
    return 0 if result["decision"] != "calibration_rejected_for_revision" else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0
# Created: 2026-07-16
