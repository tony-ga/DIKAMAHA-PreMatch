"""Proveedor ESPN que confirma persistencia cruda antes del parseo.

# Requirements:
#   requests>=2.31
#   tenacity>=8.2
#   sqlalchemy>=2

Version: 1.0.0
Created: 2026-07-27
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from src.espn_prospective_connector import (
    EspnFetchResult,
    EspnProspectiveConnector,
    EspnRequest,
    PAGINATED_RESOURCES,
)
from src.prematch_data_contracts import (
    CaptureKind,
    EntityType,
    PrematchDataProvider,
    RawResponseRepository,
    RawResponseWrite,
    StoredRawResponse,
)
from src.prematch_raw_store import canonical_hash

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CaptureMetadata:
    """Metadatos causales independientes del transporte."""

    entity_type: EntityType
    entity_id: str | None
    scope_event_id: str | None
    snapshot_bucket: str | None
    cutoff_ts: datetime | None
    kickoff_ts: datetime | None
    capture_kind: CaptureKind
    parser_version: str = "unparsed"


class EspnRawFirstProvider(PrematchDataProvider):
    """Orquestador causal entre transporte ESPN y persistencia."""

    def __init__(
        self,
        connector: EspnProspectiveConnector,
        repository: RawResponseRepository,
    ) -> None:
        """Inyecta transporte y repositorio."""

        self._connector = connector
        self._repository = repository

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
        parser_version: str = "unparsed",
        **identifiers: str,
    ) -> StoredRawResponse:
        """Captura un recurso y devuelve sólo la confirmación persistida."""
        request = self._connector.resource_request(resource, **identifiers)
        self._reject_duplicate(request.url, request.params, scope_event_id, snapshot_bucket)
        result = self._fetch_result(resource, request)
        metadata = CaptureMetadata(
            entity_type, entity_id, scope_event_id, snapshot_bucket,
            cutoff_ts, kickoff_ts, capture_kind, parser_version,
        )
        return self._persist(request, result, metadata)

    def _fetch_result(
        self, resource: str, request: EspnRequest,
    ) -> EspnFetchResult:
        """Obtiene colecciones completas sin alterar recursos singulares."""

        if resource in PAGINATED_RESOURCES:
            return self._connector.fetch_all_pages_result(request)
        return self._connector.fetch_request_result(request)

    def replay(self, response_id: int) -> dict[str, Any]:
        """Lee el payload confirmado para parsers posteriores."""

        return self._repository.payload(response_id)

    def fetch_plays(
        self, *, event_id: str, competition_id: str,
        entity_type: EntityType, entity_id: str,
    ) -> StoredRawResponse:
        """Persiste el play-by-play paginado completo antes del parseo."""

        request = self._connector.resource_request(
            "plays", event_id=event_id, competition_id=competition_id)
        result = self._connector.plays_fetch_result(
            event_id, competition_id)
        metadata = CaptureMetadata(
            entity_type, entity_id, event_id, None, None, None,
            CaptureKind.HISTORICAL_RECONSTRUCTION)
        return self._persist(request, result, metadata)

    def _persist(
        self,
        request: EspnRequest,
        result: EspnFetchResult,
        metadata: CaptureMetadata,
    ) -> StoredRawResponse:
        """Valida temporalidad y persiste la captura."""
        fetched_at = _utc(result.source_fetched_at)
        kickoff_ts = _optional_utc(metadata.kickoff_ts)
        _validate_temporal(fetched_at, kickoff_ts, metadata.capture_kind)
        cutoff_ts = _effective_cutoff(metadata.cutoff_ts, fetched_at, metadata.capture_kind)
        command = RawResponseWrite(
            provider="espn_unofficial",
            endpoint=request.url,
            request_params=request.params,
            response_json=result.payload,
            fetched_at=fetched_at,
            entity_type=metadata.entity_type,
            capture_kind=metadata.capture_kind,
            entity_id=metadata.entity_id,
            scope_event_id=metadata.scope_event_id,
            league_slug=self._connector.config.league,
            snapshot_bucket=metadata.snapshot_bucket,
            cutoff_ts=cutoff_ts,
            kickoff_ts=kickoff_ts,
            http_status=result.http_status,
            parser_version=metadata.parser_version,
        )
        existing = self._existing_observation(command)
        return existing if existing is not None else self._repository.store(command)

    def _existing_observation(
        self,
        command: RawResponseWrite,
    ) -> StoredRawResponse | None:
        """Reutiliza una observación exacta proveniente de caché."""

        if command.snapshot_bucket is not None:
            return None
        request_hash = canonical_hash(
            {"endpoint": command.endpoint, "params": command.request_params}
        )
        response_hash = canonical_hash(command.response_json)
        return self._repository.find_observation(
            request_hash,
            response_hash,
            command.fetched_at,
        )

    def _reject_duplicate(
        self,
        endpoint: str,
        params: dict[str, Any],
        scope_event_id: str | None,
        snapshot_bucket: str | None,
    ) -> None:
        """Evita red y escritura duplicadas para un bucket ya capturado."""

        if scope_event_id is None or snapshot_bucket is None:
            return
        request_hash = canonical_hash({"endpoint": endpoint, "params": params})
        if self._repository.capture_exists(request_hash, scope_event_id, snapshot_bucket):
            raise DuplicateSnapshotError("snapshot_already_captured")


class DuplicateSnapshotError(RuntimeError):
    """Indica que el recurso ya existe para fixture y bucket."""


def _validate_temporal(
    fetched_at: datetime,
    kickoff_ts: datetime | None,
    capture_kind: CaptureKind,
) -> None:
    """Impide etiquetar como prospectiva una captura tardía."""

    if capture_kind == CaptureKind.PROSPECTIVE_SNAPSHOT and kickoff_ts is not None:
        if fetched_at >= kickoff_ts:
            raise ValueError("prospective_capture_not_before_kickoff")


def _optional_utc(value: datetime | None) -> datetime | None:
    """Normaliza un timestamp opcional."""

    return _utc(value) if value is not None else None


def _effective_cutoff(
    cutoff_ts: datetime | None,
    fetched_at: datetime,
    capture_kind: CaptureKind,
) -> datetime | None:
    """Usa disponibilidad efectiva como cutoff mínimo prospectivo."""

    if capture_kind != CaptureKind.PROSPECTIVE_SNAPSHOT:
        return _optional_utc(cutoff_ts)
    requested = _optional_utc(cutoff_ts)
    if requested is not None and requested < fetched_at:
        raise ValueError("cutoff_precedes_fetch")
    return requested or fetched_at


def _utc(value: datetime) -> datetime:
    """Convierte a UTC y rechaza datetimes sin zona."""

    if value.tzinfo is None:
        raise ValueError("timezone_required")
    return value.astimezone(timezone.utc)


# Version: 1.0.0
# Created: 2026-07-27
