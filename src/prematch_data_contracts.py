"""Contratos de datos pre-match independientes de ESPN y PostgreSQL.

# Requirements:
#   sqlalchemy>=2

Version: 1.0.0
Created: 2026-07-27
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class EntityType(StrEnum):
    """Entidades externas admitidas por el contrato raw-first."""

    EVENT = "event"
    TEAM = "team"
    ATHLETE = "athlete"
    LEAGUE = "league"
    SEASON = "season"
    VENUE = "venue"


class CaptureKind(StrEnum):
    """Naturaleza temporal de una captura externa."""

    HISTORICAL_RECONSTRUCTION = "historical_reconstruction"
    PROSPECTIVE_SNAPSHOT = "prospective_snapshot"


@dataclass(frozen=True, slots=True)
class RawResponseWrite:
    """Comando inmutable para persistir una respuesta externa."""

    provider: str
    endpoint: str
    request_params: dict[str, Any]
    response_json: dict[str, Any]
    fetched_at: datetime
    entity_type: EntityType
    capture_kind: CaptureKind
    entity_id: str | None = None
    scope_event_id: str | None = None
    league_slug: str | None = None
    season: str | None = None
    snapshot_bucket: str | None = None
    available_at: datetime | None = None
    cutoff_ts: datetime | None = None
    kickoff_ts: datetime | None = None
    http_status: int = 200
    parser_version: str = "unparsed"


@dataclass(frozen=True, slots=True)
class StoredRawResponse:
    """Confirmación desacoplada del registro raw persistido."""

    id: int
    request_hash: str
    response_hash: str
    fetched_at: datetime


class RawResponseRepository(ABC):
    """Puerto de persistencia para respuestas externas crudas."""

    @abstractmethod
    def store(self, command: RawResponseWrite) -> StoredRawResponse:
        """Persiste un payload crudo dentro de una transacción."""

    @abstractmethod
    def payload(self, response_id: int) -> dict[str, Any]:
        """Recupera el payload persistido para replay determinista."""

    @abstractmethod
    def capture_exists(
        self,
        request_hash: str,
        scope_event_id: str,
        snapshot_bucket: str,
    ) -> bool:
        """Comprueba idempotencia antes de consultar la fuente."""

    @abstractmethod
    def find_observation(
        self,
        request_hash: str,
        response_hash: str,
        fetched_at: datetime,
    ) -> StoredRawResponse | None:
        """Busca una observación externa exacta ya persistida."""


class PrematchDataProvider(ABC):
    """Puerto de lectura causal para fuentes pre-match."""

    @abstractmethod
    def fetch(
        self,
        resource: str,
        *,
        entity_type: EntityType,
        entity_id: str | None = None,
        scope_event_id: str | None = None,
        snapshot_bucket: str | None = None,
        cutoff_ts: datetime | None = None,
        kickoff_ts: datetime | None = None,
        capture_kind: CaptureKind = CaptureKind.PROSPECTIVE_SNAPSHOT,
        **identifiers: str,
    ) -> StoredRawResponse:
        """Captura y persiste un recurso antes de cualquier parseo."""


# Version: 1.0.0
# Created: 2026-07-27
