"""Ingesta prospectiva aislada en PostgreSQL para DIKAMAHA.

La fuente ESPN no se consulta ni se escribe en PostgreSQL sin las banderas
explícitas de operación. Las tablas viven en el schema ``prospective_staging``
y nunca referencian ni modifican las tablas históricas.

Requirements:
    - SQLAlchemy==2.0.41
    - psycopg2-binary==2.9.10

Version: 1.0.0
Created: 2026-07-16
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from src.api.espn_client import ESPNClient, ESPNClientError, _extract_competition_id
from src.espn_event_taxonomy import classify_event_type, normalize_raw_type
from src.postgres_readonly_staging import sanitize_error

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
STAGING_SCHEMA = "prospective_staging"
STAGING_TABLES = (
    "ingestion_runs", "raw_payloads", "matches", "events", "write_audit",
)
FINAL_STATUSES = {"post", "final", "finished", "completed", "full_time"}


class ProspectiveIngestionError(RuntimeError):
    """Error controlado de contrato, proveedor o persistencia staging."""


@dataclass(frozen=True, slots=True)
class IngestionConfig:
    """Configuración congelada para una corrida prospectiva."""

    version: str = "prospective_staging_ingestion_v1"
    provider: str = "espn"
    league: str = "esp.1"
    cutoff_ts: str = "2025-10-26T15:15:00+00:00"
    timeout_seconds: int = 20
    payload_max_bytes: int = 2_000_000
    page_limit: int = 300
    source_enabled: bool = False
    staging_write_enabled: bool = False
    dry_run: bool = True


@dataclass(frozen=True, slots=True)
class SourceMatchRef:
    """Referencia explícita y estable a un evento del proveedor."""

    provider_match_id: str
    competition_id: str


@dataclass(frozen=True, slots=True)
class FetchedMatch:
    """Payloads crudos de detalle y play-by-play de un partido."""

    reference: SourceMatchRef
    event_payload: dict[str, Any]
    plays_payload: dict[str, Any]
    fetched_at: str


class ProspectiveProvider(ABC):
    """Puerto para proveedores de fixtures y play-by-play."""

    @abstractmethod
    def fetch(self, reference: SourceMatchRef) -> FetchedMatch:
        """Obtiene un partido y sus eventos sin persistirlos."""


class StagingRepository(ABC):
    """Puerto de escritura idempotente limitado a staging."""

    @abstractmethod
    def prepare(self) -> None:
        """Crea exclusivamente el schema y tablas staging autorizadas."""

    @abstractmethod
    def counts(self) -> dict[str, int]:
        """Devuelve conteos únicamente de tablas staging."""

    @abstractmethod
    def store(self, batch: dict[str, Any]) -> dict[str, int]:
        """Persiste un lote de forma atómica e idempotente."""

    @abstractmethod
    def close(self) -> None:
        """Libera recursos de persistencia."""


class EspnProspectiveProvider(ProspectiveProvider):
    """Adaptador ESPN basado sólo en endpoints documentados localmente."""

    def __init__(self, config: IngestionConfig) -> None:
        """Inicializa cliente con timeout, cache y límite de página configurados."""

        self._config = config
        self._client = ESPNClient(
            league=config.league, timeout_seconds=config.timeout_seconds,
            cache_dir=ROOT / "data" / "cache" / "prospective_staging",
        )

    def fetch(self, reference: SourceMatchRef) -> FetchedMatch:
        """Descarga detalle y play-by-play de ESPN con controles de tamaño."""

        event = self._client.get_event(reference.provider_match_id)
        competition = _extract_competition_id(event)
        if competition != reference.competition_id:
            raise ProspectiveIngestionError("provider_competition_id_mismatch")
        plays = self._client.get_play_by_play_all(
            reference.provider_match_id, reference.competition_id, self._config.page_limit,
        )
        _validate_payload_size(event, self._config.payload_max_bytes)
        _validate_payload_size(plays, self._config.payload_max_bytes)
        return FetchedMatch(reference, event, plays, _utc_now())


def _utc_now() -> str:
    """Devuelve un timestamp UTC serializable sin microsegundos."""

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _canonical_hash(payload: Any) -> str:
    """Calcula hash estable de JSON para deduplicación y provenance."""

    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _validate_payload_size(payload: dict[str, Any], maximum: int) -> None:
    """Rechaza payloads que exceden el límite explícito de staging."""

    size = len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    if size > maximum:
        raise ProspectiveIngestionError("payload_size_limit_exceeded")


def _extract_id(reference: Any, token: str) -> int | None:
    """Extrae un ID numérico de una referencia ESPN sin resolver URLs."""

    text = str(reference or "")
    marker = f"/{token}/"
    if marker not in text:
        return None
    value = text.split(marker, 1)[1].split("?", 1)[0].split("/", 1)[0]
    return int(value) if value.isdigit() else None


def _extract_match_identity(payload: dict[str, Any], reference: SourceMatchRef) -> dict[str, Any]:
    """Valida orientación, kickoff UTC y equipos sin inferir valores ausentes."""

    competitions = payload.get("competitions")
    if not isinstance(competitions, list) or not competitions or not isinstance(competitions[0], dict):
        raise ProspectiveIngestionError("malformed_event_competition")
    competition = competitions[0]
    competitors = competition.get("competitors")
    if not isinstance(competitors, list):
        raise ProspectiveIngestionError("malformed_event_competitors")
    by_side = {str(row.get("homeAway")): row for row in competitors if isinstance(row, dict)}
    home, away = by_side.get("home"), by_side.get("away")
    if home is None or away is None:
        raise ProspectiveIngestionError("missing_home_away_orientation")
    kickoff = payload.get("date") or competition.get("date")
    if not isinstance(kickoff, str) or not kickoff.endswith(("Z", "+00:00")):
        raise ProspectiveIngestionError("kickoff_not_utc")
    status = str((competition.get("status") or {}).get("type", {}).get("name") or "unknown").lower()
    return {
        "provider_match_id": reference.provider_match_id, "competition_id": reference.competition_id,
        "kickoff_ts": kickoff.replace("Z", "+00:00"),
        "home_provider_team_id": _extract_id((home.get("team") or {}).get("$ref"), "teams"),
        "away_provider_team_id": _extract_id((away.get("team") or {}).get("$ref"), "teams"),
        "home_score": _integer_or_none(home.get("score")), "away_score": _integer_or_none(away.get("score")),
        "provider_status": status.removeprefix("status_"),
    }


def _integer_or_none(value: Any) -> int | None:
    """Convierte enteros de marcador sin imputar valores inválidos."""

    return int(value) if isinstance(value, (int, str)) and str(value).lstrip("-").isdigit() else None


def _event_rows(fetched: FetchedMatch, identity: dict[str, Any]) -> list[dict[str, Any]]:
    """Normaliza eventos manteniendo anulados, desconocidos y team_id nulo."""

    raw_plays = fetched.plays_payload.get("items")
    if not isinstance(raw_plays, list):
        raise ProspectiveIngestionError("malformed_plays_items")
    rows = []
    for index, play in enumerate(raw_plays):
        if not isinstance(play, dict):
            continue
        minute, second = _clock(play.get("clock"))
        if minute < 0 or second < 0 or second >= 60:
            raise ProspectiveIngestionError("invalid_event_clock")
        raw_hash = _canonical_hash(play)
        rows.append({
            "provider_match_id": fetched.reference.provider_match_id, "event_index": index,
            "event_hash": raw_hash, "minute": minute, "second": second,
            "team_provider_id": _event_team_id(play),
            "event_type": _event_type(play), "event_type_raw": _raw_event_type(play),
            "annulled": bool(play.get("annulled", False)), "raw_data": play,
            "event_ts": _event_timestamp(identity["kickoff_ts"], minute, second),
        })
    return _deduplicate_events(rows)


def _clock(value: Any) -> tuple[int, int]:
    """Convierte el reloj ESPN en minuto y segundo sin depender del ORM histórico."""

    seconds = value.get("value") if isinstance(value, dict) else None
    if not isinstance(seconds, (int, float)) or seconds < 0:
        return 0, 0
    return int(seconds) // 60, int(seconds) % 60


def _raw_event_type(play: dict[str, Any]) -> str | None:
    """Extrae el tipo original de ESPN, conservando desconocidos como provenance."""

    value = play.get("type")
    if not isinstance(value, dict):
        return None
    raw = value.get("type") or value.get("text")
    return normalize_raw_type(raw)


def _event_type(play: dict[str, Any]) -> str:
    """Mapea sólo eventos conocidos; el resto queda explícitamente unclassified."""

    raw = _raw_event_type(play)
    return classify_event_type(raw, play.get("scoringPlay") is True)


def _event_team_id(play: dict[str, Any]) -> int | None:
    """Obtiene sólo el equipo explícito del evento, sin imputar team_id nulo."""

    team = play.get("team")
    return _extract_id(team.get("$ref"), "teams") if isinstance(team, dict) else None


def _event_timestamp(kickoff: str, minute: int, second: int) -> str:
    """Construye event_ts UTC exclusivamente desde kickoff y reloj del proveedor."""

    parsed = datetime.fromisoformat(kickoff.replace("Z", "+00:00"))
    return (parsed + timedelta(minutes=minute, seconds=second)).isoformat()


def _as_utc(value: str) -> datetime:
    """Convierte un timestamp ISO UTC en datetime para la capa SQLAlchemy."""

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProspectiveIngestionError("timestamp_not_utc")
    return parsed.astimezone(timezone.utc)


def _deduplicate_events(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplica por proveedor, índice y hash de manera estable."""

    seen: set[tuple[str, int, str]] = set()
    output = []
    for row in rows:
        key = (row["provider_match_id"], row["event_index"], row["event_hash"])
        if key not in seen:
            seen.add(key)
            output.append(row)
    return output


def match_complete(identity: dict[str, Any], events: list[dict[str, Any]]) -> bool:
    """Marca cierre sólo con estado final, score completo y timeline recibido."""

    return (
        identity["provider_status"] in FINAL_STATUSES
        and identity["home_score"] is not None and identity["away_score"] is not None
        and isinstance(events, list)
    )


def build_batch(fetched: FetchedMatch) -> dict[str, Any]:
    """Crea un lote validado sin escribir ni evaluar señales o modelos."""

    if fetched.reference.provider_match_id == "704766":
        raise ProspectiveIngestionError("blocked_match_704766")
    identity = _extract_match_identity(fetched.event_payload, fetched.reference)
    if identity["home_provider_team_id"] is None or identity["away_provider_team_id"] is None:
        raise ProspectiveIngestionError("missing_provider_team_id")
    events = _event_rows(fetched, identity)
    fetched_at = _as_utc(fetched.fetched_at)
    if any(_as_utc(row["event_ts"]) > fetched_at for row in events):
        raise ProspectiveIngestionError("future_event_after_ingestion")
    return {
        "identity": {**identity, "complete": match_complete(identity, events)}, "events": events,
        "raw_payloads": [
            _raw_payload_row(fetched, "event", fetched.event_payload),
            _raw_payload_row(fetched, "plays", fetched.plays_payload),
        ], "fetched_at": fetched.fetched_at,
    }


def _raw_payload_row(fetched: FetchedMatch, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Describe un payload crudo sin incluirlo en logs o artefactos públicos."""

    return {"provider_match_id": fetched.reference.provider_match_id, "endpoint": endpoint,
            "payload": payload, "payload_hash": _canonical_hash(payload), "fetched_at": fetched.fetched_at}


def source_config_from_env() -> IngestionConfig:
    """Lee configuración sin registrar credenciales ni activar la fuente por defecto."""

    return IngestionConfig(
        league=os.getenv("DIKAMAHA_PROSPECTIVE_ESPN_LEAGUE", "esp.1"),
        source_enabled=os.getenv("DIKAMAHA_PROSPECTIVE_SOURCE_ENABLED", "false").lower() == "true",
        staging_write_enabled=os.getenv("DIKAMAHA_PROSPECTIVE_STAGING_WRITE_ENABLED", "false").lower() == "true",
        dry_run=os.getenv("DIKAMAHA_PROSPECTIVE_DRY_RUN", "true").lower() != "false",
    )


class SqlAlchemyStagingRepository(StagingRepository):
    """Persistencia PostgreSQL limitada físicamente al schema staging."""

    def __init__(self, database_url: str) -> None:
        """Construye metadata sin abrir ni exponer la URL de conexión."""

        from sqlalchemy import MetaData, create_engine

        self._engine = create_engine(database_url, future=True, pool_pre_ping=True)
        self._metadata = MetaData(schema=STAGING_SCHEMA)
        self._tables = self._define_tables()

    def _define_tables(self) -> dict[str, Any]:
        """Define las únicas tablas que este repositorio puede escribir."""

        from sqlalchemy import Boolean, Column, DateTime, Integer, JSON, String, Table, Text, UniqueConstraint

        run = Table("ingestion_runs", self._metadata, Column("id", Integer, primary_key=True),
                    Column("run_hash", String(64), nullable=False, unique=True), Column("status", String(40), nullable=False),
                    Column("created_at", DateTime(timezone=True), nullable=False))
        raw = Table("raw_payloads", self._metadata, Column("id", Integer, primary_key=True),
                    Column("provider", String(40), nullable=False), Column("provider_match_id", String(80), nullable=False),
                    Column("endpoint", String(40), nullable=False), Column("payload_hash", String(64), nullable=False),
                    Column("payload", JSON, nullable=False), Column("fetched_at", DateTime(timezone=True), nullable=False),
                    UniqueConstraint("provider", "provider_match_id", "endpoint", "payload_hash"))
        matches = Table("matches", self._metadata, Column("id", Integer, primary_key=True),
                        Column("provider", String(40), nullable=False), Column("provider_match_id", String(80), nullable=False),
                        Column("competition_id", String(80), nullable=False), Column("kickoff_ts", DateTime(timezone=True), nullable=False),
                        Column("home_provider_team_id", Integer, nullable=False), Column("away_provider_team_id", Integer, nullable=False),
                        Column("home_score", Integer), Column("away_score", Integer), Column("provider_status", String(40), nullable=False),
                        Column("complete", Boolean, nullable=False), Column("updated_at", DateTime(timezone=True), nullable=False),
                        UniqueConstraint("provider", "provider_match_id"))
        events = Table("events", self._metadata, Column("id", Integer, primary_key=True),
                       Column("provider", String(40), nullable=False), Column("provider_match_id", String(80), nullable=False),
                       Column("event_index", Integer, nullable=False), Column("event_hash", String(64), nullable=False),
                       Column("event_ts", DateTime(timezone=True), nullable=False), Column("minute", Integer, nullable=False),
                       Column("second", Integer, nullable=False), Column("team_provider_id", Integer), Column("event_type", String(60), nullable=False),
                       Column("event_type_raw", String(120)), Column("annulled", Boolean, nullable=False), Column("raw_data", JSON, nullable=False),
                       UniqueConstraint("provider", "provider_match_id", "event_index", "event_hash"))
        audit = Table("write_audit", self._metadata, Column("id", Integer, primary_key=True),
                      Column("run_hash", String(64), nullable=False), Column("table_name", String(80), nullable=False),
                      Column("inserted_rows", Integer, nullable=False), Column("created_at", DateTime(timezone=True), nullable=False))
        return {"runs": run, "raw": raw, "matches": matches, "events": events, "audit": audit}

    def prepare(self) -> None:
        """Crea el schema aislado y sus tablas, sólo bajo operación explícita."""

        from sqlalchemy import text

        with self._engine.begin() as connection:
            connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {STAGING_SCHEMA}"))
            self._metadata.create_all(connection)

    def counts(self) -> dict[str, int]:
        """Obtiene conteos de staging sin consultar tablas históricas."""

        from sqlalchemy import func, select

        with self._engine.connect() as connection:
            return {name: int(connection.execute(select(func.count()).select_from(table)).scalar_one())
                    for name, table in self._tables.items()}

    def store(self, batch: dict[str, Any]) -> dict[str, int]:
        """Hace upsert idempotente de un lote dentro de una transacción única."""

        from sqlalchemy.dialects.postgresql import insert

        identity, now = batch["identity"], _utc_now()
        run_hash = _canonical_hash({"identity": identity, "raw": [row["payload_hash"] for row in batch["raw_payloads"]]})
        with self._engine.begin() as connection:
            self._insert_run(connection, insert, run_hash, now)
            inserted = self._upsert_rows(connection, insert, batch, now)
            self._audit_writes(connection, insert, run_hash, inserted, now)
        return inserted

    def _insert_run(self, connection: Any, insert: Any, run_hash: str, now: str) -> None:
        """Registra la corrida sin permitir duplicación por hash lógico."""

        stmt = insert(self._tables["runs"]).values(run_hash=run_hash, status="success", created_at=_as_utc(now))
        connection.execute(stmt.on_conflict_do_nothing(index_elements=["run_hash"]))

    def _upsert_rows(self, connection: Any, insert: Any, batch: dict[str, Any], now: str) -> dict[str, int]:
        """Inserta raw/eventos y actualiza únicamente el match staging correspondiente."""

        provider = "espn"
        match = {**batch["identity"], "provider": provider, "kickoff_ts": _as_utc(batch["identity"]["kickoff_ts"]), "updated_at": _as_utc(now)}
        stmt = insert(self._tables["matches"]).values(**match).on_conflict_do_update(
            index_elements=["provider", "provider_match_id"], set_=match,
        )
        connection.execute(stmt)
        raw_rows = [{**row, "provider": provider, "fetched_at": _as_utc(row["fetched_at"])} for row in batch["raw_payloads"]]
        event_rows = [{**row, "provider": provider, "event_ts": _as_utc(row["event_ts"])} for row in batch["events"]]
        raw_count = self._insert_many(connection, insert, "raw", raw_rows)
        event_count = self._insert_many(connection, insert, "events", event_rows)
        return {"matches": 1, "raw": raw_count, "events": event_count}

    def _insert_many(self, connection: Any, insert: Any, name: str, rows: list[dict[str, Any]]) -> int:
        """Inserta múltiples filas con la única llave idempotente de su tabla."""

        if not rows:
            return 0
        table = self._tables[name]
        result = connection.execute(insert(table).values(rows).on_conflict_do_nothing())
        return max(result.rowcount, 0)

    def _audit_writes(self, connection: Any, insert: Any, run_hash: str, inserted: dict[str, int], now: str) -> None:
        """Registra auditoría de escritura sin guardar payloads ni secretos."""

        rows = [{"run_hash": run_hash, "table_name": key, "inserted_rows": value, "created_at": _as_utc(now)}
                for key, value in inserted.items()]
        connection.execute(insert(self._tables["audit"]).values(rows))

    def close(self) -> None:
        """Cierra el pool SQLAlchemy al finalizar la corrida."""

        self._engine.dispose()


def frozen_config_payload(config: IngestionConfig) -> dict[str, Any]:
    """Expone configuración segura para artefactos sin variables sensibles."""

    return {**asdict(config), "database_url_present": bool(os.getenv("DATABASE_URL")),
            "api_key_present": bool(os.getenv("API_KEY")), "staging_schema": STAGING_SCHEMA,
            "network_calls_permitted": config.source_enabled and not config.dry_run}


# Version: 1.0.0
# Created: 2026-07-16
