"""Rematerializa un snapshot candidato con la taxonomía ESPN v1.1.

La ejecución es SELECT-only sobre staging, no activa el candidato y no toca el
snapshot ni el router oficiales. Compara únicamente señales observables para
detectar cambios semánticos antes del gate global.

Requirements:
    - SQLAlchemy==2.0.41
    - psycopg2-binary==2.9.10

Version: 1.0.0
Created: 2026-07-27
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_phase_38_multileague_event_windows import _is_shootout, _season
from src.espn_event_taxonomy import classify_event_type, classify_play
from src.event_windows_v1 import EventWindowsConfig, build_windows
from src.prematch_snapshot_registry import resolve_active_snapshot

LOGGER = logging.getLogger(__name__)
SCHEMA = "prospective_staging_v2"
OUTPUT = ROOT / "artifacts/phase_60_taxonomy_snapshot_candidate_v1"
SIGNAL_FIELDS = ("goals", "shots", "shots_on_target", "shots_blocked", "corners", "fouls", "yellow_cards", "red_cards", "substitutions", "pressure", "pressure_conceded", "score_for_start", "score_against_start", "goal_difference_start")


def _database_url() -> str:
    """Obtiene DATABASE_URL sin exponer su contenido."""

    value = os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("missing_database_url")
    return value


def _matches(connection: Any) -> list[dict[str, Any]]:
    """Lee partidos completos desde staging sin modificar la base."""

    query = text(f"SELECT provider_match_id, league_slug, competition_id, kickoff_ts, home_provider_team_id, away_provider_team_id, home_score, away_score FROM {SCHEMA}.matches WHERE provider='espn' AND complete IS TRUE AND home_score IS NOT NULL AND away_score IS NOT NULL ORDER BY kickoff_ts, provider_match_id")
    return [dict(row) for row in connection.execute(query).mappings().all()]


def _events(connection: Any, league: str) -> Any:
    """Transmite eventos raw de una liga para normalización en memoria."""

    query = text(f"SELECT e.provider_match_id, e.event_index, e.minute, e.second, e.team_provider_id, e.event_type, e.event_type_raw, e.raw_data, e.annulled FROM {SCHEMA}.events e JOIN {SCHEMA}.matches m ON m.provider='espn' AND m.provider_match_id=e.provider_match_id WHERE m.provider='espn' AND m.league_slug=:league AND m.complete IS TRUE AND m.home_score IS NOT NULL AND m.away_score IS NOT NULL ORDER BY e.provider_match_id, e.event_index")
    return connection.execution_options(stream_results=True).execute(query, {"league": league}).mappings()


def _match(row: dict[str, Any]) -> dict[str, Any]:
    """Convierte identidad staging al contrato de ventanas."""

    kickoff = str(row["kickoff_ts"])
    return {"match_id": int(row["provider_match_id"]), "competition_id": str(row["competition_id"]), "home_team_id": int(row["home_provider_team_id"]), "away_team_id": int(row["away_provider_team_id"]), "match_date": kickoff, "season": _season(kickoff), "home_score": int(row["home_score"]), "away_score": int(row["away_score"])}


def _event(row: dict[str, Any]) -> dict[str, Any]:
    """Clasifica de nuevo una fila usando raw_data como fuente primaria."""

    raw = row.get("raw_data") if isinstance(row.get("raw_data"), dict) else {}
    if raw:
        event_type, _ = classify_play(raw)
        text_value = str(raw.get("text") or "")
    else:
        event_type = classify_event_type(row.get("event_type_raw") or row.get("event_type"))
        text_value = ""
    if _is_shootout({"event_type_raw": row.get("event_type_raw"), "event_text": text_value}):
        event_type = "penalty_shootout"
    return {"event_id": int(row["event_index"]), "match_id": int(row["provider_match_id"]), "minute": int(row["minute"] or 0), "second": int(row["second"] or 0), "team_id": row["team_provider_id"], "event_type": event_type, "annulled": bool(row["annulled"])}


def _materialize(matches: list[dict[str, Any]], connection: Any) -> tuple[list[dict[str, Any]], Counter[str], list[int]]:
    """Construye ventanas por liga y registra discrepancias de marcador."""

    by_id = {int(row["match_id"]): row for row in matches}
    output: list[dict[str, Any]] = []
    types: Counter[str] = Counter()
    mismatches: list[int] = []
    for league in sorted({str(row["league_slug"]) for row in matches}):
        league_matches = {int(row["match_id"]): row for row in matches if str(row["league_slug"]) == league}
        grouped: dict[int, list[dict[str, Any]]] = {match_id: [] for match_id in league_matches}
        for raw_row in _events(connection, league):
            event = _event(dict(raw_row))
            grouped[int(event["match_id"])].append(event)
            types[str(event["event_type"])] += 1
        for match_id, events in grouped.items():
            windows, _ = build_windows([league_matches[match_id]], events, EventWindowsConfig(competition_id=league, competition_name=league))
            observed = (sum(int(row["goals"]) for row in windows if row["is_home"]), sum(int(row["goals"]) for row in windows if not row["is_home"]))
            expected = (int(league_matches[match_id]["home_score"]), int(league_matches[match_id]["away_score"]))
            if observed != expected:
                mismatches.append(match_id)
            if observed == expected:
                for window in windows:
                    window["league_slug"] = league
                output.extend(windows)
    return output, types, sorted(set(mismatches))


def _hash(value: Any) -> str:
    """Calcula hash SHA-256 del contenido público del candidato."""

    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def _compare(candidate: list[dict[str, Any]], active: list[dict[str, Any]]) -> dict[str, Any]:
    """Compara filas y señales contra el snapshot activo."""

    left = {(int(row["match_id"]), int(row["team_id"]), int(row["window_index"])): row for row in candidate}
    right = {(int(row["match_id"]), int(row["team_id"]), int(row["window_index"])): row for row in active}
    common = set(left) & set(right)
    changed = [key for key in common if any(left[key].get(field) != right[key].get(field) for field in SIGNAL_FIELDS)]
    return {"candidate_rows": len(candidate), "active_rows": len(active), "common_rows": len(common), "candidate_only_rows": len(set(left) - set(right)), "active_only_rows": len(set(right) - set(left)), "signal_changed_rows": len(changed), "signal_changed_sample": [list(key) for key in sorted(changed)[:20]]}


def _write(payload: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    """Publica candidato, auditoría y hashes sin payloads raw."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "event_windows.json").write_text(json.dumps(rows, indent=2, sort_keys=True, default=str), encoding="utf-8")
    (OUTPUT / "audit.json").write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    report = ["# Fase 60 — candidato de snapshot con taxonomía v1.1", "", f"**Clasificación:** `{payload['classification']}`", "", f"- partidos completos staging: `{payload['complete_matches']}`", f"- filas candidatas: `{payload['candidate_rows']}`", f"- eventos raw reclasificados: `{payload['event_type_counts_total']}`", f"- eventos `unclassified`: `{payload['unclassified_events']}`", f"- discrepancias de marcador: `{payload['score_mismatch_matches']}`", f"- filas con señales cambiadas: `{payload['comparison']['signal_changed_rows']}`", "- snapshot activo modificado: `False`", "- router modificado: `False`", "- Markov promovido: `False`", "", "El candidato permanece aislado; sólo el gate global puede autorizar la siguiente fase."]
    (OUTPUT / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    files = [path for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"]
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
    (OUTPUT / "hashes.json").write_text(json.dumps(hashes, indent=2, sort_keys=True), encoding="utf-8")


def run() -> dict[str, Any]:
    """Ejecuta la rematerialización aislada y devuelve su auditoría."""

    active_path = resolve_active_snapshot()
    active = json.loads(active_path.read_text(encoding="utf-8"))
    engine = create_engine(_database_url(), future=True, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            matches = _matches(connection)
            candidate, types, mismatches = _materialize([_match(row) | {"league_slug": str(row["league_slug"])} for row in matches], connection)
            db_counts = {"matches": int(connection.execute(text(f"SELECT COUNT(*) FROM {SCHEMA}.matches")).scalar_one()), "events": int(connection.execute(text(f"SELECT COUNT(*) FROM {SCHEMA}.events")).scalar_one())}
    finally:
        engine.dispose()
    comparison = _compare(candidate, active)
    unknown = int(types.get("unclassified", 0))
    classification = "taxonomy_candidate_ready_for_global_gate" if unknown == 0 and comparison["signal_changed_rows"] == 0 and comparison["active_only_rows"] == 0 else "taxonomy_candidate_requires_review"
    audit = {"classification": classification, "taxonomy_version": "espn_event_taxonomy_v1.1", "source_schema": SCHEMA, "active_snapshot": str(active_path), "complete_matches": len(matches), "candidate_rows": len(candidate), "event_type_counts": dict(sorted(types.items())), "event_type_counts_total": sum(types.values()), "unclassified_events": unknown, "score_mismatch_matches": mismatches, "comparison": comparison, "database_counts": db_counts, "snapshot_active_modified": False, "router_modified": False, "markov_promoted": False}
    _write(audit, candidate)
    LOGGER.info("Fase 60 candidato: %s", classification)
    return audit


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    try:
        raise SystemExit(0 if run()["classification"] == "taxonomy_candidate_ready_for_global_gate" else 1)
    except (OSError, RuntimeError, ValueError) as error:
        LOGGER.error("Candidato de taxonomía rechazado: %s", error)
        raise SystemExit(2) from error

# Version: 1.0.0
# Created: 2026-07-27
