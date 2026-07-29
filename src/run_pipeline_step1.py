"""Primera rutina de ingesta controlada para ESPN.

Flujo:
- valida correspondencia entre `espn_event_id` y `match_id`;
- descarga y guarda todas las páginas del play-by-play;
- resuelve IDs ESPN hacia `teams.id` interno;
- persiste `raw_api_responses`, `events_ledger` y `events_timeline`;
- soporta `--dry-run` para validar sin escribir.

Requirements:
    pip install sqlalchemy python-dotenv psycopg2-binary tenacity requests

Version: 1.0.0
Created: 2026-07-14
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import requests
from sqlalchemy import MetaData, Table, insert, select, text
from sqlalchemy.orm import Session

from src.api.espn_client import ESPNClient, ESPNClientError, _extract_competition_id
from src.api.espn_parser import ESPNPlayParser, ParseReport
from src.database.manager import DatabaseConnectionError, DatabaseManager, Match, Team

logger = logging.getLogger("run_pipeline_step1")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)


@dataclass(slots=True)
class IngestionSummary:
    """Resumen de una corrida de ingesta."""

    raw_pages: int
    raw_items: int
    ledger_events: int
    timeline_events: int
    response_hashes: int
    content_hashes: int
    skipped_existing: bool = False


@dataclass(slots=True)
class ResolvedMatchTeams:
    """Mapa de equipos internos y sus IDs ESPN asociados."""

    home_internal_id: int
    away_internal_id: int
    home_internal_name: str
    away_internal_name: str
    home_espn_id: int
    away_espn_id: int
    home_espn_name: str | None = None
    away_espn_name: str | None = None


class ControlledIngestionError(RuntimeError):
    """Error bloqueante de la ingesta controlada."""


def canonical_json(payload: Any) -> str:
    """Serializa JSON de forma canónica."""

    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(payload: Any) -> str:
    """Calcula SHA-256 sobre JSON canónico."""

    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def load_tables(engine) -> dict[str, Table]:
    """Refleja las tablas usadas por la ingesta."""

    metadata = MetaData()
    names = ["matches", "teams", "raw_api_responses", "events_ledger", "events_timeline"]
    metadata.reflect(bind=engine, only=names)
    return {name: metadata.tables[name] for name in names}


def get_match(session: Session, match_id: int) -> Match:
    """Obtiene el partido interno solicitado."""

    match = session.execute(select(Match).where(Match.id == match_id)).scalar_one_or_none()
    if match is None:
        raise ControlledIngestionError(f"match_id={match_id} no existe.")
    return match


def has_completed_ingestion(
    session: Session,
    match_id: int,
    espn_event_id: str,
) -> tuple[bool, Optional[datetime]]:
    """Detecta si ya existe una ingesta completa para el mismo partido y evento.

    Criteria:
        - existe al menos una respuesta cruda de ESPN para ese `match_id` y `espn_event_id`;
        - existe al menos un registro en `events_ledger` para ese `match_id`;
        - existe al menos un registro en `events_timeline` para ese `match_id`.

    Returns:
        tuple[bool, Optional[datetime]]: bandera de corrida completa y fecha más reciente.
    """

    raw_exists = session.execute(
        select(text("1"))
        .select_from(text("raw_api_responses"))
        .where(text("match_id = :match_id AND source = 'ESPN' AND source_event_id = :espn_event_id"))
        .limit(1),
        {"match_id": match_id, "espn_event_id": espn_event_id},
    ).first()
    if raw_exists is None:
        return False, None
    ledger_exists = session.execute(
        select(text("1"))
        .select_from(text("events_ledger"))
        .where(text("match_id = :match_id")),
        {"match_id": match_id},
    ).first()
    timeline_exists = session.execute(
        select(text("1"))
        .select_from(text("events_timeline"))
        .where(text("match_id = :match_id")),
        {"match_id": match_id},
    ).first()
    if ledger_exists is None or timeline_exists is None:
        return False, None
    last_fetched = session.execute(
        select(text("MAX(fetched_at)"))
        .select_from(text("raw_api_responses"))
        .where(text("match_id = :match_id AND source = 'ESPN' AND source_event_id = :espn_event_id")),
        {"match_id": match_id, "espn_event_id": espn_event_id},
    ).scalar_one_or_none()
    return True, last_fetched


def load_team_id_map(session: Session) -> dict[int, int]:
    """Carga el mapeo ESPN team id -> teams.id interno."""

    rows = session.execute(
        select(Team.id, Team.espn_team_id).where(Team.espn_team_id.isnot(None))
    ).all()
    return {int(espn_id): int(internal_id) for internal_id, espn_id in rows}


def resolve_match_teams(session: Session, match: Match, event_json: dict[str, Any]) -> ResolvedMatchTeams:
    """Resuelve equipos internos y reporta los equipos ESPN detectados."""

    home = session.execute(select(Team).where(Team.id == match.home_team_id)).scalar_one()
    away = session.execute(select(Team).where(Team.id == match.away_team_id)).scalar_one()
    team_map = load_team_id_map(session)
    missing = []
    for side, team in (("home", home), ("away", away)):
        if team.espn_team_id is None:
            missing.append(f"{side}:{team.id}")
        elif int(team.espn_team_id) not in team_map:
            missing.append(f"{side}:{team.id}:espn={team.espn_team_id}")
    event_home_id, event_away_id = _extract_event_team_ids(event_json)
    event_home_name, event_away_name = _extract_event_team_names(event_json)
    if missing:
        raise ControlledIngestionError(
            "Falta mapping ESPN para equipos internos: " + ", ".join(missing)
            + f". Evento detectado home={event_home_id} ({event_home_name}),"
            + f" away={event_away_id} ({event_away_name})"
        )
    return ResolvedMatchTeams(
        home_internal_id=home.id,
        away_internal_id=away.id,
        home_internal_name=home.name,
        away_internal_name=away.name,
        home_espn_id=int(home.espn_team_id),
        away_espn_id=int(away.espn_team_id),
        home_espn_name=event_home_name,
        away_espn_name=event_away_name,
    )


def validate_match_identity(event_json: dict[str, Any], teams: ResolvedMatchTeams) -> None:
    """Confirma coincidencia de conjunto y orientación de equipos."""

    competition_id = _extract_competition_id(event_json)
    if competition_id is None:
        raise ControlledIngestionError("No se pudo extraer competition_id del evento ESPN.")
    event_home, event_away = _extract_event_team_ids(event_json)
    if event_home is None or event_away is None:
        raise ControlledIngestionError("No se pudieron resolver equipos del evento ESPN.")
    event_set = {event_home, event_away}
    expected_set = {teams.home_espn_id, teams.away_espn_id}
    if event_set != expected_set:
        raise ControlledIngestionError(
            "teams_mismatch: "
            f"interno home={teams.home_internal_id} ({teams.home_internal_name}, ESPN {teams.home_espn_id}) "
            f"away={teams.away_internal_id} ({teams.away_internal_name}, ESPN {teams.away_espn_id}) "
            f"vs ESPN home={event_home} ({teams.home_espn_name}), away={event_away} ({teams.away_espn_name})"
        )
    if event_home != teams.home_espn_id or event_away != teams.away_espn_id:
        raise ControlledIngestionError(
            "home_away_orientation_mismatch: "
            f"interno home={teams.home_internal_id} ({teams.home_internal_name}, ESPN {teams.home_espn_id}) "
            f"away={teams.away_internal_id} ({teams.away_internal_name}, ESPN {teams.away_espn_id}); "
            f"ESPN home={event_home} ({teams.home_espn_name}) away={event_away} ({teams.away_espn_name}). "
            "Corrige matches.id o usa otro evento ESPN."
        )


def _extract_event_team_ids(event_json: dict[str, Any]) -> tuple[Optional[int], Optional[int]]:
    """Extrae los IDs internos del evento ESPN usando team refs."""

    competitions = event_json.get("competitions") or []
    if not competitions or not isinstance(competitions[0], dict):
        return None, None
    competitors = competitions[0].get("competitors") or []
    team_ids: list[Optional[int]] = []
    for competitor in competitors:
        if not isinstance(competitor, dict):
            continue
        team = competitor.get("team")
        ref = team.get("$ref") if isinstance(team, dict) else None
        team_ids.append(_extract_id_from_ref(ref, "teams"))
    if len(team_ids) >= 2:
        return team_ids[0], team_ids[1]
    return None, None


def _extract_event_team_names(event_json: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    """Extrae nombres de equipos desde la respuesta del evento."""

    competitions = event_json.get("competitions") or []
    if not competitions or not isinstance(competitions[0], dict):
        return None, None
    competitors = competitions[0].get("competitors") or []
    names: list[Optional[str]] = []
    for competitor in competitors:
        if not isinstance(competitor, dict):
            continue
        team = competitor.get("team")
        if isinstance(team, dict):
            value = team.get("displayName") or team.get("name") or team.get("location")
            if isinstance(value, str) and value.strip():
                names.append(value.strip())
            else:
                names.append(_fetch_team_name_from_ref(team.get("$ref")))
    if len(names) >= 2:
        return names[0], names[1]
    return None, None


def _fetch_team_name_from_ref(ref: Any) -> Optional[str]:
    """Resuelve el nombre de un equipo a partir de su `$ref`."""

    if not isinstance(ref, str) or not ref.strip():
        return None
    try:
        response = requests.get(ref, timeout=20)
        response.raise_for_status()
        payload = response.json()
        name = payload.get("displayName") or payload.get("name") or payload.get("location")
        return name.strip() if isinstance(name, str) and name.strip() else None
    except requests.RequestException:
        return None


def _extract_id_from_ref(ref: Any, token: str) -> Optional[int]:
    """Extrae un identificador numérico desde un `$ref`."""

    if not isinstance(ref, str):
        return None
    match = re.search(rf"/{token}/(\d+)(?:\?|$)", ref)
    return int(match.group(1)) if match else None


def download_all_pages(client: ESPNClient, espn_event_id: str, competition_id: str) -> list[dict[str, Any]]:
    """Descarga todas las páginas del play-by-play."""

    first_page = client.get_play_by_play(espn_event_id, competition_id, limit=300)
    page_count = int(first_page.get("pageCount") or 1)
    pages = [first_page]
    for page_number in range(2, page_count + 1):
        url = f"{client.base_url}/events/{espn_event_id}/competitions/{competition_id}/plays?limit=300&page={page_number}"
        pages.append(client._get_json(url))
    return pages


def _hash_payload(payload: dict[str, Any]) -> str:
    """Hash canónico para respuestas y eventos."""

    return sha256_hex(payload)


def _now_utc() -> datetime:
    """Devuelve la hora actual en UTC sin microsegundos."""

    return datetime.now(timezone.utc).replace(microsecond=0)


def persist_raw_pages(
    session: Session,
    raw_table: Table,
    match_id: int,
    endpoint: str,
    espn_event_id: str,
    competition_id: str,
    pages: list[dict[str, Any]],
    dry_run: bool,
) -> list[int]:
    """Inserta una respuesta consolidada y devuelve los IDs creados."""

    created_ids: list[int] = []
    if dry_run:
        logger.info("DRY-RUN raw_api_responses pages=%s", len(pages))
        return created_ids
    for page_number, payload in enumerate(pages, start=1):
        response_hash = _hash_payload(payload)
        row = {
            "match_id": match_id,
            "endpoint": endpoint,
            "response_json": payload,
            "source": "ESPN",
            "source_event_id": espn_event_id,
            "source_competition_id": competition_id,
            "page_number": page_number,
            "page_count": int(payload.get("pageCount") or len(pages)),
            "total_count": int(payload.get("count") or len(payload.get("items") or [])),
            "http_status": 200,
            "response_hash": response_hash,
            "fetched_at": _now_utc(),
        }
        result = session.execute(insert(raw_table).values(row).returning(raw_table.c.id))
        created_ids.append(int(result.scalar_one()))
    return created_ids


def persist_ledger_and_timeline(
    session: Session,
    ledger_table: Table,
    timeline_table: Table,
    match_id: int,
    raw_response_id: Optional[int],
    pages: list[dict[str, Any]],
    parser: ESPNPlayParser,
    dry_run: bool,
) -> tuple[int, int]:
    """Normaliza eventos y persiste ledger/timeline."""

    consolidated = {"items": [item for page in pages for item in (page.get("items") or [])]}
    parsed_events = parser.parse(consolidated)
    report: ParseReport = parser.last_report or parser.report(consolidated)
    team_map = load_team_id_map(session)

    def _to_internal_team_id(team_id: Any) -> Optional[int]:
        if team_id is None:
            return None
        internal_id = team_map.get(int(team_id))
        if internal_id is None:
            raise ControlledIngestionError(f"No existe mapeo interno para ESPN team_id={team_id}")
        return internal_id

    if dry_run:
        ledger_total = len(parsed_events)
        timeline_total = sum(
            1
            for event in parsed_events
            if event["event_type"] in ESPNPlayParser.ALLOWED_EVENT_TYPES and event["team_id"] is not None
        )
        ledger_no_team = sum(1 for event in parsed_events if event["team_id"] is None)
        timeline_no_team = 0
        logger.info(
            "DRY-RUN raw_items=%s ledger_events=%s timeline_events=%s ledger_sin_team_id=%s timeline_sin_team_id=%s",
            report.total_events,
            ledger_total,
            timeline_total,
            ledger_no_team,
            timeline_no_team,
        )
        logger.info("DRY-RUN eventos procesados=%s relevantes=%s", report.total_events, len(parsed_events))
        return ledger_total, timeline_total
    ledger_ids: list[int] = []
    for index, event in enumerate(parsed_events):
        internal_team_id = _to_internal_team_id(event["team_id"])
        ledger_payload = {
            "match_id": match_id,
            "raw_api_response_id": raw_response_id,
            "espn_play_id": None,
            "espn_event_uid": None,
            "event_index": index,
            "minute": event["minute"],
            "second": event["second"],
            "period_number": None,
            "clock_value": None,
            "team_id": internal_team_id,
            "athlete_ref": event["athlete_ref"],
            "event_type_raw": event["event_type_raw"],
            "event_type": event["event_type"],
            "description": event["description"],
            "player_name": event["player_name"],
            "assist_name": event["assist_name"],
            "scoring_play": event["event_type"] == "goal",
            "penalty_kick": False,
            "valid": True,
            "raw_data": consolidated,
            "content_hash": _hash_payload({"index": index, "event": event, "match_id": match_id}),
            "created_at": _now_utc(),
        }
        ledger_result = session.execute(insert(ledger_table).values(ledger_payload).returning(ledger_table.c.id))
        ledger_id = int(ledger_result.scalar_one())
        ledger_ids.append(ledger_id)
        if event["event_type"] in ESPNPlayParser.ALLOWED_EVENT_TYPES and internal_team_id is not None:
            timeline_payload = {
                "match_id": match_id,
                "minute": event["minute"],
                "second": event["second"],
                "team_id": internal_team_id,
                "event_type": event["event_type"],
                "description": event["description"],
                "player_name": event["player_name"],
                "assist_name": event["assist_name"],
                "event_ledger_id": ledger_id,
                "event_type_raw": event["event_type_raw"],
                "athlete_ref": event["athlete_ref"],
                "raw_data": consolidated,
                "created_at": _now_utc(),
            }
            session.execute(insert(timeline_table).values(timeline_payload))
    return len(ledger_ids), sum(
        1
        for event in parsed_events
        if event["event_type"] in ESPNPlayParser.ALLOWED_EVENT_TYPES and event["team_id"] is not None
    )


def run_pipeline(
    espn_event_id: str,
    match_id: int,
    league: str,
    competition: str,
    season: str,
    dry_run: bool,
    force_reingest: bool,
) -> IngestionSummary:
    """Ejecuta la ingesta controlada dentro de una única transacción."""

    manager = DatabaseManager()
    tables = load_tables(manager.engine)
    client = ESPNClient(league=league)
    parser = ESPNPlayParser()
    endpoint = f"/events/{espn_event_id}/competitions/{competition}/plays?limit=300"
    with manager.SessionLocal() as session:
        match = get_match(session, match_id)
        completed, last_fetched = has_completed_ingestion(session, match_id, espn_event_id)
        if completed and not force_reingest:
            raise ControlledIngestionError(
                "ingesta_equivalente_existente: "
                f"match_id={match_id}, espn_event_id={espn_event_id}, "
                f"ultima_ingesta={last_fetched}"
            )
        event_payload = client.get_event(espn_event_id)
        validate_match_identity(event_payload, resolve_match_teams(session, match, event_payload))
        if completed and force_reingest:
            logger.warning(
                "FORCE-REINGEST habilitado para match_id=%s espn_event_id=%s; no se borrarán datos previos.",
                match_id,
                espn_event_id,
            )
        pages = download_all_pages(client, espn_event_id, competition)
        consolidated = {
            "items": [item for page in pages for item in (page.get("items") or [])],
            "pageCount": len(pages),
            "count": sum(len(page.get("items") or []) for page in pages),
        }
        if dry_run:
            ledger_count, timeline_count = persist_ledger_and_timeline(
                session,
                tables["events_ledger"],
                tables["events_timeline"],
                match.id,
                None,
                pages,
                parser,
                True,
            )
            logger.info("DRY-RUN validado para match_id=%s espn_event_id=%s", match_id, espn_event_id)
            return IngestionSummary(
                raw_pages=int(consolidated.get("pageCount") or 1),
                raw_items=int(consolidated.get("count") or len(consolidated.get("items") or [])),
                ledger_events=ledger_count,
                timeline_events=timeline_count,
                response_hashes=0,
                content_hashes=0,
                skipped_existing=completed and not force_reingest,
            )
    with manager.SessionLocal() as write_session:
        with write_session.begin():
            raw_ids = persist_raw_pages(
                write_session,
                tables["raw_api_responses"],
                match.id,
                endpoint,
                espn_event_id,
                competition,
                pages,
                dry_run,
            )
            ledger_count, timeline_count = persist_ledger_and_timeline(
                write_session,
                tables["events_ledger"],
                tables["events_timeline"],
                match.id,
                raw_ids[0] if raw_ids else None,
                pages,
                parser,
                dry_run,
            )
    return IngestionSummary(
        raw_pages=int(consolidated.get("pageCount") or 1),
        raw_items=int(consolidated.get("count") or len(consolidated.get("items") or [])),
        ledger_events=ledger_count,
        timeline_events=timeline_count,
        response_hashes=0 if dry_run else 1,
        content_hashes=0 if dry_run else ledger_count,
        skipped_existing=False,
    )


def build_parser() -> argparse.ArgumentParser:
    """Construye el parser CLI."""

    parser = argparse.ArgumentParser(description="Pipeline step 1 controlado para ESPN.")
    parser.add_argument("--espn-event-id", required=True)
    parser.add_argument("--match-id", required=True, type=int)
    parser.add_argument("--league", required=True)
    parser.add_argument("--competition", required=True)
    parser.add_argument("--season", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force-reingest", action="store_true")
    return parser


def main() -> int:
    """Entrada principal del pipeline."""

    args = build_parser().parse_args()
    try:
        summary = run_pipeline(
            espn_event_id=args.espn_event_id,
            match_id=args.match_id,
            league=args.league,
            competition=args.competition,
            season=args.season,
            dry_run=args.dry_run,
            force_reingest=args.force_reingest,
        )
        print(summary)
        return 0
    except (ControlledIngestionError, ESPNClientError, DatabaseConnectionError) as exc:
        logger.error("Ingesta rechazada: %s", exc, exc_info=True)
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
