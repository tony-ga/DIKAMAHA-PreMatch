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
    home_team_logo: str | None = None
    away_team_logo: str | None = None


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


def _team(competitor: dict[str, Any]) -> tuple[int, str, str | None]:
    """Extrae ID y nombre de un competidor ESPN."""

    team = competitor.get("team") if isinstance(competitor.get("team"), dict) else {}
    identifier = team.get("id") or competitor.get("id")
    if not str(identifier).isdigit():
        raise FixtureResolutionError("scoreboard_team_id_missing")
    name = team.get("displayName") or team.get("name") or team.get("shortDisplayName")
    logo = team.get("logo")
    if not isinstance(logo, str):
        logos = team.get("logos")
        logo = logos[0].get("href") if isinstance(logos, list) and logos and isinstance(logos[0], dict) else None
    return int(identifier), str(name or identifier), str(logo) if logo else None


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
        home_id, home_name, home_logo = _team(competitors["home"]); away_id, away_name, away_logo = _team(competitors["away"])
        kickoff = event.get("date") or competition.get("date")
        if not isinstance(kickoff, str):
            continue
        parsed = datetime.fromisoformat(kickoff.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            continue
        status = ((competition.get("status") or {}).get("type") or {}).get("state") or "unknown"
        fixtures.append(ResolvedFixture(league_slug, int(event["id"]), str(competition.get("id") or event["id"]), parsed.astimezone(timezone.utc).isoformat(), home_id, away_id, home_name, away_name, str(status).lower(), home_team_logo=home_logo, away_team_logo=away_logo))
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


def allocate_fixtures_fairly(
    rows: list[dict[str, Any]], limit: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Recorta un barrido de fixtures repartiendo el cupo entre ligas.

    Compartido por `/v1/upcoming` y `/v1/live`
    (`src/dikamaha_service.py::_upcoming_catalog`,
    `src/live_prediction_runtime.py::LivePredictionRuntime.list_active`).

    Antes, `sorted(rows, key=kickoff)[:limit]` tomaba estrictamente los
    fixtures de kickoff más próximo sin mirar de qué liga son. Un torneo de
    clasificación con muchos partidos simultáneos (`uefa.europa.conf_qual`
    con 20+ kickoffs a la misma hora es el caso real medido en producción)
    agotaba el cupo completo por sí solo, y ningún partido de las otras
    ligas activas ese día aparecía en la respuesta -sin ninguna señal de que
    hubiera más partidos de los mostrados-.

    El reparto es una ronda: se toma como mucho un fixture por liga -el de
    kickoff más próximo de esa liga- antes de tomar un segundo de cualquier
    liga, y así sucesivamente. El orden final que se devuelve sigue siendo
    cronológico -esto sólo cambia *cuáles* fixtures entran al cupo, no cómo
    se presentan-. Ligas cuyo cupo se agotó antes de mostrar todos sus
    fixtures del barrido quedan en `hidden_leagues`, para que el contrato
    pueda declarar el truncamiento en vez de dejarlo invisible.
    """

    ordered = sorted(rows, key=lambda row: str(row.get("kickoff_ts", "")))
    queues: dict[str, list[dict[str, Any]]] = {}
    for row in ordered:
        queues.setdefault(str(row.get("league_slug")), []).append(row)
    league_order = sorted(
        queues, key=lambda slug: str(queues[slug][0].get("kickoff_ts", "")))
    cursors = {slug: 0 for slug in league_order}
    selected: list[dict[str, Any]] = []
    remaining = sum(len(queue) for queue in queues.values())
    while len(selected) < limit and remaining > 0:
        took_any = False
        for slug in league_order:
            if len(selected) >= limit:
                break
            cursor = cursors[slug]
            queue = queues[slug]
            if cursor >= len(queue):
                continue
            selected.append(queue[cursor])
            cursors[slug] = cursor + 1
            remaining -= 1
            took_any = True
        if not took_any:
            break
    selected.sort(key=lambda row: str(row.get("kickoff_ts", "")))
    hidden_leagues = sorted(
        slug for slug in league_order if cursors[slug] < len(queues[slug]))
    return selected, hidden_leagues


# Version: 1.0.0
# Created: 2026-07-27
