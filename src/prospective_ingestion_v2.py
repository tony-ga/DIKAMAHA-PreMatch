"""Ingesta prospectiva ESPN en staging aislado y con escritura explícita.

La red requiere una fuente y manifiesto configurados. PostgreSQL sólo se toca
cuando el llamador habilita ``--enable-staging-write``; nunca se escriben las
tablas históricas. Los payloads crudos se guardan únicamente en staging.

Requirements:
    SQLAlchemy==2.0.41
    psycopg2-binary==2.9.10

Version: 2.0.0
Created: 2026-07-16
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

from src.api.espn_client import ESPNClient, ESPNClientError, _extract_competition_id
from src.espn_event_taxonomy import KNOWN_EVENTS, classify_play
from src.postgres_readonly_staging import sanitize_error

LOGGER = logging.getLogger(__name__)
STAGING_SCHEMA = "prospective_staging_v2"
PROVIDER = "espn"
FINAL_STATUSES = {"post", "final", "finished", "completed", "full_time"}
class ProspectiveIngestionV2Error(RuntimeError):
    """Error controlado de la ingesta prospectiva v2."""


@dataclass(frozen=True, slots=True)
class SourceReference:
    """Referencia estable a un evento y competición del proveedor."""

    provider_match_id: str
    competition_id: str
    league_slug: str = "esp.1"


@dataclass(frozen=True, slots=True)
class IngestionV2Config:
    """Configuración inmutable y saneada de una corrida v2."""

    source_enabled: bool
    write_enabled: bool
    source_manifest: str | None
    league: str = "esp.1"
    timeout_seconds: int = 20
    payload_max_bytes: int = 2_000_000
    version: str = "phase_7_14_prospective_ingestion_v1"


class Provider(Protocol):
    """Contrato mínimo de proveedor que permite pruebas sin red."""

    def fetch(self, reference: SourceReference) -> tuple[dict[str, Any], dict[str, Any], str]:
        """Obtiene detalle, timeline y timestamp UTC de descarga."""


def canonical_hash(value: Any) -> str:
    """Calcula un SHA-256 determinista para JSON serializable."""

    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def utc_now() -> str:
    """Devuelve tiempo UTC serializable sin microsegundos."""

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_utc(value: str) -> datetime:
    """Valida y normaliza un timestamp UTC recibido del proveedor."""

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProspectiveIngestionV2Error("timestamp_not_utc")
    return parsed.astimezone(timezone.utc)


def _team_id(value: Any) -> int | None:
    """Extrae un ID de equipo ESPN sin inferir valores ausentes."""

    if not isinstance(value, dict):
        return None
    direct = value.get("id")
    if isinstance(direct, (int, str)) and str(direct).isdigit():
        return int(direct)
    ref = str(value.get("$ref", ""))
    parsed = urlparse(ref)
    segments = [segment for segment in parsed.path.split("/") if segment]
    try:
        marker = segments.index("teams")
    except ValueError:
        return None
    candidate = segments[marker + 1] if marker + 1 < len(segments) else ""
    return int(candidate) if candidate.isdigit() else None


def team_ref_audit(value: Any) -> dict[str, Any]:
    """Audita path, query y nombre de un ref ESPN sin resolver URLs externas."""

    ref = str((value or {}).get("$ref", "")) if isinstance(value, dict) else ""
    parsed = urlparse(ref)
    segments = [segment for segment in parsed.path.split("/") if segment]
    marker = segments.index("teams") if "teams" in segments else -1
    candidate = segments[marker + 1] if marker >= 0 and marker + 1 < len(segments) else None
    name = None
    if isinstance(value, dict):
        name = value.get("displayName") or value.get("name") or value.get("shortDisplayName")
    return {"ref_present": bool(ref), "path_valid": bool(parsed.path and marker >= 0), "team_id": int(candidate) if candidate and candidate.isdigit() else None, "query_keys": sorted(item.split("=", 1)[0] for item in parsed.query.split("&") if item), "name": str(name) if isinstance(name, str) else None, "provenance": "provider_ref_path" if candidate and candidate.isdigit() else "unresolved"}


def normalize_match(payload: dict[str, Any], reference: SourceReference) -> dict[str, Any]:
    """Normaliza identidad, orientación y estado sin usar datos históricos."""

    competitions = payload.get("competitions")
    if not isinstance(competitions, list) or not competitions or not isinstance(competitions[0], dict):
        raise ProspectiveIngestionV2Error("malformed_event_competition")
    competition = competitions[0]
    competitors = competition.get("competitors")
    if not isinstance(competitors, list):
        raise ProspectiveIngestionV2Error("malformed_event_competitors")
    sides = {row.get("homeAway"): row for row in competitors if isinstance(row, dict)}
    home, away = sides.get("home"), sides.get("away")
    if not isinstance(home, dict) or not isinstance(away, dict):
        raise ProspectiveIngestionV2Error("missing_home_away_orientation")
    kickoff = str(payload.get("date") or competition.get("date") or "")
    kickoff_ts = parse_utc(kickoff).isoformat()
    status_type = ((competition.get("status") or {}).get("type") or {}) if isinstance(competition.get("status"), dict) else {}
    status = str(status_type.get("state") or status_type.get("name") or "unknown")
    return {"provider_match_id": reference.provider_match_id, "competition_id": reference.competition_id,
            "league_slug": reference.league_slug,
            "kickoff_ts": kickoff_ts, "home_provider_team_id": _team_id(home.get("team")),
            "away_provider_team_id": _team_id(away.get("team")), "home_score": _score(home.get("score")),
            "away_score": _score(away.get("score")), "provider_status": status.lower().removeprefix("status_")}


def _score(value: Any) -> int | None:
    """Convierte un marcador entero sin usar cero como imputación."""

    return int(value) if isinstance(value, (int, str)) and str(value).lstrip("-").isdigit() else None


def _clock_seconds(play: dict[str, Any]) -> int | None:
    """Lee el clock ESPN; la ausencia se rechaza en vez de imputarse."""

    value = (play.get("clock") or {}).get("value") if isinstance(play.get("clock"), dict) else None
    return int(value) if isinstance(value, (int, float)) and value >= 0 else None


def _event_type(play: dict[str, Any]) -> tuple[str, str | None]:
    """Conserva tipo bruto y normaliza sólo el vocabulario documentado."""

    return classify_play(play)


def normalize_events(
    plays: dict[str, Any], match: dict[str, Any], fetched_at: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """Normaliza eventos válidos y conserva inválidos como rejected_raw."""

    items = plays.get("items")
    if not isinstance(items, list):
        raise ProspectiveIngestionV2Error("malformed_plays_items")
    fetched = parse_utc(fetched_at)
    valid, rejected, seen = [], [], set()
    kickoff = parse_utc(match["kickoff_ts"])
    for index, play in enumerate(items):
        row, rejected_row = _normalize_event(play, index, match, kickoff, fetched)
        if rejected_row is not None:
            rejected.append(rejected_row)
        elif row is not None:
            key = (row["provider_event_id"], row["event_hash"])
            if key not in seen:
                seen.add(key)
                valid.append(row)
    return valid, rejected, {"input": len(items), "valid": len(valid), "rejected": len(rejected), "duplicates": len(items) - len(valid) - len(rejected)}


def _normalize_event(
    play: Any, index: int, match: dict[str, Any], kickoff: datetime, fetched: datetime
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Valida un evento y produce una fila válida o una fila rechazada."""

    if not isinstance(play, dict):
        return None, _rejected(match, index, play, "malformed_event")
    seconds = _clock_seconds(play)
    if seconds is None:
        return None, _rejected(match, index, play, "missing_or_invalid_event_clock")
    event_ts = kickoff + timedelta(seconds=seconds)
    if event_ts > fetched:
        return None, _rejected(match, index, play, "future_event_after_ingestion")
    event_type, raw = _event_type(play)
    event_id = str(play.get("id") or f"event_index:{index}")
    return {"provider_match_id": match["provider_match_id"], "provider_event_id": event_id,
            "event_index": index, "event_hash": canonical_hash(play), "event_ts": event_ts.isoformat(),
            "minute": seconds // 60, "second": seconds % 60, "team_provider_id": _team_id(play.get("team")),
            "event_type": event_type, "event_type_raw": raw, "annulled": bool(play.get("annulled", False)),
            "raw_data": play}, None


def _rejected(match: dict[str, Any], index: int, raw: Any, reason: str) -> dict[str, Any]:
    """Describe un raw inválido sin divulgarlo en artefactos externos."""

    return {"provider_match_id": match["provider_match_id"], "event_index": index,
            "raw_hash": canonical_hash(raw), "reason": reason, "raw_data": raw}


def build_batch(
    reference: SourceReference, event: dict[str, Any], plays: dict[str, Any], fetched_at: str
) -> dict[str, Any]:
    """Construye un lote staging con payload crudo, eventos y rechazos."""

    if reference.provider_match_id == "704766":
        raise ProspectiveIngestionV2Error("blocked_match_704766")
    identity = normalize_match(event, reference)
    if identity["home_provider_team_id"] is None or identity["away_provider_team_id"] is None:
        raise ProspectiveIngestionV2Error("missing_provider_team_id")
    events, rejected, audit = normalize_events(plays, identity, fetched_at)
    identity["complete"] = identity["provider_status"] in FINAL_STATUSES and all(
        identity[key] is not None for key in ("home_score", "away_score")
    )
    raw = [{"endpoint": "event", "payload": event}, {"endpoint": "plays", "payload": plays}]
    return {"identity": identity, "events": events, "rejected": rejected, "event_audit": audit,
            "raw_payloads": [{**item, "payload_hash": canonical_hash(item["payload"])} for item in raw],
            "fetched_at": parse_utc(fetched_at).isoformat()}


class EspnProvider:
    """Adaptador ESPN explícitamente invocado por el runner de staging."""

    def __init__(self, config: IngestionV2Config) -> None:
        """Configura cliente existente con timeout y caché local."""

        self._client = ESPNClient(config.league, timeout_seconds=config.timeout_seconds)
        self._limit = 300

    def fetch(self, reference: SourceReference) -> tuple[dict[str, Any], dict[str, Any], str]:
        """Obtiene sólo endpoints documentados y valida competición declarada."""

        event = self._client.get_event(reference.provider_match_id)
        if _extract_competition_id(event) != reference.competition_id:
            raise ProspectiveIngestionV2Error("provider_competition_id_mismatch")
        plays = self._client.get_play_by_play_all(reference.provider_match_id, reference.competition_id, self._limit)
        return event, plays, utc_now()


class StagingV2Repository:
    """Persistencia SQLAlchemy restringida físicamente a staging v2."""

    def __init__(self, database_url: str, *, write_enabled: bool) -> None:
        """Construye metadata sólo cuando el llamador autorizó escritura staging."""

        if not write_enabled:
            raise ProspectiveIngestionV2Error("staging_write_flag_required")
        from sqlalchemy import MetaData, create_engine

        self._engine = create_engine(database_url, future=True, pool_pre_ping=True)
        self._metadata = MetaData(schema=STAGING_SCHEMA)
        self._tables = self._define_tables()

    def _define_tables(self) -> dict[str, Any]:
        """Define sólo las seis tablas autorizadas para esta zona aislada."""

        from sqlalchemy import Boolean, Column, DateTime, Integer, JSON, String, Table, UniqueConstraint

        run = Table("ingestion_runs", self._metadata, Column("id", Integer, primary_key=True),
                    Column("run_hash", String(64), unique=True, nullable=False), Column("status", String(50), nullable=False),
                    Column("created_at", DateTime(timezone=True), nullable=False))
        raw = Table("raw_payloads", self._metadata, Column("id", Integer, primary_key=True), Column("provider", String(30), nullable=False),
                    Column("provider_match_id", String(80), nullable=False), Column("endpoint", String(20), nullable=False),
                    Column("payload_hash", String(64), nullable=False), Column("payload", JSON, nullable=False),
                    Column("fetched_at", DateTime(timezone=True), nullable=False),
                    UniqueConstraint("provider", "provider_match_id", "endpoint", "payload_hash"))
        matches = Table("matches", self._metadata, Column("id", Integer, primary_key=True), Column("provider", String(30), nullable=False),
                        Column("provider_match_id", String(80), nullable=False), Column("competition_id", String(80), nullable=False),
                        Column("league_slug", String(80), nullable=False, default="esp.1"),
                        Column("kickoff_ts", DateTime(timezone=True), nullable=False), Column("home_provider_team_id", Integer, nullable=False),
                        Column("away_provider_team_id", Integer, nullable=False), Column("home_score", Integer), Column("away_score", Integer),
                        Column("provider_status", String(40), nullable=False), Column("complete", Boolean, nullable=False),
                        Column("updated_at", DateTime(timezone=True), nullable=False), UniqueConstraint("provider", "provider_match_id"))
        events = Table("events", self._metadata, Column("id", Integer, primary_key=True), Column("provider", String(30), nullable=False),
                       Column("provider_match_id", String(80), nullable=False), Column("provider_event_id", String(100), nullable=False),
                       Column("event_index", Integer, nullable=False), Column("event_hash", String(64), nullable=False),
                       Column("event_ts", DateTime(timezone=True), nullable=False), Column("minute", Integer, nullable=False), Column("second", Integer, nullable=False),
                       Column("team_provider_id", Integer), Column("event_type", String(60), nullable=False), Column("event_type_raw", String(120)),
                       Column("annulled", Boolean, nullable=False), Column("raw_data", JSON, nullable=False),
                       UniqueConstraint("provider", "provider_match_id", "provider_event_id", "event_hash"))
        rejected = Table("rejected_raw_events", self._metadata, Column("id", Integer, primary_key=True), Column("provider", String(30), nullable=False),
                         Column("provider_match_id", String(80), nullable=False), Column("event_index", Integer, nullable=False),
                         Column("raw_hash", String(64), nullable=False), Column("reason", String(100), nullable=False), Column("raw_data", JSON, nullable=False),
                         UniqueConstraint("provider", "provider_match_id", "event_index", "raw_hash", "reason"))
        audit = Table("write_audit", self._metadata, Column("id", Integer, primary_key=True), Column("run_hash", String(64), nullable=False),
                      Column("table_name", String(80), nullable=False), Column("inserted_rows", Integer, nullable=False),
                      Column("created_at", DateTime(timezone=True), nullable=False))
        return {"runs": run, "raw": raw, "matches": matches, "events": events, "rejected": rejected, "audit": audit}

    def prepare(self) -> None:
        """Crea sólo el schema y las tablas aisladas durante una escritura autorizada."""

        from sqlalchemy import text

        with self._engine.begin() as connection:
            connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {STAGING_SCHEMA}"))
            self._metadata.create_all(connection)

    def counts(self) -> dict[str, int]:
        """Cuenta tablas v2 sin tocar entidades históricas."""

        from sqlalchemy import func, select

        with self._engine.connect() as connection:
            return {name: int(connection.execute(select(func.count()).select_from(table)).scalar_one())
                    for name, table in self._tables.items()}

    def store(self, batch: dict[str, Any]) -> dict[str, int]:
        """Persiste una corrida en una única transacción idempotente."""

        from sqlalchemy.dialects.postgresql import insert
        from sqlalchemy import select

        now = parse_utc(utc_now())
        run_hash = canonical_hash({"identity": batch["identity"], "raw": [row["payload_hash"] for row in batch["raw_payloads"]]})
        with self._engine.begin() as connection:
            exists = connection.execute(select(self._tables["runs"].c.id).where(self._tables["runs"].c.run_hash == run_hash)).first()
            if exists is not None:
                return {"matches": 0, "raw": 0, "events": 0, "rejected": 0, "audit": 0, "runs": 0}
            connection.execute(insert(self._tables["runs"]).values(run_hash=run_hash, status="success", created_at=now).on_conflict_do_nothing())
            inserted = self._store_rows(connection, insert, batch, now)
            audits = [{"run_hash": run_hash, "table_name": key, "inserted_rows": value, "created_at": now} for key, value in inserted.items()]
            connection.execute(insert(self._tables["audit"]).values(audits))
        return inserted

    def _store_rows(self, connection: Any, insert: Any, batch: dict[str, Any], now: datetime) -> dict[str, int]:
        """Hace upsert de match e insert idempotente de raws/eventos/rechazos."""

        identity = {**batch["identity"], "provider": PROVIDER, "kickoff_ts": parse_utc(batch["identity"]["kickoff_ts"]), "updated_at": now}
        statement = insert(self._tables["matches"]).values(**identity).on_conflict_do_update(
            index_elements=["provider", "provider_match_id"], set_=identity)
        connection.execute(statement)
        rows = {"raw": [{**row, "provider": PROVIDER, "provider_match_id": identity["provider_match_id"], "fetched_at": parse_utc(batch["fetched_at"])} for row in batch["raw_payloads"]],
                "events": [{**row, "provider": PROVIDER, "event_ts": parse_utc(row["event_ts"])} for row in batch["events"]],
                "rejected": [{**row, "provider": PROVIDER} for row in batch["rejected"]]}
        return {"matches": 1, **{name: self._insert_rows(connection, insert, name, value) for name, value in rows.items()}}

    def _insert_rows(self, connection: Any, insert: Any, name: str, rows: list[dict[str, Any]]) -> int:
        """Inserta filas staging sin sobrescribir datos prospectivos previos."""

        if not rows:
            return 0
        result = connection.execute(insert(self._tables[name]).values(rows).on_conflict_do_nothing())
        return max(result.rowcount, 0)

    def close(self) -> None:
        """Cierra el pool local después de completar o revertir la corrida."""

        self._engine.dispose()

    def store_many(self, batches: list[dict[str, Any]]) -> dict[str, int]:
        """Persiste refrescos en una sola transacción y fusiona eventos corregidos."""

        from sqlalchemy import select, update
        from sqlalchemy.dialects.postgresql import insert

        now = parse_utc(utc_now())
        totals = {"matches": 0, "raw": 0, "events": 0, "events_inserted": 0, "events_updated": 0, "rejected": 0, "audit": 0, "runs": 0}
        with self._engine.begin() as connection:
            for batch in batches:
                run_hash = canonical_hash({"identity": batch["identity"], "raw": [row["payload_hash"] for row in batch["raw_payloads"]]})
                exists = connection.execute(select(self._tables["runs"].c.id).where(self._tables["runs"].c.run_hash == run_hash)).first()
                if exists is not None:
                    continue
                connection.execute(insert(self._tables["runs"]).values(run_hash=run_hash, status="success", created_at=now).on_conflict_do_nothing())
                identity = {**batch["identity"], "provider": PROVIDER, "kickoff_ts": parse_utc(batch["identity"]["kickoff_ts"]), "updated_at": now}
                connection.execute(insert(self._tables["matches"]).values(**identity).on_conflict_do_update(index_elements=["provider", "provider_match_id"], set_=identity))
                totals["matches"] += 1
                raw_rows = [{**row, "provider": PROVIDER, "provider_match_id": identity["provider_match_id"], "fetched_at": parse_utc(batch["fetched_at"])} for row in batch["raw_payloads"]]
                totals["raw"] += self._insert_rows(connection, insert, "raw", raw_rows)
                totals["rejected"] += self._insert_rows(connection, insert, "rejected", [{**row, "provider": PROVIDER} for row in batch["rejected"]])
                existing = {str(row.provider_event_id): row for row in connection.execute(select(self._tables["events"]).where(self._tables["events"].c.provider == PROVIDER, self._tables["events"].c.provider_match_id == identity["provider_match_id"])).fetchall()}
                new_rows = []
                for row in batch["events"]:
                    previous = existing.get(str(row["provider_event_id"]))
                    if previous is None:
                        new_rows.append({**row, "provider": PROVIDER, "event_ts": parse_utc(row["event_ts"])})
                    elif previous.event_hash != row["event_hash"]:
                        values = {**row, "provider": PROVIDER, "event_ts": parse_utc(row["event_ts"])}
                        connection.execute(update(self._tables["events"]).where(self._tables["events"].c.id == previous.id).values(**values))
                        totals["events_updated"] += 1
                totals["events_inserted"] += self._insert_rows(connection, insert, "events", new_rows)
                totals["events"] = totals["events_inserted"] + totals["events_updated"]
                audits = [{"run_hash": run_hash, "table_name": key, "inserted_rows": value, "created_at": now} for key, value in {"matches": 1, "raw": len(raw_rows), "events": totals["events_updated"] + len(new_rows), "rejected": len(batch["rejected"]) }.items()]
                connection.execute(insert(self._tables["audit"]).values(audits))
                totals["audit"] += len(audits)
                totals["runs"] += 1
        return totals


def frozen_config(config: IngestionV2Config) -> dict[str, Any]:
    """Expone configuración sin URL, credenciales ni rutas sensibles."""

    return {**asdict(config), "source_manifest": bool(config.source_manifest), "staging_schema": STAGING_SCHEMA,
            "database_url_present": bool(os.getenv("DATABASE_URL")), "api_key_present": bool(os.getenv("API_KEY")),
            "network_calls_permitted": config.source_enabled, "staging_writes_permitted": config.write_enabled}


def load_references(path: Path) -> list[SourceReference]:
    """Carga el manifiesto explícito sin descubrir fixtures ni endpoints."""

    rows = json.loads(path.read_text(encoding="utf-8"))
    rows = rows.get("matches", rows) if isinstance(rows, dict) else rows
    if not isinstance(rows, list):
        raise ProspectiveIngestionV2Error("malformed_source_manifest")
    return [SourceReference(str(row["provider_match_id"]), str(row["competition_id"]), str(row.get("league_slug", "esp.1"))) for row in rows]


def sanitized_error(error: BaseException) -> str:
    """Sanea diagnósticos para impedir exposición de credenciales."""

    return sanitize_error(error, os.getenv("DATABASE_URL"))


# Version: 2.0.0
# Created: 2026-07-16
