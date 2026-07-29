"""Contrato de presentación de contexto pre-match desde snapshots raw-first.

Requirements:
    sqlalchemy>=2

Version: 1.0.0
Created: 2026-07-29
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from src.prematch_raw_store import RawResponse

DEFAULT_LEDGER = Path("data/phase_100/raw_responses.sqlite")


class FixtureContextRepository(ABC):
    """Puerto read-only de snapshots raw por fixture."""

    @abstractmethod
    def rows(self, league: str, event_id: str) -> list[RawResponse]:
        """Devuelve snapshots persistidos sin hacer llamadas externas."""

    @abstractmethod
    def context_rows(self, league: str, team_ids: list[str]) -> list[RawResponse]:
        """Devuelve snapshots de liga y equipos para contexto visible."""


class SqlAlchemyFixtureContextRepository(FixtureContextRepository):
    """Adaptador SQLAlchemy de sólo lectura del ledger Fase 100."""

    def __init__(self, database_url: str) -> None:
        """Construye una fábrica de sesiones sin modificar el esquema."""

        self._engine = create_engine(database_url, future=True, pool_pre_ping=True)
        self._factory = sessionmaker(bind=self._engine, class_=Session)

    def rows(self, league: str, event_id: str) -> list[RawResponse]:
        """Ordena snapshots por captura para aplicar latest-before-kickoff."""

        statement = select(RawResponse).where(
            RawResponse.league_slug == league,
            RawResponse.scope_event_id == event_id,
        ).order_by(RawResponse.fetched_at.desc(), RawResponse.id.desc())
        with self._factory() as session:
            return list(session.execute(statement).scalars())

    def context_rows(self, league: str, team_ids: list[str]) -> list[RawResponse]:
        """Lee snapshots de liga y de ambos equipos sin tocar ESPN."""

        statement = select(RawResponse).where(
            RawResponse.league_slug == league,
            (RawResponse.entity_id == league) | RawResponse.entity_id.in_(team_ids),
        ).order_by(RawResponse.fetched_at.desc(), RawResponse.id.desc())
        with self._factory() as session:
            return list(session.execute(statement).scalars())


class FixtureContextService:
    """Normaliza sólo información visible, nunca features de modelo."""

    def __init__(self, repository: FixtureContextRepository) -> None:
        """Inyecta una fuente de snapshots persistidos."""

        self._repository = repository

    def context(self, league: str, event_id: str) -> dict[str, Any]:
        """Devuelve ficha reconciliada o ausencia explícita de snapshots."""

        rows = self._repository.rows(league, event_id)
        if not rows:
            return _unavailable(league, event_id)
        payloads, sources = _latest(rows)
        summary = payloads.get("summary", {})
        competition = _competition(summary)
        teams = _teams(competition)
        related = _before(self._repository.context_rows(league, _team_ids(teams)), competition.get("date"))
        context_payloads, context_sources = _latest(related)
        return {
            "status": "available", "schema_version": "fixture_context_v1",
            "display_only": True, "model_feature": False,
            "fixture": _fixture(league, event_id, competition),
            "competition": _competition_details(summary, competition),
            "venue": _venue(summary, payloads.get("competition", {})),
            "officials": _officials(payloads.get("officials", {})),
            "broadcasts": _broadcasts(summary, payloads.get("broadcasts", {})),
            "teams": teams, "team_context": _team_context(
                teams, context_payloads, related, competition.get("date")),
            "availability": _availability(teams, related),
            "editorial": _editorial(context_payloads.get("news", {})),
            "sources": {**sources, **context_sources},
        }


def default_context_service() -> FixtureContextService:
    """Crea el servicio sobre el ledger local Fase 100 ya materializado."""

    return FixtureContextService(SqlAlchemyFixtureContextRepository(
        f"sqlite+pysqlite:///{DEFAULT_LEDGER}"))


def _latest(rows: list[RawResponse]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Selecciona la última captura por recurso y conserva proveniencia."""

    payloads: dict[str, dict[str, Any]] = {}
    sources: dict[str, Any] = {}
    for row in rows:
        resource = _resource(row.endpoint)
        if resource in payloads:
            continue
        payloads[resource] = dict(row.response_json)
        sources[resource] = {"raw_response_id": row.id, "fetched_at": row.fetched_at.isoformat()}
    return payloads, sources


def _resource(endpoint: str) -> str:
    """Clasifica endpoints permitidos sin inspeccionar cuerpos de respuesta."""

    if endpoint.endswith("/officials"):
        return "officials"
    if endpoint.endswith("/broadcasts"):
        return "broadcasts"
    if endpoint.endswith("/summary"):
        return "summary"
    if endpoint.endswith("/standings"):
        return "standings"
    if endpoint.endswith("/schedule"):
        return "team_schedule"
    if endpoint.endswith("/news"):
        return "news"
    if endpoint.endswith("/injuries"):
        return "injuries"
    if endpoint.endswith("/roster"):
        return "roster"
    if "/competitions/" in endpoint:
        return "competition"
    return "event"


def _competition(summary: dict[str, Any]) -> dict[str, Any]:
    """Extrae la única competición Site declarada para el fixture."""

    header = summary.get("header") if isinstance(summary.get("header"), dict) else {}
    rows = header.get("competitions") if isinstance(header.get("competitions"), list) else []
    return rows[0] if rows and isinstance(rows[0], dict) else {}


def _fixture(league: str, event_id: str, competition: dict[str, Any]) -> dict[str, Any]:
    """Devuelve identidad y estado sin inferir kickoff ni orientación."""

    status = competition.get("status") if isinstance(competition.get("status"), dict) else {}
    kind = status.get("type") if isinstance(status.get("type"), dict) else {}
    return {"league_slug": league, "event_id": event_id, "kickoff_ts": competition.get("date"),
            "status": kind.get("description"), "status_detail": kind.get("shortDetail")}


def _competition_details(summary: dict[str, Any], competition: dict[str, Any]) -> dict[str, Any]:
    """Normaliza liga, temporada y fase publicados por ESPN."""

    header = summary.get("header") if isinstance(summary.get("header"), dict) else {}
    league = header.get("league") if isinstance(header.get("league"), dict) else {}
    season = header.get("season") if isinstance(header.get("season"), dict) else {}
    group = competition.get("groups") if isinstance(competition.get("groups"), dict) else {}
    return {"name": league.get("shortName") or league.get("name"), "season": season.get("name"),
            "phase": group.get("name") or competition.get("altGameNote"), "neutral_site": competition.get("neutralSite")}


def _venue(summary: dict[str, Any], competition: dict[str, Any]) -> dict[str, Any]:
    """Extrae sede publicada y conserva ausencia explícita por campo."""

    info = summary.get("gameInfo") if isinstance(summary.get("gameInfo"), dict) else {}
    venue = info.get("venue") if isinstance(info.get("venue"), dict) else {}
    venue = venue or (competition.get("venue") if isinstance(competition.get("venue"), dict) else {})
    address = venue.get("address") if isinstance(venue.get("address"), dict) else {}
    return {"name": venue.get("fullName"), "city": address.get("city"), "country": address.get("country"),
            "capacity": venue.get("capacity")}


def _officials(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Normaliza oficiales sin asignar nombres ante filas incompletas."""

    rows = payload.get("items") if isinstance(payload.get("items"), list) else []
    return [{"name": row.get("displayName") or row.get("fullName"), "role": row.get("role")}
            for row in rows if isinstance(row, dict)]


def _broadcasts(summary: dict[str, Any], payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Une broadcasts Site/Core y elimina duplicados sin inventar canales."""

    rows = list(summary.get("broadcasts") or []) + list(payload.get("items") or [])
    seen: set[str] = set(); output = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or row.get("market") or row.get("shortName") or "")
        if name and name not in seen:
            seen.add(name); output.append({"name": name, "market": row.get("market")})
    return output


def _teams(competition: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Preserva identidad local/visitante y branding publicado por ESPN."""

    output: dict[str, dict[str, Any]] = {}
    rows = competition.get("competitors") if isinstance(competition.get("competitors"), list) else []
    for row in rows:
        team = row.get("team") if isinstance(row, dict) and isinstance(row.get("team"), dict) else {}
        side = row.get("homeAway") if isinstance(row, dict) else None
        if side in {"home", "away"}:
            logos = team.get("logos") if isinstance(team.get("logos"), list) else []
            logo = logos[0].get("href") if logos and isinstance(logos[0], dict) else None
            output[side] = {"id": team.get("id"), "name": team.get("displayName"), "logo": logo,
                            "color": team.get("color"), "alternate_color": team.get("alternateColor")}
    return output


def _team_ids(teams: dict[str, dict[str, Any]]) -> list[str]:
    """Obtiene los IDs de ambos equipos sin usar nombres como llave."""

    return [str(row["id"]) for row in teams.values() if row.get("id") is not None]


def _before(rows: list[RawResponse], kickoff: Any) -> list[RawResponse]:
    """Excluye capturas posteriores al kickoff cuando la fecha es conocida."""

    if not isinstance(kickoff, str):
        return rows
    try:
        limit = datetime.fromisoformat(kickoff.replace("Z", "+00:00"))
    except ValueError:
        return []
    return [row for row in rows if _utc(row.fetched_at) < limit]


def _utc(value: datetime) -> datetime:
    """Normaliza timestamps SQLite ingenuos al instante UTC almacenado."""

    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _team_context(
    teams: dict[str, dict[str, Any]], payloads: dict[str, dict[str, Any]],
    rows: list[RawResponse], kickoff: Any,
) -> dict[str, Any]:
    """Une standing y agenda pre-kickoff, sólo para presentación."""

    table = _table_index(_standing_payload(rows, payloads.get("standings", {})))
    schedules = _schedule_index(rows, kickoff)
    return {side: {"standing": table.get(str(team.get("id"))),
                   "recent_schedule": schedules.get(str(team.get("id")), [])}
            for side, team in teams.items()}


def _table_index(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Indexa posición y estadísticas publicadas sin calcular una tabla nueva."""

    children = payload.get("children") if isinstance(payload.get("children"), list) else []
    entries = children[0].get("standings", {}).get("entries", []) if children and isinstance(children[0], dict) else []
    return {str(entry.get("team", {}).get("id")): _standing(entry) for entry in entries if isinstance(entry, dict)}


def _standing_payload(rows: list[RawResponse], fallback: dict[str, Any]) -> dict[str, Any]:
    """Prefiere el snapshot Site con entries sobre un Core vacío del proveedor."""

    for row in rows:
        if not row.endpoint.endswith("/standings"):
            continue
        if _table_index(dict(row.response_json)):
            return dict(row.response_json)
    return fallback


def _standing(entry: dict[str, Any]) -> dict[str, Any]:
    """Extrae el subconjunto estable de un entry de standings ESPN."""

    values = {str(row.get("name")): row.get("displayValue") for row in entry.get("stats", []) if isinstance(row, dict)}
    return {"rank": values.get("rank"), "points": values.get("points"), "played": values.get("gamesPlayed"),
            "wins": values.get("wins"), "draws": values.get("ties"), "losses": values.get("losses"),
            "goals_for": values.get("pointsFor"), "goals_against": values.get("pointsAgainst"),
            "goal_difference": values.get("pointDifferential")}


def _schedule_index(
    rows: list[RawResponse], kickoff: Any,
) -> dict[str, list[dict[str, Any]]]:
    """Devuelve los eventos previos publicados para el equipo del schedule."""

    output: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        team_id = _row_team_id(row)
        if not row.endpoint.endswith("/schedule") or team_id in output:
            continue
        payload = row.response_json; team = payload.get("team") if isinstance(payload.get("team"), dict) else {}
        events = payload.get("events") if isinstance(payload.get("events"), list) else []
        output[team_id] = [_schedule_event(event) for event in events
                           if isinstance(event, dict) and _event_before(event, kickoff)]
    return output


def _row_team_id(row: RawResponse) -> str:
    """Obtiene la identidad persistida o, en pruebas, la incluida en payload."""

    stored = getattr(row, "entity_id", None)
    if stored is not None:
        return str(stored)
    team = row.response_json.get("team") if isinstance(row.response_json.get("team"), dict) else {}
    return str(team.get("id") or "")


def _schedule_event(event: dict[str, Any]) -> dict[str, Any]:
    """Reduce un evento histórico a identidad, fecha y nombre visibles."""

    return {"event_id": event.get("id"), "date": event.get("date"), "name": event.get("name")}


def _event_before(event: dict[str, Any], kickoff: Any) -> bool:
    """Acepta sólo calendario publicado estrictamente anterior al kickoff."""

    if not isinstance(kickoff, str) or not isinstance(event.get("date"), str):
        return False
    try:
        return datetime.fromisoformat(event["date"].replace("Z", "+00:00")) < datetime.fromisoformat(kickoff.replace("Z", "+00:00"))
    except ValueError:
        return False


def _availability(teams: dict[str, dict[str, Any]], rows: list[RawResponse]) -> dict[str, Any]:
    """Resume roster e incidencias publicadas sin diagnosticar ausencias."""

    rosters = _team_resource(rows, "/roster")
    reports = _team_resource(rows, "/injuries")
    return {side: _team_availability(str(team.get("id") or ""), rosters, reports)
            for side, team in teams.items()}


def _team_resource(rows: list[RawResponse], suffix: str) -> dict[str, RawResponse]:
    """Indexa la captura más reciente por equipo y recurso ESPN."""

    output: dict[str, RawResponse] = {}
    for row in rows:
        team_id = _row_team_id(row)
        if row.endpoint.endswith(suffix) and team_id and team_id not in output:
            output[team_id] = row
    return output


def _team_availability(
    team_id: str, rosters: dict[str, RawResponse], reports: dict[str, RawResponse],
) -> dict[str, Any]:
    """Conserva la distinción entre reporte vacío y reporte no publicado."""

    roster = rosters.get(team_id); report = reports.get(team_id)
    athletes = _athletes(roster.response_json) if roster else []
    report_status = _report_status(report.response_json if report else None)
    return {"roster_status": "published" if roster else "not_published",
            "roster_count": len(athletes), "active_roster_count": _active_count(athletes),
            "injury_report_status": report_status,
            "published_injuries": _published_injuries(athletes),
            "sources": _availability_sources(roster, report)}


def _athletes(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Devuelve únicamente atletas con el shape publicado por ESPN."""

    rows = payload.get("athletes") if isinstance(payload.get("athletes"), list) else []
    return [row for row in rows if isinstance(row, dict)]


def _active_count(athletes: list[dict[str, Any]]) -> int:
    """Cuenta estados Active explícitos sin asumir estado en registros incompletos."""

    return sum(1 for athlete in athletes if _is_active(athlete))


def _is_active(athlete: dict[str, Any]) -> bool:
    """Reconoce sólo el estado de roster explícitamente publicado como activo."""

    status = athlete.get("status") if isinstance(athlete.get("status"), dict) else {}
    return str(status.get("type") or status.get("name") or "").lower() == "active"


def _report_status(payload: dict[str, Any] | None) -> str:
    """Clasifica disponibilidad del proveedor sin convertir ausencia en cero."""

    if not payload:
        return "not_published"
    return "published" if isinstance(payload.get("injuries"), list) else "unrecognized"


def _published_injuries(athletes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extrae incidencias incluidas dentro del roster publicado por ESPN."""

    output: list[dict[str, Any]] = []
    for athlete in athletes:
        rows = athlete.get("injuries") if isinstance(athlete.get("injuries"), list) else []
        output.extend(_athlete_injuries(athlete, rows))
    return output


def _athlete_injuries(athlete: dict[str, Any], rows: list[Any]) -> list[dict[str, Any]]:
    """Normaliza incidencias con jugador identificado, cuando el proveedor las emite."""

    return [{"player_id": athlete.get("id"), "player_name": athlete.get("fullName"),
             "status": row.get("status"), "description": row.get("description")}
            for row in rows if isinstance(row, dict)]


def _availability_sources(roster: RawResponse | None, report: RawResponse | None) -> dict[str, Any]:
    """Expone procedencia por recurso para permitir auditoría posterior."""

    return {"roster_raw_response_id": getattr(roster, "id", None),
            "injuries_raw_response_id": getattr(report, "id", None)}


def _editorial(payload: dict[str, Any]) -> dict[str, Any]:
    """Expone noticias como contexto editorial, nunca como señal de modelo."""

    rows = payload.get("articles") if isinstance(payload.get("articles"), list) else []
    articles = [_article(row) for row in rows if isinstance(row, dict)]
    return {"status": "published" if payload else "not_published",
            "model_feature": False, "articles": articles[:3]}


def _article(row: dict[str, Any]) -> dict[str, Any]:
    """Reduce un artículo ESPN a campos seguros para una interfaz compacta."""

    links = row.get("links") if isinstance(row.get("links"), dict) else {}
    web = links.get("web") if isinstance(links.get("web"), dict) else {}
    return {"id": row.get("id"), "headline": row.get("headline"),
            "published_at": row.get("published") or row.get("lastModified"),
            "source": "ESPN", "url": web.get("href")}


def _unavailable(league: str, event_id: str) -> dict[str, Any]:
    """Expresa ausencia de snapshot sin recurrir a ESPN ni imputación."""

    return {"status": "unavailable", "schema_version": "fixture_context_v1", "display_only": True,
            "model_feature": False, "fixture": {"league_slug": league, "event_id": event_id},
            "reason": "raw_snapshot_not_found", "sources": {}}


# Version: 1.0.0
# Created: 2026-07-29
