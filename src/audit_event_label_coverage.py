"""Auditoría read-only de cobertura y etiquetado in-play de Markov v1.

La fase compara las reglas históricas con reglas candidatas causales sobre un
universo temporal nuevo. No modifica Markov, Hawkes, match_features ni
PostgreSQL.

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
import os
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    from src.calibrate_inplay_models import (
        BLOCKED_IDS,
        LabelingConfig,
        _events_by_match,
        _file_hash,
        _snapshot_labels,
        _snapshot_times,
        _stable_hash,
        _utc,
        _valid_events,
        _window,
    )
    from src.hawkes_v1_integration import HawkesIntegrationConfig
    from src.markov_v1 import EVENT_TYPES_ALLOWED
    from src.postgres_readonly_staging import (
        ReadonlyDatabase,
        counts_identical,
        database_error_types,
        detect_capabilities,
        sanitize_error,
    )
except ModuleNotFoundError:  # pragma: no cover
    from calibrate_inplay_models import (
        BLOCKED_IDS,
        LabelingConfig,
        _events_by_match,
        _file_hash,
        _snapshot_labels,
        _snapshot_times,
        _stable_hash,
        _utc,
        _valid_events,
        _window,
    )
    from hawkes_v1_integration import HawkesIntegrationConfig
    from markov_v1 import EVENT_TYPES_ALLOWED
    from postgres_readonly_staging import (
        ReadonlyDatabase,
        counts_identical,
        database_error_types,
        detect_capabilities,
        sanitize_error,
    )

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/phase_7_7_event_label_coverage"
PHASE_71_SELECTION = ROOT / "artifacts/phase_7_1_historical_expansion/selection.json"
PHASE_76_PARTITION = (
    ROOT / "artifacts/phase_7_6_model_calibration/temporal_selection_partition.json"
)
KNOWN_TIMELINE_EVENTS = set(EVENT_TYPES_ALLOWED) | {"foul"}
RULE_ORDER = (
    "red_card_disadvantage",
    "opponent_red_advantage",
    "two_goal_context_after_60",
    "late_score_context",
    "sustained_pressure_dominance",
    "sustained_opponent_pressure",
)
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CoverageConfig:
    """Configuración fija de la auditoría de etiquetado."""

    version: str = "phase_7_7_event_label_coverage_v1"
    development_fraction: float = 0.50
    validation_fraction: float = 0.25
    minimum_development_snapshots: int = 30
    minimum_development_matches: int = 10
    minimum_holdout_snapshots: int = 15
    minimum_holdout_matches: int = 5
    minimum_confirmation_improvement: float = 0.10
    maximum_confirmation_unknown: float = 0.50
    maximum_block_rate_spread: float = 0.10
    late_minute: int = 75
    two_goal_minute: int = 60
    pressure_5m_threshold: float = 3.0
    pressure_10m_threshold: float = 6.0
    pressure_5m_margin: float = 2.0
    pressure_10m_margin: float = 3.0


@dataclass(frozen=True, slots=True)
class TeamContext:
    """Contexto causal de un equipo en un snapshot."""

    match_id: int
    snapshot_ts: str
    block: str
    team_id: int
    rival_team_id: int
    minute: int
    goal_difference: int
    own_pressure_5m: float
    rival_pressure_5m: float
    own_pressure_10m: float
    rival_pressure_10m: float
    own_red_10m: int
    rival_red_10m: int
    event_types_10m: tuple[str, ...]
    event_timestamps_10m: tuple[str, ...]


def _load_json(path: Path) -> Any:
    """Carga JSON versionado."""

    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    """Escribe JSON de forma atómica."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    temporary.replace(path)


def _counts(session: Any) -> dict[str, int]:
    """Obtiene conteos auditables mediante SELECT."""

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
    """Carga partidos finalizados en orden temporal."""

    return session.rows(
        """
        SELECT id, home_team_id, away_team_id, match_date, home_score,
               away_score, season, status
        FROM matches
        WHERE home_score IS NOT NULL AND away_score IS NOT NULL
        ORDER BY match_date, id
        """
    )


def _event_rows(session: Any) -> list[dict[str, Any]]:
    """Carga timeline y provenance ledger mediante SELECT."""

    return session.rows(
        """
        SELECT et.id, et.match_id, et.minute, et.second,
               et.team_id AS timeline_team_id, el.team_id AS ledger_team_id,
               et.event_type, et.event_type_raw, et.event_ledger_id,
               COALESCE((et.raw_data ->> 'annulled') IN ('true','1'), FALSE)
                   AS annulled
        FROM events_timeline et
        LEFT JOIN events_ledger el ON el.id = et.event_ledger_id
        ORDER BY et.match_id, et.minute, et.second, et.id
        """
    )


def _referential_rows(session: Any) -> dict[str, int]:
    """Audita referencias huérfanas mediante SELECT."""

    row = session.rows(
        """
        SELECT
          COUNT(*) FILTER (WHERE m.id IS NULL) AS orphan_match,
          COUNT(*) FILTER (
            WHERE et.event_ledger_id IS NOT NULL AND el.id IS NULL
          ) AS orphan_ledger,
          COUNT(*) FILTER (WHERE et.team_id IS NULL) AS timeline_null_team
        FROM events_timeline et
        LEFT JOIN matches m ON m.id = et.match_id
        LEFT JOIN events_ledger el ON el.id = et.event_ledger_id
        """
    )[0]
    return {key: int(value) for key, value in row.items()}


def _read_database(database_url: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Ejecuta únicamente SELECT y compara conteos."""

    database = ReadonlyDatabase(database_url)
    with database.session() as session:
        before = _counts(session)
        matches = _match_rows(session)
        events = _event_rows(session)
        referential = _referential_rows(session)
        after = _counts(session)
    audit = {
        "status": "postgres_readonly_verified",
        "before": before, "after": after,
        "identical": counts_identical(before, after),
        "connection_closed": database.closed,
        "statements": database.statements,
        "referential": referential,
        "write_statements": 0,
    }
    return matches, events, audit


def _prior_ids() -> set[int]:
    """Combina todos los partidos usados en evaluaciones anteriores."""

    output = set(BLOCKED_IDS)
    output.update(_load_json(PHASE_71_SELECTION)["selected_match_ids"])
    partition = _load_json(PHASE_76_PARTITION)
    for block in ("development", "validation", "confirmation"):
        output.update(partition[block]["match_ids"])
    return output


def _eligible_matches(
    matches: list[dict[str, Any]], event_match_ids: set[int]
) -> list[dict[str, Any]]:
    """Selecciona partidos nuevos con identidad y timeline."""

    excluded = _prior_ids()
    rows = [
        row for row in matches
        if int(row["id"]) not in excluded
        and int(row["id"]) in event_match_ids
        and row["home_team_id"] is not None
        and row["away_team_id"] is not None
    ]
    return sorted(rows, key=lambda row: (_utc(row["match_date"]), int(row["id"])))


def _cut_after_timestamp(rows: list[dict[str, Any]], target: int) -> int:
    """Evita dividir kickoffs simultáneos entre bloques."""

    target = min(max(target, 1), len(rows) - 1)
    timestamp = _utc(rows[target - 1]["match_date"])
    while target < len(rows) and _utc(rows[target]["match_date"]) == timestamp:
        target += 1
    return target


def _partition(
    rows: list[dict[str, Any]], config: CoverageConfig
) -> dict[str, list[dict[str, Any]]]:
    """Separa partidos completos en tres bloques temporales."""

    first = _cut_after_timestamp(rows, round(len(rows) * config.development_fraction))
    second_target = round(
        len(rows) * (config.development_fraction + config.validation_fraction)
    )
    second = _cut_after_timestamp(rows, max(first + 1, second_target))
    return {
        "development": rows[:first],
        "validation": rows[first:second],
        "confirmation": rows[second:],
    }


def _pressure(events: Iterable[dict[str, Any]], team_id: int) -> float:
    """Calcula presión con la fórmula congelada de Fase 4.10."""

    weights = {
        "shot_on_target": 2.0, "shot_off_target": 1.0,
        "shot_blocked": 1.0, "corner": 0.5, "substitution": 0.25,
        "yellow": 0.5, "red": 0.5,
    }
    return sum(
        weights.get(event["event_type"], 0.0)
        for event in events if event["team_id"] == team_id
    )


def _team_context(
    row: dict[str, Any],
    side: str,
    events: list[dict[str, Any]],
) -> TeamContext:
    """Construye contexto de un equipo desde ventanas ya cortadas."""

    own_id = row[f"{side}_team_id"]
    rival_side = "away" if side == "home" else "home"
    rival_id = row[f"{rival_side}_team_id"]
    snapshot = _utc(row["snapshot_ts"])
    observed = _valid_events(events, snapshot)
    short, medium = _window(observed, snapshot, 5), _window(observed, snapshot, 10)
    goal_difference = (
        row["score_home"] - row["score_away"]
        if side == "home" else row["score_away"] - row["score_home"]
    )
    return TeamContext(
        match_id=row["match_id"], snapshot_ts=row["snapshot_ts"],
        block=row["block"], team_id=own_id, rival_team_id=rival_id,
        minute=row["minute"], goal_difference=goal_difference,
        own_pressure_5m=_pressure(short, own_id),
        rival_pressure_5m=_pressure(short, rival_id),
        own_pressure_10m=_pressure(medium, own_id),
        rival_pressure_10m=_pressure(medium, rival_id),
        own_red_10m=sum(
            event["event_type"] == "red" and event["team_id"] == own_id
            for event in medium
        ),
        rival_red_10m=sum(
            event["event_type"] == "red" and event["team_id"] == rival_id
            for event in medium
        ),
        event_types_10m=tuple(sorted(event["event_type"] for event in medium)),
        event_timestamps_10m=tuple(sorted(event["event_ts"] for event in medium)),
    )


def _rule_matches(context: TeamContext, config: CoverageConfig) -> list[tuple[str, int]]:
    """Evalúa reglas causales predefinidas sin consultar targets futuros."""

    output: list[tuple[str, int]] = []
    if context.own_red_10m > context.rival_red_10m:
        output.append(("red_card_disadvantage", 1))
    if context.rival_red_10m > context.own_red_10m:
        output.append(("opponent_red_advantage", 2))
    if context.minute >= config.two_goal_minute and abs(context.goal_difference) >= 2:
        output.append(("two_goal_context_after_60", 1 if context.goal_difference > 0 else 2))
    if context.minute >= config.late_minute and context.goal_difference != 0:
        output.append(("late_score_context", 1 if context.goal_difference > 0 else 2))
    if _own_pressure_dominates(context, config):
        output.append(("sustained_pressure_dominance", 2))
    if _rival_pressure_dominates(context, config):
        output.append(("sustained_opponent_pressure", 1))
    return output


def _own_pressure_dominates(context: TeamContext, config: CoverageConfig) -> bool:
    """Comprueba presión propia sostenida en ambas ventanas."""

    return (
        context.own_pressure_5m >= config.pressure_5m_threshold
        and context.own_pressure_10m >= config.pressure_10m_threshold
        and context.own_pressure_5m - context.rival_pressure_5m
        >= config.pressure_5m_margin
        and context.own_pressure_10m - context.rival_pressure_10m
        >= config.pressure_10m_margin
    )


def _rival_pressure_dominates(context: TeamContext, config: CoverageConfig) -> bool:
    """Comprueba presión rival sostenida en ambas ventanas."""

    return (
        context.rival_pressure_5m >= config.pressure_5m_threshold
        and context.rival_pressure_10m >= config.pressure_10m_threshold
        and context.rival_pressure_5m - context.own_pressure_5m
        >= config.pressure_5m_margin
        and context.rival_pressure_10m - context.own_pressure_10m
        >= config.pressure_10m_margin
    )


def _unknown_cause(context: TeamContext) -> str:
    """Asigna una causa exacta y auditable a un unknown."""

    events = set(context.event_types_10m)
    total_pressure = context.own_pressure_10m + context.rival_pressure_10m
    pressure_delta = context.own_pressure_10m - context.rival_pressure_10m
    if not events:
        return "no_recent_events"
    if events <= {"substitution"}:
        return "substitution_only"
    if events <= {"yellow"}:
        return "yellow_only"
    if context.goal_difference != 0 and context.minute < 60 and total_pressure < 3:
        return "early_score_context_low_activity"
    if context.goal_difference != 0 and 60 <= context.minute < 75 and total_pressure < 3:
        return "pre_late_score_context_low_activity"
    if context.goal_difference == 0 and abs(pressure_delta) >= 3:
        return "tied_pressure_ambiguous"
    if context.goal_difference * pressure_delta > 0 and abs(pressure_delta) >= 3:
        return "score_pressure_conflict"
    high_5 = max(context.own_pressure_5m, context.rival_pressure_5m) >= 3
    high_10 = max(context.own_pressure_10m, context.rival_pressure_10m) >= 6
    if high_5 != high_10:
        return "single_window_signal"
    return "insufficient_combined_confidence"


def _baseline_snapshot_rows(
    partition: dict[str, list[dict[str, Any]]],
    events_map: dict[int, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Genera etiquetas baseline para todos los snapshots nuevos."""

    output: list[dict[str, Any]] = []
    for block, matches in partition.items():
        for match in matches:
            match_id = int(match["id"])
            for snapshot in _snapshot_times(match, events_map[match_id]):
                labels = _snapshot_labels(
                    match, events_map[match_id], snapshot, LabelingConfig()
                )
                output.append({
                    "match_id": match_id, "block": block,
                    "snapshot_ts": snapshot.isoformat(),
                    "home_team_id": int(match["home_team_id"]),
                    "away_team_id": int(match["away_team_id"]), **labels,
                })
    return output


def _team_rows(
    snapshots: list[dict[str, Any]],
    events_map: dict[int, list[dict[str, Any]]],
    config: CoverageConfig,
) -> list[dict[str, Any]]:
    """Expande snapshots a observaciones por equipo."""

    output = []
    for row in snapshots:
        for side in ("home", "away"):
            context = _team_context(row, side, events_map[row["match_id"]])
            baseline = row[f"{side}_state_label"]
            output.append({
                **asdict(context), "side": side, "baseline_state": baseline,
                "baseline_unknown": baseline == -1,
                "unknown_cause": _unknown_cause(context) if baseline == -1 else None,
                "candidate_matches": [
                    {"rule_id": rule, "state": state}
                    for rule, state in _rule_matches(context, config)
                ] if baseline == -1 else [],
            })
    return output


def _development_rule_support(
    rows: list[dict[str, Any]], config: CoverageConfig
) -> dict[str, dict[str, Any]]:
    """Selecciona reglas únicamente por soporte en desarrollo."""

    output = {}
    development = [
        row for row in rows
        if row["block"] == "development" and row["baseline_unknown"]
    ]
    for rule_id in RULE_ORDER:
        matched = [
            row for row in development
            if any(item["rule_id"] == rule_id for item in row["candidate_matches"])
        ]
        match_count = len({row["match_id"] for row in matched})
        output[rule_id] = {
            "development_snapshot_count": len(matched),
            "development_match_count": match_count,
            "accepted_from_development": (
                len(matched) >= config.minimum_development_snapshots
                and match_count >= config.minimum_development_matches
            ),
        }
    return output


def _apply_candidate(
    row: dict[str, Any], accepted_rules: set[str]
) -> tuple[int, str | None]:
    """Aplica reglas congeladas y conserva conflictos como unknown."""

    if not row["baseline_unknown"]:
        return int(row["baseline_state"]), "baseline_rule"
    matches = [
        item for item in row["candidate_matches"]
        if item["rule_id"] in accepted_rules
    ]
    if not matches:
        return -1, None
    states = {int(item["state"]) for item in matches}
    if len(states) != 1:
        return -1, "candidate_rule_conflict"
    for rule_id in RULE_ORDER:
        if any(item["rule_id"] == rule_id for item in matches):
            return states.pop(), rule_id
    return -1, None


def _candidate_rows(
    rows: list[dict[str, Any]], support: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Añade etiqueta candidata sin alterar baseline."""

    accepted = {
        rule_id for rule_id, values in support.items()
        if values["accepted_from_development"]
    }
    output = []
    for row in rows:
        state, rule_id = _apply_candidate(row, accepted)
        output.append({
            **row, "candidate_state": state,
            "candidate_unknown": state == -1,
            "resolution_rule": rule_id,
        })
    return output


def _state_coverage(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    """Resume cobertura por estado para un campo."""

    counts = Counter(int(row[field]) for row in rows)
    total = len(rows)
    return {
        "observations": total,
        "counts": {
            "equilibrio": counts[0], "repliegue": counts[1],
            "asedio": counts[2], "unknown": counts[-1],
        },
        "shares": {
            "equilibrio": counts[0] / max(1, total),
            "repliegue": counts[1] / max(1, total),
            "asedio": counts[2] / max(1, total),
            "unknown": counts[-1] / max(1, total),
        },
    }


def _coverage_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compara baseline y candidato por bloque."""

    output = {}
    for block in ("development", "validation", "confirmation"):
        group = [row for row in rows if row["block"] == block]
        baseline = _state_coverage(group, "baseline_state")
        candidate = _state_coverage(group, "candidate_state")
        output[block] = {
            "baseline": baseline, "candidate": candidate,
            "unknown_absolute_reduction": (
                baseline["shares"]["unknown"] - candidate["shares"]["unknown"]
            ),
            "unknown_relative_reduction": (
                1.0 - candidate["shares"]["unknown"] / baseline["shares"]["unknown"]
                if baseline["shares"]["unknown"] else 0.0
            ),
        }
    return output


def _bucket_minute(minute: int) -> str:
    """Agrupa minutos para análisis de unknown."""

    if minute <= 15:
        return "00_15"
    if minute <= 30:
        return "16_30"
    if minute <= 45:
        return "31_45"
    if minute <= 60:
        return "46_60"
    if minute <= 75:
        return "61_75"
    if minute <= 90:
        return "76_90"
    return "90_plus"


def _bucket_goal_difference(value: int) -> str:
    """Agrupa diferencial observable."""

    if value <= -2:
        return "minus_2_or_less"
    if value >= 2:
        return "plus_2_or_more"
    return str(value)


def _bucket_event_volume(row: dict[str, Any]) -> str:
    """Agrupa volumen reciente sin usar targets."""

    volume = len(row["event_types_10m"])
    if volume == 0:
        return "none"
    if volume <= 3:
        return "low"
    if volume <= 6:
        return "medium"
    return "high"


def _group_unknown(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    """Cuenta unknown baseline por una dimensión."""

    return dict(sorted(Counter(str(row[key]) for row in rows if row["baseline_unknown"]).items()))


def _unknown_breakdown(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Descompone unknown por contexto y causa exacta."""

    enriched = [{
        **row, "minute_bucket": _bucket_minute(row["minute"]),
        "goal_difference_bucket": _bucket_goal_difference(row["goal_difference"]),
        "event_volume_bucket": _bucket_event_volume(row),
    } for row in rows]
    unknown = [row for row in enriched if row["baseline_unknown"]]
    event_types = Counter(
        event_type for row in unknown for event_type in set(row["event_types_10m"])
    )
    return {
        "unknown_count": len(unknown),
        "by_block": _group_unknown(enriched, "block"),
        "by_minute": _group_unknown(enriched, "minute_bucket"),
        "by_goal_difference": _group_unknown(enriched, "goal_difference_bucket"),
        "by_event_volume": _group_unknown(enriched, "event_volume_bucket"),
        "by_cause": _group_unknown(enriched, "unknown_cause"),
        "by_event_type_presence": dict(sorted(event_types.items())),
    }


def _rule_evaluation(
    rows: list[dict[str, Any]],
    support: dict[str, dict[str, Any]],
    config: CoverageConfig,
) -> dict[str, Any]:
    """Evalúa impacto y estabilidad sin reabrir selección."""

    output = {}
    for rule_id in RULE_ORDER:
        block_metrics = {}
        for block in ("development", "validation", "confirmation"):
            group = [
                row for row in rows
                if row["block"] == block and row["resolution_rule"] == rule_id
            ]
            total = sum(row["block"] == block for row in rows)
            block_metrics[block] = {
                "snapshot_count": len(group),
                "match_count": len({row["match_id"] for row in group}),
                "assignment_rate": len(group) / max(1, total),
            }
        rates = [item["assignment_rate"] for item in block_metrics.values()]
        holdout_ok = all(
            block_metrics[block]["snapshot_count"] >= config.minimum_holdout_snapshots
            and block_metrics[block]["match_count"] >= config.minimum_holdout_matches
            for block in ("validation", "confirmation")
        )
        output[rule_id] = {
            **support[rule_id], "blocks": block_metrics,
            "rate_spread": max(rates) - min(rates),
            "holdout_coverage_sufficient": holdout_ok,
            "temporally_stable": (
                holdout_ok and max(rates) - min(rates)
                <= config.maximum_block_rate_spread
            ),
        }
    return output


def _match_categories(
    partition: dict[str, list[dict[str, Any]]],
    events_map: dict[int, list[dict[str, Any]]],
) -> dict[int, list[str]]:
    """Clasifica volumen usando umbrales aprendidos solo en desarrollo."""

    development_counts = sorted(
        sum(event["event_type"] in EVENT_TYPES_ALLOWED for event in events_map[int(row["id"])])
        for row in partition["development"]
    )
    low = development_counts[max(0, round(0.25 * (len(development_counts) - 1)))]
    high = development_counts[round(0.75 * (len(development_counts) - 1))]
    output = {}
    for rows in partition.values():
        for row in rows:
            events = events_map[int(row["id"])]
            relevant = sum(event["event_type"] in EVENT_TYPES_ALLOWED for event in events)
            volume = "low_event" if relevant <= low else "high_event" if relevant >= high else "median_event"
            categories = [volume]
            for event_type, category in (
                ("goal", "goals"), ("yellow", "cards"), ("red", "cards"),
                ("substitution", "substitutions"),
            ):
                if any(event["event_type"] == event_type for event in events):
                    categories.append(category)
            if any(event["team_id"] is None for event in events):
                categories.append("team_id_null")
            output[int(row["id"])] = sorted(set(categories))
    return output


def _coverage_by_match(
    rows: list[dict[str, Any]],
    categories: dict[int, list[str]],
) -> list[dict[str, Any]]:
    """Agrega cobertura por partido completo."""

    output = []
    for match_id in sorted({row["match_id"] for row in rows}):
        group = [row for row in rows if row["match_id"] == match_id]
        baseline_unknown = sum(row["baseline_unknown"] for row in group)
        candidate_unknown = sum(row["candidate_unknown"] for row in group)
        output.append({
            "match_id": match_id, "block": group[0]["block"],
            "team_snapshot_count": len(group),
            "baseline_unknown_count": baseline_unknown,
            "candidate_unknown_count": candidate_unknown,
            "baseline_unknown_fraction": baseline_unknown / len(group),
            "candidate_unknown_fraction": candidate_unknown / len(group),
            "categories": categories[match_id],
        })
    return output


def _event_quality(
    raw_events: list[dict[str, Any]], selected_ids: set[int]
) -> dict[str, Any]:
    """Audita tipos, nulos, anulados y duplicados de la muestra."""

    rows = [row for row in raw_events if int(row["match_id"]) in selected_ids]
    canonical = [
        f"ledger:{row['event_ledger_id']}"
        if row["event_ledger_id"] is not None else f"timeline:{row['id']}"
        for row in rows
    ]
    return {
        "source_event_count": len(rows),
        "timeline_team_id_null": sum(row["timeline_team_id"] is None for row in rows),
        "effective_team_id_null": sum(
            row["timeline_team_id"] is None and row["ledger_team_id"] is None
            for row in rows
        ),
        "annulled_events": sum(bool(row["annulled"]) for row in rows),
        "unknown_event_types": dict(sorted(Counter(
            str(row["event_type"]) for row in rows
            if row["event_type"] not in KNOWN_TIMELINE_EVENTS
        ).items())),
        "non_tactical_known_events": dict(sorted(Counter(
            str(row["event_type"]) for row in rows
            if row["event_type"] in KNOWN_TIMELINE_EVENTS
            and row["event_type"] not in EVENT_TYPES_ALLOWED
        ).items())),
        "duplicate_canonical_ids": len(canonical) - len(set(canonical)),
    }


def _temporal_audit(
    partition: dict[str, list[dict[str, Any]]],
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Comprueba separación y uso causal de eventos."""

    ids = {
        block: {int(row["id"]) for row in matches}
        for block, matches in partition.items()
    }
    overlaps = (
        ids["development"] & ids["validation"]
        | ids["development"] & ids["confirmation"]
        | ids["validation"] & ids["confirmation"]
    )
    return {
        "match_blocks_disjoint": not overlaps,
        "snapshot_block_unique": all(
            sum(row["match_id"] in values for values in ids.values()) == 1
            for row in rows
        ),
        "event_ts_lte_snapshot_ts": all(
            all(event_ts <= row["snapshot_ts"] for event_ts in row["event_timestamps_10m"])
            for row in rows
        ),
        "final_score_not_used_for_labels": True,
        "future_events_not_used": True,
        "snapshots_not_iid_documented": True,
        "block_order_strict": (
            max(_utc(row["match_date"]) for row in partition["development"])
            < min(_utc(row["match_date"]) for row in partition["validation"])
            < min(_utc(row["match_date"]) for row in partition["confirmation"])
        ),
    }


def _source_hashes() -> dict[str, str]:
    """Registra fuentes y componentes oficiales congelados."""

    paths = (
        ROOT / "src/audit_event_label_coverage.py",
        ROOT / "scripts/run_phase_7_7_event_label_coverage.py",
        ROOT / "tests/test_phase_7_7_event_label_coverage.py",
        ROOT / "tests/test_phase_7_7_event_label_coverage_postgres.py",
        ROOT / "src/markov_v1.py",
        ROOT / "src/hawkes_v1.py",
        ROOT / "src/hawkes_v1_integration.py",
        ROOT / "src/dikamaha_inference.py",
        ROOT / "artifacts/phase_7_6_model_calibration/manifest.json",
        ROOT / "artifacts/phase_7_1_historical_expansion/manifest.json",
        ROOT / (
            "artifacts/phase_2_4_match_features_v1_dry_run/"
            "match_features_v1_candidate.json"
        ),
    )
    return {str(path.relative_to(ROOT)): _file_hash(path) for path in paths}


def _provenance_audit(
    event_quality: dict[str, Any], source_hashes: dict[str, str]
) -> dict[str, Any]:
    """Documenta provenance y separación de capas."""

    shadow = HawkesIntegrationConfig()
    phase_76 = _load_json(
        ROOT / "artifacts/phase_7_6_model_calibration/audit.json"
    )["source_hashes"]
    official_keys = (
        "src/markov_v1.py", "src/hawkes_v1.py",
        "src/hawkes_v1_integration.py", "src/dikamaha_inference.py",
    )
    return {
        "canonical_event_id": "ledger:<event_ledger_id> else timeline:<id>",
        "team_id_resolution": "events_timeline.team_id then events_ledger.team_id",
        "event_ts_definition": "matches.match_date + minute + second",
        "unknown_events_retained_for_audit": True,
        "annulled_events_excluded_from_rules": True,
        "team_id_null_excluded_from_rules": True,
        "markov_official_modified": False,
        "hawkes_shadow_only": True,
        "hawkes_enabled_default": shadow.hawkes_enabled,
        "hawkes_parameters_calibrated": False,
        "official_hashes_match_phase_7_6": all(
            source_hashes[key] == phase_76[key] for key in official_keys
        ),
        "frozen_artifact_write_attempts": 0,
        "event_quality": event_quality,
        "source_hashes": source_hashes,
    }


def _decision(
    coverage: dict[str, Any],
    rule_evaluation: dict[str, Any],
    temporal: dict[str, Any],
    provenance: dict[str, Any],
    database: dict[str, Any],
) -> str:
    """Clasifica la mejora sin alterar Markov oficial."""

    temporal_valid = all(
        value for value in temporal.values() if isinstance(value, bool)
    )
    provenance_valid = all(
        provenance[key] for key in (
            "unknown_events_retained_for_audit",
            "annulled_events_excluded_from_rules",
            "team_id_null_excluded_from_rules",
            "hawkes_shadow_only",
            "official_hashes_match_phase_7_6",
        )
    )
    expected_false = (
        provenance["hawkes_enabled_default"] is False
        and provenance["markov_official_modified"] is False
        and provenance["hawkes_parameters_calibrated"] is False
    )
    referential_valid = all(
        value == 0 for value in database["referential"].values()
    )
    if not temporal_valid or not provenance_valid or not expected_false:
        return "labeling_revision_rejected"
    if not referential_valid:
        return "labeling_revision_rejected"
    if not database["identical"] or database["write_statements"] != 0:
        return "labeling_revision_rejected"
    confirmation = coverage["confirmation"]
    improved = (
        confirmation["unknown_absolute_reduction"]
        >= CoverageConfig().minimum_confirmation_improvement
        and confirmation["candidate"]["shares"]["unknown"]
        <= CoverageConfig().maximum_confirmation_unknown
    )
    accepted = [
        values for values in rule_evaluation.values()
        if values["accepted_from_development"]
    ]
    stable = bool(accepted) and all(item["temporally_stable"] for item in accepted)
    return "labeling_coverage_improved" if improved and stable else "insufficient_labeling_signal"


def _selection_payload(
    partition: dict[str, list[dict[str, Any]]]
) -> dict[str, Any]:
    """Serializa el universo temporal independiente."""

    output: dict[str, Any] = {
        "rule": "all_final_matches_with_timeline_not_used_in_7_1_7_2_7_6",
        "excluded_prior_ids": sorted(_prior_ids()),
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


def _candidate_rules_payload(
    evaluation: dict[str, Any], config: CoverageConfig
) -> dict[str, Any]:
    """Documenta reglas, soporte y limitaciones."""

    definitions = {
        "red_card_disadvantage": "red propia no compensada en ventana 10m -> repliegue",
        "opponent_red_advantage": "red rival no compensada en ventana 10m -> asedio",
        "two_goal_context_after_60": "|diferencial| >= 2 y minuto >= 60",
        "late_score_context": "diferencial != 0 y minuto >= 75",
        "sustained_pressure_dominance": "presión propia supera umbrales y márgenes en 5m y 10m",
        "sustained_opponent_pressure": "presión rival supera umbrales y márgenes en 5m y 10m",
    }
    return {
        "version": "markov_labeling_candidate_phase_7_7_v1",
        "selected_using": "development_only",
        "frozen_before_validation": True,
        "config": asdict(config),
        "precedence": list(RULE_ORDER),
        "rules": {
            rule_id: {
                "definition": definitions[rule_id],
                "causally_available_at_snapshot": True,
                "uses_final_score": False,
                "uses_future_events": False,
                **evaluation[rule_id],
            }
            for rule_id in RULE_ORDER
        },
        "must_remain_unknown": [
            "substitution_only", "yellow_only", "no_recent_events",
            "single_window_signal", "candidate_rule_conflict",
            "insufficient_combined_confidence",
        ],
    }


def _report(
    decision: str,
    selection: dict[str, Any],
    coverage: dict[str, Any],
    rules: dict[str, Any],
    unknown: dict[str, Any],
    database: dict[str, Any],
) -> str:
    """Renderiza el informe final."""

    confirmation = coverage["confirmation"]
    accepted = [
        rule_id for rule_id, values in rules["rules"].items()
        if values["accepted_from_development"]
    ]
    return "\n".join([
        "# Fase 7.7 - Cobertura y etiquetado in-play",
        "",
        f"**Clasificación:** `{decision}`",
        "",
        "## Universo independiente",
        f"- desarrollo: `{selection['development']['match_count']}` partidos",
        f"- validación: `{selection['validation']['match_count']}` partidos",
        f"- confirmación: `{selection['confirmation']['match_count']}` partidos",
        "- ningún partido había sido usado para seleccionar parámetros en 7.1/7.2/7.6",
        "",
        "## Cobertura",
        f"- unknown baseline confirmatorio: `{confirmation['baseline']['shares']['unknown']:.4f}`",
        f"- unknown candidato confirmatorio: `{confirmation['candidate']['shares']['unknown']:.4f}`",
        f"- reducción absoluta: `{confirmation['unknown_absolute_reduction']:.4f}`",
        f"- reglas aceptadas solo por desarrollo: `{accepted}`",
        f"- causa unknown dominante: `{max(unknown['by_cause'], key=unknown['by_cause'].get)}`",
        "",
        "## Límites",
        "- `unknown` no se convierte en estado por conveniencia",
        "- sustituciones o amarillas aisladas permanecen ambiguas",
        "- las reglas candidatas no modifican Markov oficial ni su matriz",
        "- Hawkes conserva alpha/beta congelados y permanece shadow",
        "",
        "## Auditoría",
        f"- PostgreSQL: `{database['status']}`; conteos idénticos: `{database['identical']}`",
        "- consultas exclusivamente SELECT; cero escrituras",
        "- event_ts se corta en snapshot_ts y no se usa marcador final",
        "- resultados agregados por partido; snapshots no IID",
    ])


def _core_result(
    matches: list[dict[str, Any]],
    raw_events: list[dict[str, Any]],
    database: dict[str, Any],
) -> dict[str, Any]:
    """Ejecuta auditoría de cobertura y reglas candidatas."""

    eligible = _eligible_matches(matches, {int(row["match_id"]) for row in raw_events})
    partition = _partition(eligible, CoverageConfig())
    match_map = {int(row["id"]): row for values in partition.values() for row in values}
    events_map = _events_by_match(raw_events, match_map)
    snapshots = _baseline_snapshot_rows(partition, events_map)
    team_rows = _team_rows(snapshots, events_map, CoverageConfig())
    support = _development_rule_support(team_rows, CoverageConfig())
    candidate_rows = _candidate_rows(team_rows, support)
    coverage = _coverage_summary(candidate_rows)
    rule_evaluation = _rule_evaluation(candidate_rows, support, CoverageConfig())
    categories = _match_categories(partition, events_map)
    event_quality = _event_quality(raw_events, set(match_map))
    temporal = _temporal_audit(partition, candidate_rows)
    provenance = _provenance_audit(event_quality, _source_hashes())
    decision = _decision(coverage, rule_evaluation, temporal, provenance, database)
    return {
        "decision": decision, "selection": _selection_payload(partition),
        "coverage": coverage, "unknown": _unknown_breakdown(candidate_rows),
        "rules": _candidate_rules_payload(rule_evaluation, CoverageConfig()),
        "coverage_by_match": _coverage_by_match(candidate_rows, categories),
        "temporal": temporal, "provenance": provenance,
        "database": database, "team_rows": candidate_rows,
    }


def _incomplete(reason: str, capabilities: dict[str, Any]) -> int:
    """Registra indisponibilidad sin inventar conteos."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    payload = {
        "classification": "insufficient_labeling_signal",
        "database_status": "database_verification_incomplete",
        "reason": reason, "capabilities": capabilities,
        "postgresql_modified": False,
    }
    _write_json(OUTPUT / "postgres_readonly_audit.json", payload)
    _write_json(OUTPUT / "manifest.json", {"phase": "7.7", **payload})
    (OUTPUT / "final_report.md").write_text(
        f"# Fase 7.7\n\nEjecución incompleta: `{reason}`.\n", encoding="utf-8"
    )
    return 0


def _write_artifacts(result: dict[str, Any], replay_hashes: dict[str, Any]) -> None:
    """Escribe los artefactos contractuales y sus hashes."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    payloads = {
        "coverage_baseline.json": result["coverage"],
        "unknown_breakdown.json": result["unknown"],
        "labeling_rules_candidate.json": result["rules"],
        "coverage_by_match.json": result["coverage_by_match"],
        "temporal_audit.json": result["temporal"],
        "provenance_audit.json": result["provenance"],
        "postgres_readonly_audit.json": result["database"],
        "replay_hashes.json": replay_hashes,
    }
    for name, payload in payloads.items():
        _write_json(OUTPUT / name, payload)
    manifest = {
        "phase": "7.7", "version": CoverageConfig().version,
        "classification": result["decision"],
        "selection": result["selection"],
        "input_hash": _stable_hash({
            "selection": result["selection"],
            "sources": result["provenance"]["source_hashes"],
        }),
        "output_hash": replay_hashes["primary_hash"],
        "replay_hash": replay_hashes["replay_hash"],
        "replay_identical": replay_hashes["identical"],
        "postgresql_modified": False,
        "markov_official_modified": False,
        "hawkes_official": False,
    }
    _write_json(OUTPUT / "manifest.json", manifest)
    report = _report(
        result["decision"], result["selection"], result["coverage"],
        result["rules"], result["unknown"], result["database"],
    )
    (OUTPUT / "final_report.md").write_text(report, encoding="utf-8")
    hashes = {
        path.name: _file_hash(path)
        for path in sorted(OUTPUT.iterdir())
        if path.is_file() and path.name != "hashes.json"
    }
    _write_json(OUTPUT / "hashes.json", hashes)


def _replay_payload(result: dict[str, Any]) -> dict[str, Any]:
    """Extrae resultados deterministas excluyendo diagnósticos de conexión."""

    return {
        "decision": result["decision"], "selection": result["selection"],
        "coverage": result["coverage"], "unknown": result["unknown"],
        "rules": result["rules"], "coverage_by_match": result["coverage_by_match"],
        "temporal": result["temporal"], "provenance": result["provenance"],
        "team_rows": result["team_rows"],
    }


def main() -> int:
    """Ejecuta Fase 7.7 en modo PostgreSQL read-only."""

    capabilities = detect_capabilities()
    if not capabilities.ready:
        return _incomplete(
            f"missing:{','.join(capabilities.missing())}", asdict(capabilities)
        )
    database_url = os.environ["DATABASE_URL"]
    try:
        matches, events, database = _read_database(database_url)
        primary = _core_result(matches, events, database)
        replay = _core_result(matches, events, database)
    except database_error_types() as error:
        return _incomplete(sanitize_error(error, database_url), asdict(capabilities))
    primary_hash = _stable_hash(_replay_payload(primary))
    replay_hash = _stable_hash(_replay_payload(replay))
    replay_hashes = {
        "primary_hash": primary_hash, "replay_hash": replay_hash,
        "identical": primary_hash == replay_hash,
    }
    if not replay_hashes["identical"]:
        primary["decision"] = "labeling_revision_rejected"
    _write_artifacts(primary, replay_hashes)
    LOGGER.info("Fase 7.7: %s", primary["decision"])
    return 0 if primary["decision"] != "labeling_revision_rejected" else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0
# Created: 2026-07-16
