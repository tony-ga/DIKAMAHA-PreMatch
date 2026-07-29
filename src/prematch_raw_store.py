"""Persistencia SQLAlchemy del contrato genérico ``raw_responses``.

# Requirements:
#   sqlalchemy>=2

Version: 1.0.0
Created: 2026-07-27
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, DateTime, Index, Integer, String, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from src.prematch_data_contracts import (
    RawResponseRepository,
    RawResponseWrite,
    StoredRawResponse,
)

LOGGER = logging.getLogger(__name__)
JsonType = JSON().with_variant(JSONB, "postgresql")


class PrematchRawBase(DeclarativeBase):
    """Base ORM aislada para el contrato pre-match v2."""


class RawResponse(PrematchRawBase):
    """Respuesta externa cruda persistida antes del parseo de dominio."""

    __tablename__ = "raw_responses"
    __table_args__ = (
        Index("ix_raw_responses_request_hash", "request_hash"),
        Index("ix_raw_responses_entity_cutoff", "entity_type", "entity_id", "cutoff_ts"),
        Index(
            "ix_raw_responses_snapshot_scope",
            "scope_event_id",
            "snapshot_bucket",
            "request_hash",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(100))
    scope_event_id: Mapped[str | None] = mapped_column(String(100))
    league_slug: Mapped[str | None] = mapped_column(String(100))
    season: Mapped[str | None] = mapped_column(String(30))
    snapshot_bucket: Mapped[str | None] = mapped_column(String(20))
    endpoint: Mapped[str] = mapped_column(String(500), nullable=False)
    request_params: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_json: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    response_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cutoff_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    kickoff_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    http_status: Mapped[int] = mapped_column(Integer, nullable=False)
    parser_version: Mapped[str] = mapped_column(String(50), nullable=False)
    capture_kind: Mapped[str] = mapped_column(String(40), nullable=False)


def canonical_hash(payload: Any) -> str:
    """Calcula SHA-256 sobre JSON canónico."""

    value = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def utc_datetime(value: datetime) -> datetime:
    """Normaliza un datetime a UTC y rechaza timestamps ingenuos."""

    if value.tzinfo is None:
        raise ValueError("timezone_required")
    return value.astimezone(timezone.utc)


class SqlAlchemyRawResponseRepository(RawResponseRepository):
    """Adaptador transaccional SQLAlchemy para ``raw_responses``."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        """Recibe una fábrica de sesiones inyectada."""

        self._session_factory = session_factory

    def store(self, command: RawResponseWrite) -> StoredRawResponse:
        """Persiste y confirma el payload crudo dentro de ``session.begin``."""

        values = self._values(command)
        try:
            stored = self._insert(values)
        except SQLAlchemyError:
            LOGGER.exception("raw_response_store_failed endpoint=%s", command.endpoint)
            raise
        LOGGER.info("raw_response_stored id=%s endpoint=%s", stored.id, command.endpoint)
        return stored

    def payload(self, response_id: int) -> dict[str, Any]:
        """Recupera el JSON crudo persistido por identificador."""

        with self._session_factory() as session:
            payload = session.execute(
                select(RawResponse.response_json).where(RawResponse.id == response_id)
            ).scalar_one()
        return dict(payload)

    def capture_exists(
        self,
        request_hash: str,
        scope_event_id: str,
        snapshot_bucket: str,
    ) -> bool:
        """Comprueba si el recurso del bucket ya fue persistido."""

        stmt = select(RawResponse.id).where(
            RawResponse.request_hash == request_hash,
            RawResponse.scope_event_id == scope_event_id,
            RawResponse.snapshot_bucket == snapshot_bucket,
        )
        with self._session_factory() as session:
            return session.execute(stmt).first() is not None

    def find_observation(
        self,
        request_hash: str,
        response_hash: str,
        fetched_at: datetime,
    ) -> StoredRawResponse | None:
        """Devuelve una observación exacta para reutilizar su fila."""

        stmt = select(RawResponse).where(
            RawResponse.request_hash == request_hash,
            RawResponse.response_hash == response_hash,
            RawResponse.fetched_at == fetched_at,
            RawResponse.snapshot_bucket.is_(None),
        )
        with self._session_factory() as session:
            row = session.execute(stmt).scalars().first()
        return _stored(row) if row is not None else None

    def _insert(self, values: dict[str, Any]) -> StoredRawResponse:
        """Inserta una fila y devuelve una confirmación desacoplada."""

        with self._session_factory() as session:
            with session.begin():
                row = RawResponse(**values)
                session.add(row)
                session.flush()
                result = StoredRawResponse(
                    id=row.id,
                    request_hash=row.request_hash,
                    response_hash=row.response_hash,
                    fetched_at=row.fetched_at,
                )
        return result

    @staticmethod
    def _values(command: RawResponseWrite) -> dict[str, Any]:
        """Convierte el comando validado a columnas ORM."""

        request = {"endpoint": command.endpoint, "params": command.request_params}
        return {
            **asdict(command),
            "entity_type": command.entity_type.value,
            "capture_kind": command.capture_kind.value,
            "request_hash": canonical_hash(request),
            "response_hash": canonical_hash(command.response_json),
            "fetched_at": utc_datetime(command.fetched_at),
            "available_at": _optional_utc(command.available_at),
            "cutoff_ts": _optional_utc(command.cutoff_ts),
            "kickoff_ts": _optional_utc(command.kickoff_ts),
        }


def _optional_utc(value: datetime | None) -> datetime | None:
    """Normaliza un timestamp opcional."""

    return utc_datetime(value) if value is not None else None


def _stored(row: RawResponse) -> StoredRawResponse:
    """Desacopla una fila ORM existente."""

    return StoredRawResponse(row.id, row.request_hash, row.response_hash, row.fetched_at)


# Version: 1.0.0
# Created: 2026-07-27
