"""Materializa ventanas de 15 minutos para el corpus multi-liga.

La fuente es ``prospective_staging_v2`` en modo SELECT-only. Los partidos sin
marcador completo se publican como rechazados y no entran en ventanas ni en
el entrenamiento. ``event_type_raw`` se conserva para recuperar eventos
como faltas que llegaron con una etiqueta normalizada genérica.

Requirements:
    SQLAlchemy==2.0.41
    psycopg2-binary==2.9.10

Version: 1.0.0
Created: 2026-07-26
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.event_windows_v1 import EventWindowsConfig, build_windows
from src.espn_event_reconciliation import reconcile_staging_events
from src.espn_event_taxonomy import classify_event_type

LOGGER = logging.getLogger(__name__)
OUTPUT = ROOT / "artifacts/phase_38_multileague_event_windows_v1"
SCHEMA = "prospective_staging_v2"
VALID_EVENT_TYPES = {"goal", "shot_off_target", "shot_on_target", "shot_blocked", "corner", "foul", "yellow", "red", "substitution", "auxiliary"}


def _database_url() -> str:
    """Obtiene DATABASE_URL sin registrar su contenido."""

    value = os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("missing_database_url")
    return value


def _season(kickoff: str) -> str:
    """Deriva temporada futbolística a partir del kickoff UTC."""

    year, month = int(kickoff[:4]), int(kickoff[5:7])
    return f"{year}-{str(year + 1)[-2:]}" if month >= 8 else f"{year - 1}-{str(year)[-2:]}"


def _event_type(row: dict[str, Any]) -> str:
    """Recupera una etiqueta útil usando raw cuando la normalización fue genérica."""

    normalized = str(row.get("event_type") or "unclassified")
    raw = str(row.get("event_type_raw") or "")
    candidate = classify_event_type(raw or normalized, normalized == "goal")
    return candidate if candidate in VALID_EVENT_TYPES else "unclassified"


def _matches(connection: Any) -> list[dict[str, Any]]:
    """Lee partidos completos con identidad multi-liga."""

    query = text(f"SELECT provider_match_id, league_slug, competition_id, kickoff_ts, home_provider_team_id, away_provider_team_id, home_score, away_score FROM {SCHEMA}.matches WHERE provider='espn' AND complete IS TRUE AND home_score IS NOT NULL AND away_score IS NOT NULL ORDER BY kickoff_ts, provider_match_id")
    return [dict(row) for row in connection.execute(query).mappings().all()]


def _incomplete(connection: Any) -> list[dict[str, Any]]:
    """Audita partidos post sin marcador completo."""

    query = text(f"SELECT m.provider_match_id, m.league_slug, m.competition_id, m.kickoff_ts, m.provider_status, m.home_score, m.away_score, COUNT(e.id) AS event_count FROM {SCHEMA}.matches m LEFT JOIN {SCHEMA}.events e ON e.provider='espn' AND e.provider_match_id=m.provider_match_id WHERE m.provider='espn' AND (m.complete IS FALSE OR m.home_score IS NULL OR m.away_score IS NULL) GROUP BY m.provider_match_id, m.league_slug, m.competition_id, m.kickoff_ts, m.provider_status, m.home_score, m.away_score ORDER BY m.kickoff_ts, m.provider_match_id")
    return [dict(row) for row in connection.execute(query).mappings().all()]


def _events(connection: Any, league: str) -> Any:
    """Transmite eventos completos de una liga sin acumularlos en memoria."""

    query = text(f"SELECT e.provider_match_id, e.event_index, e.minute, e.second, e.team_provider_id, e.event_type, e.event_type_raw, e.raw_data->>'text' AS event_text, e.annulled FROM {SCHEMA}.events e JOIN {SCHEMA}.matches m ON m.provider='espn' AND m.provider_match_id=e.provider_match_id WHERE m.provider='espn' AND m.league_slug=:league AND m.complete IS TRUE AND m.home_score IS NOT NULL AND m.away_score IS NOT NULL ORDER BY e.provider_match_id, e.event_index")
    return connection.execution_options(stream_results=True).execute(query, {"league": league}).mappings()


def _normalize_matches(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convierte filas staging al contrato de construcción de ventanas."""

    return [{"match_id": int(row["provider_match_id"]), "competition_id": str(row["competition_id"]), "home_team_id": int(row["home_provider_team_id"]), "away_team_id": int(row["away_provider_team_id"]), "match_date": str(row["kickoff_ts"]), "season": _season(str(row["kickoff_ts"])), "home_score": int(row["home_score"]), "away_score": int(row["away_score"])} for row in rows]


def _normalize_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convierte eventos staging y recupera faltas desde la etiqueta raw."""

    return [{"event_id": int(row["event_index"]), "match_id": int(row["provider_match_id"]), "minute": int(row["minute"] or 0), "second": int(row["second"] or 0), "team_id": row["team_provider_id"], "event_type": _event_type(row) if not _is_shootout(row) else "penalty_shootout", "annulled": bool(row["annulled"])} for row in rows]


def _is_shootout(row: dict[str, Any]) -> bool:
    """Detecta la anotación ESPN de una tanda de penales."""

    text_value = str(row.get("event_text") or "")
    raw_type = str(row.get("event_type_raw") or "").lower().replace("-", "_")
    return raw_type == "penalty___scored" and bool(re.search(r"\(\d+\)", text_value))


def _mismatches(windows: list[dict[str, Any]], matches: list[dict[str, Any]]) -> list[int]:
    """Compara goles observados en ventanas contra el marcador final."""

    observed: dict[int, list[int]] = {}
    for row in windows:
        score = observed.setdefault(int(row["match_id"]), [0, 0])
        score[0 if row["is_home"] else 1] += int(row["goals"])
    expected = {int(row["match_id"]): (int(row["home_score"]), int(row["away_score"])) for row in matches}
    return sorted(match_id for match_id, score in expected.items() if tuple(observed.get(match_id, [0, 0])) != score)


def _materialize_match(match: dict[str, Any], events: list[dict[str, Any]], config: EventWindowsConfig, league: str) -> tuple[list[dict[str, Any]], list[int]]:
    """Construye y etiqueta las doce filas de un partido."""

    reconciled, _ = reconcile_staging_events(
        events, int(match["home_score"]), int(match["away_score"]),
        int(match["home_team_id"]), int(match["away_team_id"]),
    )
    windows, _ = build_windows([match], _normalize_events(reconciled), config)
    for window in windows:
        window["league_slug"] = league
        window["competition_id"] = match["competition_id"]
    return windows, _mismatches(windows, [match])


def _hash(value: Any) -> str:
    """Calcula hash reproducible del artefacto público."""

    raw = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _write(result: dict[str, Any]) -> None:
    """Publica ventanas, rechazos y auditoría sin payloads crudos."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    payloads = {"config": result["config"], "coverage": result["coverage"], "audit": result["audit"], "incomplete_matches": result["incomplete_matches"], "event_windows": result["event_windows"]}
    for name, value in payloads.items():
        (OUTPUT / f"{name}.json").write_text(json.dumps(value, indent=2, sort_keys=True, default=str), encoding="utf-8")
    (OUTPUT / "final_report.md").write_text(result["final_report"] + "\n", encoding="utf-8")
    hashes = {path.name: _hash(path.read_bytes()) for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"}
    (OUTPUT / "hashes.json").write_text(json.dumps(hashes, indent=2, sort_keys=True), encoding="utf-8")


def run() -> dict[str, Any]:
    """Construye ventanas por competición y mantiene la base SELECT-only."""

    engine = create_engine(_database_url(), future=True, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            complete = _matches(connection)
            incomplete = _incomplete(connection)
            groups = sorted({str(row["league_slug"]) for row in complete})
            all_windows, mismatch_ids, no_event_ids, event_types = [], [], [], Counter()
            for league in groups:
                source_matches = [row for row in complete if str(row["league_slug"]) == league]
                matches = _normalize_matches(source_matches)
                config = EventWindowsConfig(competition_id=league, competition_name=league)
                matches_by_id = {int(match["match_id"]): match for match in matches}
                emitted: set[int] = set()
                current_id: int | None = None
                current_events: list[dict[str, Any]] = []
                for raw_event in _events(connection, league):
                    event = dict(raw_event)
                    event_types.update([_event_type(event)])
                    match_id = int(event["provider_match_id"])
                    if current_id is not None and current_id != match_id:
                        windows, mismatches = _materialize_match(matches_by_id[current_id], current_events, config, league)
                        if mismatches:
                            mismatch_ids.extend(mismatches)
                        else:
                            all_windows.extend(windows)
                        emitted.add(current_id)
                        current_events = []
                    current_id = match_id
                    current_events.append(event)
                if current_id is not None:
                    windows, mismatches = _materialize_match(matches_by_id[current_id], current_events, config, league)
                    if mismatches:
                        mismatch_ids.extend(mismatches)
                    else:
                        all_windows.extend(windows)
                    emitted.add(current_id)
                for match_id, match in matches_by_id.items():
                    if match_id not in emitted:
                        no_event_ids.append(match_id)
            counts = {"matches": len(complete), "events": int(connection.execute(text(f"SELECT COUNT(*) FROM {SCHEMA}.events")).scalar_one()), "windows": len(all_windows), "incomplete": len(incomplete)}
    finally:
        engine.dispose()
    classification = "validated_for_multileague_labeling" if not mismatch_ids and not no_event_ids else "validated_for_multileague_labeling_with_exclusions"
    audit = {"classification": classification, "postgres_select_only": True, "score_mismatch_matches": sorted(set(mismatch_ids)), "no_event_timeline_matches": sorted(set(no_event_ids)), "incomplete_match_count": len(incomplete), "usable_match_count": len(all_windows) // 12, "raw_event_types_recovered": {key: value for key, value in sorted(event_types.items())}, "router_modified": False, "training_executed": False}
    result = {"config": {"version": "multileague_event_windows_v1", "window_minutes": 15, "rows_per_match": 12, "source_schema": SCHEMA, "complete_matches_only": True}, "coverage": {**counts, "usable_matches": len(all_windows) // 12, "leagues": len({str(row["league_slug"]) for row in complete}), "competitions": len({(str(row["league_slug"]), str(row["competition_id"])) for row in complete}), "event_types": dict(sorted(event_types.items()))}, "audit": audit, "incomplete_matches": incomplete, "event_windows": all_windows, "final_report": f"# Fase 38 — ventanas multi-liga\n\n**Clasificación:** `{audit['classification']}`\n\n- partidos completos de staging: `{counts['matches']}`\n- partidos utilizables: `{len(all_windows) // 12}`\n- ventanas materializadas: `{counts['windows']}`\n- eventos fuente staging: `{counts['events']}`\n- partidos incompletos excluidos: `{counts['incomplete']}`\n- partidos sin timeline excluidos: `{len(audit['no_event_timeline_matches'])}`\n- discrepancias parciales excluidas: `{len(audit['score_mismatch_matches'])}`\n- ligas: `{len({str(row['league_slug']) for row in complete})}`\n- entrenamiento ejecutado: `False`\n"}
    _write(result)
    LOGGER.info("Fase 38 ventanas multi-liga: %s", audit["classification"])
    return result


def main() -> int:
    """Ejecuta materialización multi-liga desde DATABASE_URL."""

    result = run()
    return 0 if result["audit"]["classification"] in {"validated_for_multileague_labeling", "validated_for_multileague_labeling_with_exclusions"} else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
