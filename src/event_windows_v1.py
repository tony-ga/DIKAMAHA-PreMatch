"""Materialización auditable de ventanas históricas de 15 minutos.

Requirements:
    - SQLAlchemy==2.0.41
    - psycopg2-binary==2.9.10

Version: 1.0.0
Created: 2026-07-26
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from src.postgres_readonly_staging import ReadonlyDatabase, counts_identical

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/phase_01_event_windows_v1"
EVENT_TYPES = frozenset({
    "goal", "shot_off_target", "shot_on_target", "shot_blocked", "corner",
    "foul", "yellow", "red", "substitution", "auxiliary",
})
SHOT_TYPES = frozenset({"shot_off_target", "shot_on_target", "shot_blocked"})


@dataclass(frozen=True, slots=True)
class EventWindowsConfig:
    """Configuración congelada para `event_windows v1`."""

    version: str = "event_windows_v1"
    competition_id: str = "esp.1"
    competition_name: str = "LaLiga"
    window_minutes: int = 15
    regular_window_count: int = 6


def _hash(value: Any) -> str:
    """Calcula SHA-256 estable sobre contenido serializable."""
    raw = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _write(name: str, value: Any) -> None:
    """Escribe un artefacto JSON de forma atómica."""
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / name
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temporary.replace(target)


def _window_index(minute: int, config: EventWindowsConfig) -> int:
    """Asigna minuto a una de seis ventanas, absorbiendo tiempo añadido."""
    return min(max(minute, 0) // config.window_minutes, config.regular_window_count - 1)


def _window_bounds(index: int, config: EventWindowsConfig) -> tuple[int, int | None]:
    """Devuelve límites inclusivo/exclusivo de una ventana."""
    start = index * config.window_minutes
    return start, None if index == config.regular_window_count - 1 else start + config.window_minutes


def _event_valid(event: dict[str, Any]) -> bool:
    """Declara válido un evento usable sin reinterpretar casos ambiguos."""
    return not bool(event["annulled"]) and event["team_id"] is not None and event["event_type"] in EVENT_TYPES


def _read_matches(session: Any) -> list[dict[str, Any]]:
    """Carga partidos finalizados con identidad y orientación completas."""
    return session.rows(
        "SELECT id AS match_id, home_team_id, away_team_id, match_date, season "
        "FROM matches WHERE home_score IS NOT NULL AND away_score IS NOT NULL "
        "ORDER BY match_date, id"
    )


def _read_events(session: Any) -> list[dict[str, Any]]:
    """Carga eventos timeline y el estado de anulación desde raw data."""
    return session.rows(
        "SELECT et.id AS event_id, et.match_id, et.minute, et.second, "
        "COALESCE(et.team_id, el.team_id) AS team_id, et.event_type, "
        "et.event_ledger_id, COALESCE((et.raw_data ->> 'annulled') IN ('true','1'), FALSE) AS annulled "
        "FROM events_timeline et LEFT JOIN events_ledger el ON el.id = et.event_ledger_id "
        "ORDER BY et.match_id, et.minute, et.second, et.id"
    )


def read_database(database_url: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Lee datos fuente exclusivamente mediante SELECT y audita inmutabilidad."""
    database = ReadonlyDatabase(database_url)
    with database.session() as session:
        before = {"matches": int(session.scalar("SELECT COUNT(*) FROM matches")), "events": int(session.scalar("SELECT COUNT(*) FROM events_timeline"))}
        matches, events = _read_matches(session), _read_events(session)
        after = {"matches": int(session.scalar("SELECT COUNT(*) FROM matches")), "events": int(session.scalar("SELECT COUNT(*) FROM events_timeline"))}
    return matches, events, {"select_only": all(item.startswith("SELECT ") for item in database.statements), "counts_identical": counts_identical(before, after), "before": before, "after": after}


def _events_by_match(events: Iterable[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    """Agrupa y ordena eventos por partido conservando el orden estable."""
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        grouped[int(event["match_id"])].append(event)
    return {key: sorted(rows, key=lambda row: (int(row["minute"]), int(row["second"]), int(row["event_id"]))) for key, rows in grouped.items()}


def _blank_window(match: dict[str, Any], team_id: int, rival_id: int, index: int, config: EventWindowsConfig) -> dict[str, Any]:
    """Inicializa una fila de ventana sin inferir eventos ausentes."""
    start, end = _window_bounds(index, config)
    return {"match_id": int(match["match_id"]), "match_date": str(match["match_date"]), "team_id": team_id, "opponent_team_id": rival_id, "is_home": team_id == int(match["home_team_id"]), "competition_id": config.competition_id, "competition_name": config.competition_name, "season": str(match["season"]), "window_index": index, "window_start_minute": start, "window_end_minute": end, "period": "second_half" if start >= 45 else "first_half", "score_for_start": 0, "score_against_start": 0, "goal_difference_start": 0, "goals": 0, "shots": 0, "shots_on_target": 0, "shots_blocked": 0, "corners": 0, "fouls": 0, "yellow_cards": 0, "red_cards": 0, "substitutions": 0, "unknown_event_count": 0, "annulled_event_count": 0, "null_team_event_count": 0, "event_count": 0}


def _increment(row: dict[str, Any], event_type: str) -> None:
    """Agrega un evento válido a las métricas de su equipo y ventana."""
    row["event_count"] += 1
    if event_type == "goal": row["goals"] += 1
    if event_type in SHOT_TYPES: row["shots"] += 1
    if event_type == "shot_on_target": row["shots_on_target"] += 1
    if event_type == "shot_blocked": row["shots_blocked"] += 1
    if event_type == "corner": row["corners"] += 1
    if event_type == "foul": row["fouls"] += 1
    if event_type == "yellow": row["yellow_cards"] += 1
    if event_type == "red": row["red_cards"] += 1
    if event_type == "substitution": row["substitutions"] += 1


def _audit_event(rows: dict[tuple[int, int], dict[str, Any]], event: dict[str, Any], index: int) -> None:
    """Registra casos no utilizables en ambas filas del partido sin asignarlos."""
    for team_id, rival_id in ((event["home_team_id"], event["away_team_id"]), (event["away_team_id"], event["home_team_id"])):
        row = rows[(int(team_id), index)]
        if event["annulled"]: row["annulled_event_count"] += 1
        elif event["team_id"] is None: row["null_team_event_count"] += 1
        elif event["event_type"] not in EVENT_TYPES: row["unknown_event_count"] += 1


def _finalize(rows: dict[tuple[int, int], dict[str, Any]], match_events: list[dict[str, Any]], config: EventWindowsConfig) -> list[dict[str, Any]]:
    """Completa métricas concedidas, presión y provenance de cada ventana."""
    source_hash = _hash(match_events)
    output = []
    for _, row in sorted(rows.items(), key=lambda item: (item[1]["window_index"], not item[1]["is_home"])):
        rival = rows[(int(row["opponent_team_id"]), int(row["window_index"]))]
        row["shots_conceded"] = rival["shots"]
        row["corners_conceded"] = rival["corners"]
        row["pressure"] = row["shots"] + row["corners"]
        row["pressure_conceded"] = rival["shots"] + rival["corners"]
        row["event_coverage"] = "observed_timeline"
        row["source_hash"] = source_hash
        row["window_version"] = config.version
        output.append(row)
    return output


def build_windows(matches: list[dict[str, Any]], events: list[dict[str, Any]], config: EventWindowsConfig) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Materializa ventanas completas y una auditoría causal por partido."""
    grouped, output, audit = _events_by_match(events), [], {"orphan_event_match_ids": [], "out_of_range_clocks": 0, "duplicate_event_ids": 0}
    match_ids = {int(row["match_id"]) for row in matches}
    audit["orphan_event_match_ids"] = sorted(set(grouped) - match_ids)
    for match in matches:
        output.extend(_build_match_windows(match, grouped.get(int(match["match_id"]), []), config, audit))
    audit["window_count"] = len(output)
    return output, audit


def _build_match_windows(match: dict[str, Any], events: list[dict[str, Any]], config: EventWindowsConfig, audit: dict[str, Any]) -> list[dict[str, Any]]:
    """Construye seis ventanas por equipo preservando marcador al inicio."""
    home, away = int(match["home_team_id"]), int(match["away_team_id"])
    rows = {(team, index): _blank_window(match, team, away if team == home else home, index, config) for team in (home, away) for index in range(config.regular_window_count)}
    scores = {home: 0, away: 0}
    for index in range(config.regular_window_count):
        _set_start_scores(rows, scores, home, away, index)
        for event in (row for row in events if _window_index(int(row["minute"]), config) == index):
            _process_event(rows, scores, event, home, away, index, audit)
    return _finalize(rows, events, config)


def _set_start_scores(rows: dict[tuple[int, int], dict[str, Any]], scores: dict[int, int], home: int, away: int, index: int) -> None:
    """Guarda el marcador observable antes de iniciar una ventana."""
    for team, rival in ((home, away), (away, home)):
        row = rows[(team, index)]
        row["score_for_start"], row["score_against_start"] = scores[team], scores[rival]
        row["goal_difference_start"] = scores[team] - scores[rival]


def _process_event(rows: dict[tuple[int, int], dict[str, Any]], scores: dict[int, int], event: dict[str, Any], home: int, away: int, index: int, audit: dict[str, Any]) -> None:
    """Actualiza métricas de ventana o auditoría sin alterar eventos ambiguos."""
    if int(event["minute"]) < 0 or int(event["second"]) < 0:
        audit["out_of_range_clocks"] += 1
        return
    event["home_team_id"], event["away_team_id"] = home, away
    if not _event_valid(event):
        _audit_event(rows, event, index)
        return
    team_id = int(event["team_id"])
    if team_id not in {home, away}:
        _audit_event(rows, {**event, "team_id": None}, index)
        return
    _increment(rows[(team_id, index)], str(event["event_type"]))
    if event["event_type"] == "goal": scores[team_id] += 1


def coverage(windows: list[dict[str, Any]]) -> dict[str, Any]:
    """Resume cobertura por evento, temporada, partido y ventana."""
    by_season: dict[str, Counter[str]] = defaultdict(Counter)
    for row in windows:
        by_season[str(row["season"])]["rows"] += 1
        by_season[str(row["season"])]["events"] += int(row["event_count"])
    return {"matches": len({int(row["match_id"]) for row in windows}), "windows": len(windows), "rows_per_match": 12, "by_season": {key: dict(value) for key, value in sorted(by_season.items())}}


def run(database_url: str, config: EventWindowsConfig | None = None) -> dict[str, Any]:
    """Ejecuta Fase 01, publica artefactos y devuelve su clasificación."""
    active = config or EventWindowsConfig()
    matches, events, database = read_database(database_url)
    windows, temporal = build_windows(matches, events, active)
    event_types = Counter(str(row["event_type"]) for row in events)
    valid = database["select_only"] and database["counts_identical"] and not temporal["orphan_event_match_ids"] and not temporal["out_of_range_clocks"]
    result = {"config": asdict(active), "matches": matches, "windows": windows, "coverage": coverage(windows), "temporal": temporal, "database": database, "event_types": dict(sorted(event_types.items())), "classification": "ready_for_next_phase" if valid else "rejected_for_revision"}
    _publish(result)
    LOGGER.info("Fase 01 event_windows: %s", result["classification"])
    return result


def _publish(result: dict[str, Any]) -> None:
    """Publica el contrato de artefactos de Fase 01 sin datos de conexión."""
    input_manifest = {"source": ["matches", "events_timeline", "events_ledger"], "completed_match_count": len(result["matches"]), "event_count": sum(result["event_types"].values()), "config_hash": _hash(result["config"])}
    audit = {"classification": result["classification"], "postgres_select_only": result["database"]["select_only"], "postgres_counts_identical": result["database"]["counts_identical"], **result["temporal"]}
    payloads = {"config.json": result["config"], "input_manifest.json": input_manifest, "event_windows.json": result["windows"], "coverage.json": {**result["coverage"], "event_types": result["event_types"]}, "audit.json": audit}
    for name, value in payloads.items(): _write(name, value)
    report = "\n".join(["# Fase 01 — event_windows v1", "", f"**Clasificación:** `{result['classification']}`", "", f"- partidos: `{result['coverage']['matches']}`", f"- ventanas: `{result['coverage']['windows']}`", f"- eventos fuente: `{sum(result['event_types'].values())}`", f"- PostgreSQL sólo lectura: `{audit['postgres_select_only']}`", f"- siguiente paso: `state_labeling v1`" if result["classification"] == "ready_for_next_phase" else "- siguiente paso: corregir auditoría temporal"]) 
    (OUTPUT / "final_report.md").write_text(report + "\n", encoding="utf-8")
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"}
    _write("hashes.json", hashes)


# Version: 1.0.0
# Created: 2026-07-26
