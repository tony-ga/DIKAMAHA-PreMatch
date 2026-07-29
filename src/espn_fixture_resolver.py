"""Resuelve fixtures próximos desde el scoreboard documentado de ESPN.

No consulta play-by-play, no lee marcador final como feature y no persiste
datos. El conector inyectado conserva retry y caché de respuestas crudas.

Requirements:
    - requests
    - tenacity

Version: 1.0.0
Created: 2026-07-27
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Protocol

from src.espn_prospective_connector import EspnProspectiveConnector


class FixtureResolutionError(ValueError):
    """Error controlado de resolución, ambigüedad o ausencia de fixture."""


class ScoreboardProvider(Protocol):
    """Puerto mínimo que permite probar el resolver sin red."""

    def scoreboard(self, date: str) -> dict[str, Any]:
        """Obtiene el scoreboard de una fecha ESPN."""


@dataclass(frozen=True, slots=True)
class FixtureLookup:
    """Criterios de búsqueda del partido próximo."""

    league_slug: str
    kickoff_date: str
    match_id: int | None = None
    home_team_id: int | None = None
    away_team_id: int | None = None
    home_team_name: str | None = None
    away_team_name: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedFixture:
    """Identidad y kickoff resueltos desde ESPN."""

    league_slug: str
    match_id: int
    competition_id: str
    kickoff_ts: str
    home_team_id: int
    away_team_id: int
    home_team_name: str
    away_team_name: str
    provider_status: str
    source: str = "espn_scoreboard"


def _normal(value: str | None) -> str:
    """Normaliza nombres para comparación exacta tolerante a acentos."""

    raw = unicodedata.normalize("NFKD", value or "")
    return "".join(char for char in raw if not unicodedata.combining(char)).casefold().strip()


def _date(value: str) -> date:
    """Valida una fecha de búsqueda ESPN en formato YYYYMMDD."""

    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError as error:
        raise FixtureResolutionError("kickoff_date_must_be_YYYYMMDD") from error


def _dates(center: date) -> tuple[str, ...]:
    """Construye una ventana UTC de tres días para desfases de kickoff."""

    return tuple((center + timedelta(days=offset)).strftime("%Y%m%d") for offset in (-1, 0, 1))


def _team(competitor: dict[str, Any]) -> tuple[int, str]:
    """Extrae ID y nombre de un competidor ESPN."""

    team = competitor.get("team") if isinstance(competitor.get("team"), dict) else {}
    identifier = team.get("id") or competitor.get("id")
    if not str(identifier).isdigit():
        raise FixtureResolutionError("scoreboard_team_id_missing")
    name = team.get("displayName") or team.get("name") or team.get("shortDisplayName")
    return int(identifier), str(name or identifier)


def scoreboard_fixtures(payload: dict[str, Any], league_slug: str) -> list[ResolvedFixture]:
    """Convierte un scoreboard ESPN en fixtures con orientación validada."""

    fixtures: list[ResolvedFixture] = []
    for event in payload.get("events", []):
        if not isinstance(event, dict) or not str(event.get("id", "")).isdigit():
            continue
        competition = (event.get("competitions") or [None])[0]
        if not isinstance(competition, dict):
            continue
        competitors = {row.get("homeAway"): row for row in competition.get("competitors", []) if isinstance(row, dict)}
        if not isinstance(competitors.get("home"), dict) or not isinstance(competitors.get("away"), dict):
            continue
        home_id, home_name = _team(competitors["home"]); away_id, away_name = _team(competitors["away"])
        kickoff = event.get("date") or competition.get("date")
        if not isinstance(kickoff, str):
            continue
        parsed = datetime.fromisoformat(kickoff.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            continue
        status = ((competition.get("status") or {}).get("type") or {}).get("state") or "unknown"
        fixtures.append(ResolvedFixture(league_slug, int(event["id"]), str(competition.get("id") or event["id"]), parsed.astimezone(timezone.utc).isoformat(), home_id, away_id, home_name, away_name, str(status).lower()))
    return fixtures


def _matches(fixture: ResolvedFixture, lookup: FixtureLookup) -> bool:
    """Aplica filtros de ID, equipos y nombres sin fuzzy matching peligroso."""

    if lookup.match_id is not None and fixture.match_id != lookup.match_id:
        return False
    if lookup.home_team_id is not None and fixture.home_team_id != lookup.home_team_id:
        return False
    if lookup.away_team_id is not None and fixture.away_team_id != lookup.away_team_id:
        return False
    if lookup.home_team_name and _normal(fixture.home_team_name) != _normal(lookup.home_team_name):
        return False
    if lookup.away_team_name and _normal(fixture.away_team_name) != _normal(lookup.away_team_name):
        return False
    return True


class EspnFixtureResolver:
    """Resuelve un único fixture futuro mediante scoreboard y caché."""

    def __init__(self, provider: ScoreboardProvider | None = None) -> None:
        """Inicializa el resolver con un proveedor real o de prueba."""

        self._provider = provider or EspnProspectiveConnector()

    def resolve(self, lookup: FixtureLookup) -> ResolvedFixture:
        """Busca exactamente un fixture y exige que aún sea futuro."""

        center = _date(lookup.kickoff_date)
        candidates = [fixture for day in _dates(center) for fixture in scoreboard_fixtures(self._provider.scoreboard(day), lookup.league_slug) if _matches(fixture, lookup)]
        unique = {fixture.match_id: fixture for fixture in candidates}
        if not unique:
            raise FixtureResolutionError("fixture_not_found")
        if len(unique) > 1:
            raise FixtureResolutionError("fixture_ambiguous")
        fixture = next(iter(unique.values()))
        if datetime.fromisoformat(fixture.kickoff_ts) <= datetime.now(timezone.utc):
            raise FixtureResolutionError("fixture_is_not_future")
        if fixture.provider_status in {"in", "live", "post", "final", "completed"}:
            raise FixtureResolutionError("fixture_not_scheduled")
        return fixture


def connector_for_league(league_slug: str) -> EspnFixtureResolver:
    """Crea un resolver ESPN aislado para una liga concreta."""

    from src.espn_prospective_connector import EspnConnectorConfig

    return EspnFixtureResolver(EspnProspectiveConnector(EspnConnectorConfig(league=league_slug)))


# Version: 1.0.0
# Created: 2026-07-27
