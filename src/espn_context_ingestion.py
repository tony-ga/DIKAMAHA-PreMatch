"""Ingesta raw-first incremental del contexto ESPN para la Fase 100.

Requirements:
    requests>=2.31
    sqlalchemy>=2
    tenacity>=8.2

Version: 1.0.0
Created: 2026-07-29
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Iterable

from src.espn_prospective_connector import EspnConnectorError, EspnResourceUnavailable
from src.espn_raw_first_provider import EspnRawFirstProvider
from src.prematch_data_contracts import CaptureKind, EntityType, StoredRawResponse
from src.prematch_snapshot_scheduler import UpcomingFixture, fixtures_from_scoreboard

LOGGER = logging.getLogger(__name__)


class DataClass(StrEnum):
    """Clasificación que evita mezclar datos con roles incompatibles."""

    DISPLAY_ONLY = "display_only"
    PREMATCH_CANDIDATE = "prematch_candidate"
    LIVE_ONLY = "live_only"
    SETTLEMENT_ONLY = "settlement_only"
    FINANCIAL_ISOLATED = "financial_isolated"


@dataclass(frozen=True, slots=True)
class ResourceSpec:
    """Describe un recurso ESPN permitido y su rol dentro de DIKAMAHA."""

    resource: str
    entity_type: EntityType
    data_class: DataClass
    fixture_scoped: bool = False


@dataclass(frozen=True, slots=True)
class IngestionConfig:
    """Controla profundidad y separación temporal de una corrida incremental."""

    include_financial: bool = False
    include_live: bool = False
    include_settlement: bool = False
    include_athlete_profiles: bool = False
    max_teams_per_league: int | None = None
    max_athletes_per_league: int | None = None


LEAGUE_RESOURCES = (
    ResourceSpec("calendar", EntityType.LEAGUE, DataClass.DISPLAY_ONLY),
    ResourceSpec("standings", EntityType.LEAGUE, DataClass.PREMATCH_CANDIDATE),
    ResourceSpec("teams", EntityType.LEAGUE, DataClass.DISPLAY_ONLY),
    ResourceSpec("seasons", EntityType.LEAGUE, DataClass.DISPLAY_ONLY),
    ResourceSpec("core_teams", EntityType.LEAGUE, DataClass.DISPLAY_ONLY),
    ResourceSpec("core_standings", EntityType.LEAGUE, DataClass.PREMATCH_CANDIDATE),
    ResourceSpec("rankings", EntityType.LEAGUE, DataClass.PREMATCH_CANDIDATE),
    ResourceSpec("leaders", EntityType.LEAGUE, DataClass.DISPLAY_ONLY),
    ResourceSpec("news", EntityType.LEAGUE, DataClass.DISPLAY_ONLY),
)
TEAM_RESOURCES = (
    ResourceSpec("team", EntityType.TEAM, DataClass.DISPLAY_ONLY),
    ResourceSpec("roster", EntityType.TEAM, DataClass.DISPLAY_ONLY),
    ResourceSpec("team_schedule", EntityType.TEAM, DataClass.PREMATCH_CANDIDATE),
    ResourceSpec("injuries", EntityType.TEAM, DataClass.PREMATCH_CANDIDATE),
)
FIXTURE_RESOURCES = (
    ResourceSpec("event", EntityType.EVENT, DataClass.DISPLAY_ONLY, True),
    ResourceSpec("competition", EntityType.EVENT, DataClass.DISPLAY_ONLY, True),
    ResourceSpec("summary", EntityType.EVENT, DataClass.DISPLAY_ONLY, True),
    ResourceSpec("officials", EntityType.EVENT, DataClass.PREMATCH_CANDIDATE, True),
    ResourceSpec("broadcasts", EntityType.EVENT, DataClass.DISPLAY_ONLY, True),
)
LIVE_RESOURCES = (
    ResourceSpec("situation", EntityType.EVENT, DataClass.LIVE_ONLY, True),
    ResourceSpec("probabilities", EntityType.EVENT, DataClass.LIVE_ONLY, True),
)
SETTLEMENT_RESOURCES = (ResourceSpec("plays", EntityType.EVENT, DataClass.SETTLEMENT_ONLY, True),)
FINANCIAL_RESOURCES = (ResourceSpec("odds", EntityType.EVENT, DataClass.FINANCIAL_ISOLATED, True),)


class EspnContextIngestionService:
    """Coordina descubrimiento e ingesta raw-first sin normalizar features."""

    def __init__(self, provider: EspnRawFirstProvider, config: IngestionConfig) -> None:
        """Inyecta el único puerto autorizado para red y persistencia."""

        self._provider = provider
        self._config = config
        self._coverage: Counter[str] = Counter()
        self._errors: list[dict[str, str]] = []

    def ingest_league(self, league: str, dates: Iterable[str]) -> dict[str, Any]:
        """Ingresa catálogo de liga, equipos y fixtures de fechas solicitadas."""

        fixtures = self._discover_fixtures(league, dates)
        payloads = self._capture_league_resources(league)
        team_ids = _limit(_team_ids(payloads.get("teams", {})), self._config.max_teams_per_league)
        self._capture_team_resources(team_ids)
        self._capture_fixture_resources(fixtures)
        self._capture_athletes(payloads.get("active_athletes", {}))
        return self.report(league, fixtures)

    def report(self, league: str, fixtures: list[UpcomingFixture]) -> dict[str, Any]:
        """Devuelve evidencia sanitizada sin incluir cuerpos raw."""

        return {
            "league": league, "fixtures": len(fixtures),
            "resources": dict(sorted(self._coverage.items())),
            "errors": list(self._errors), "raw_first": True,
            "model_features_created": False, "router_modified": False,
        }

    def _discover_fixtures(self, league: str, dates: Iterable[str]) -> list[UpcomingFixture]:
        """Persiste scoreboards antes de resolver fixtures programados."""

        fixtures: dict[str, UpcomingFixture] = {}
        for date in dates:
            payload = self._capture("scoreboard", EntityType.LEAGUE, league, date=date)
            for fixture in fixtures_from_scoreboard(payload or {}, league):
                fixtures[fixture.event_id] = fixture
        return sorted(fixtures.values(), key=lambda row: row.kickoff_ts)

    def _capture_league_resources(self, league: str) -> dict[str, dict[str, Any]]:
        """Captura una vez cada recurso transversal de la liga."""

        return {
            spec.resource: self._capture(spec.resource, spec.entity_type, league)
            for spec in LEAGUE_RESOURCES
        }

    def _capture_team_resources(self, team_ids: list[str]) -> None:
        """Captura identidad, roster, calendario y disponibilidad por equipo."""

        for team_id in team_ids:
            for spec in TEAM_RESOURCES:
                self._capture(spec.resource, spec.entity_type, team_id, team_id=team_id)

    def _capture_fixture_resources(self, fixtures: list[UpcomingFixture]) -> None:
        """Captura contexto del fixture, aislando live, settlement y cuotas."""

        for fixture in fixtures:
            for spec in self._fixture_specs():
                self._capture_fixture(spec, fixture)

    def _fixture_specs(self) -> tuple[ResourceSpec, ...]:
        """Selecciona explícitamente las clases habilitadas por la corrida."""

        specs = FIXTURE_RESOURCES
        if self._config.include_live:
            specs += LIVE_RESOURCES
        if self._config.include_settlement:
            specs += SETTLEMENT_RESOURCES
        if self._config.include_financial:
            specs += FINANCIAL_RESOURCES
        return specs

    def _capture_fixture(self, spec: ResourceSpec, fixture: UpcomingFixture) -> None:
        """Persiste un recurso asociado a un fixture programado identificado."""

        identifiers = {"event_id": fixture.event_id, "competition_id": fixture.competition_id}
        self._capture(
            spec.resource, spec.entity_type, fixture.event_id,
            scope_event_id=fixture.event_id, kickoff_ts=fixture.kickoff_ts,
            **identifiers,
        )

    def _capture_athletes(self, payload: dict[str, Any]) -> None:
        """Captura perfiles sólo cuando la corrida lo solicita explícitamente."""

        if not self._config.include_athlete_profiles:
            return
        athlete_ids = _limit(_athlete_ids(payload), self._config.max_athletes_per_league)
        for athlete_id in athlete_ids:
            self._capture("athlete", EntityType.ATHLETE, athlete_id, athlete_id=athlete_id)

    def _capture(
        self, resource: str, entity_type: EntityType, entity_id: str,
        **identifiers: Any,
    ) -> dict[str, Any] | None:
        """Confirma raw-first y registra el error sin detener la corrida."""

        try:
            stored = self._provider.fetch(
                resource, entity_type=entity_type, entity_id=entity_id,
                capture_kind=CaptureKind.PROSPECTIVE_SNAPSHOT,
                parser_version="phase100_raw_catalog_v1", **identifiers)
            self._coverage[resource] += 1
            return self._provider.replay(stored.id)
        except (EspnConnectorError, EspnResourceUnavailable, OSError, ValueError) as error:
            LOGGER.warning("phase100_capture_failed resource=%s error=%s", resource, error)
            self._errors.append({"resource": resource, "error": str(error)})
            return None


def _team_ids(payload: dict[str, Any] | None) -> list[str]:
    """Extrae IDs de equipos del formato Site sin inventar identidades."""

    rows = _nested(payload or {}, ("sports", 0, "leagues", 0, "teams"))
    identifiers = []
    for row in rows if isinstance(rows, list) else []:
        team = row.get("team") if isinstance(row, dict) else None
        value = team.get("id") if isinstance(team, dict) else None
        if str(value).isdigit():
            identifiers.append(str(value))
    return sorted(set(identifiers))


def _athlete_ids(payload: dict[str, Any] | None) -> list[str]:
    """Extrae IDs explícitos de atletas Core/V3, sin seguir referencias URL."""

    rows = (payload or {}).get("items", [])
    return sorted({str(row["id"]) for row in rows if isinstance(row, dict)
                   and str(row.get("id", "")).isdigit()})


def _nested(payload: dict[str, Any], path: tuple[Any, ...]) -> Any:
    """Navega una ruta conocida y devuelve ``None`` ante formato ausente."""

    value: Any = payload
    for key in path:
        if isinstance(key, int) and isinstance(value, list) and len(value) > key:
            value = value[key]
        elif isinstance(key, str) and isinstance(value, dict):
            value = value.get(key)
        else:
            return None
    return value


def _limit(values: list[str], maximum: int | None) -> list[str]:
    """Aplica límite opcional y rechaza configuraciones negativas."""

    if maximum is None:
        return values
    if maximum < 0:
        raise ValueError("maximum_must_not_be_negative")
    return values[:maximum]


def utc_now() -> datetime:
    """Expone reloj UTC explícito para runners y pruebas."""

    return datetime.now(timezone.utc)


# Version: 1.0.0
# Created: 2026-07-29
