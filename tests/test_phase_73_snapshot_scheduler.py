"""Pruebas temporales e idempotentes del colector multicutoff."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from src.espn_prospective_connector import EspnConnectorConfig, EspnProspectiveConnector
from src.espn_raw_first_provider import DuplicateSnapshotError, EspnRawFirstProvider
from src.prematch_data_contracts import EntityType
from src.prematch_raw_store import (
    PrematchRawBase,
    RawResponse,
    SqlAlchemyRawResponseRepository,
)
from src.prematch_snapshot_scheduler import due_jobs, fixtures_from_scoreboard


class _Response:
    """Respuesta sintética válida."""

    status_code = 200

    def raise_for_status(self) -> None:
        """Conserva estado exitoso."""

    def json(self) -> dict[str, Any]:
        """Devuelve un payload mínimo."""

        return {"items": []}


class _Session:
    """Sesión HTTP contable sin red."""

    def __init__(self) -> None:
        """Inicializa headers y contador."""

        self.headers: dict[str, str] = {}
        self.calls = 0

    def get(self, url: str, **kwargs: Any) -> _Response:
        """Cuenta solicitudes realizadas."""

        self.calls += 1
        return _Response()


def _payload(kickoff: datetime) -> dict[str, Any]:
    """Construye scoreboard programado con equipos orientados."""

    return {
        "events": [{
            "id": "10",
            "date": kickoff.isoformat(),
            "status": {"type": {"state": "pre", "completed": False}},
            "competitions": [{
                "id": "20",
                "date": kickoff.isoformat(),
                "competitors": [
                    {"homeAway": "home", "team": {"id": "1"}},
                    {"homeAway": "away", "team": {"id": "2"}},
                ],
            }],
        }],
    }


@pytest.mark.parametrize(
    ("hours", "bucket"),
    [(168, "T-168h"), (93, "T-72h"), (30, "T-24h"), (8, "T-6h"), (2, "T-90m")],
)
def test_due_jobs_assign_real_window(hours: float, bucket: str) -> None:
    """Cada distancia cae en un único bucket nominal."""

    now = datetime(2026, 7, 27, 12, tzinfo=timezone.utc)
    fixtures = fixtures_from_scoreboard(_payload(now + timedelta(hours=hours)), "mex.1")
    jobs = due_jobs(fixtures, now)
    assert len(jobs) == 1
    assert jobs[0].bucket.name == bucket


def test_completed_or_live_events_are_excluded() -> None:
    """El scheduler nunca agenda eventos target ya iniciados."""

    now = datetime(2026, 7, 27, 12, tzinfo=timezone.utc)
    payload = _payload(now + timedelta(hours=2))
    payload["events"][0]["status"]["type"]["state"] = "in"
    assert fixtures_from_scoreboard(payload, "mex.1") == []


def test_duplicate_bucket_avoids_second_network_call(tmp_path: Path) -> None:
    """La idempotencia se resuelve antes de consultar ESPN."""

    now = datetime(2026, 7, 27, 12, tzinfo=timezone.utc)
    engine = create_engine("sqlite+pysqlite:///:memory:")
    PrematchRawBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    repository = SqlAlchemyRawResponseRepository(factory)
    http = _Session()
    config = EspnConnectorConfig(cache_dir=tmp_path, cache_ttl_seconds=0)
    connector = EspnProspectiveConnector(config, http, lambda: now)  # type: ignore[arg-type]
    provider = EspnRawFirstProvider(connector, repository)
    arguments = {
        "entity_type": EntityType.EVENT,
        "scope_event_id": "10",
        "snapshot_bucket": "T-72h",
        "kickoff_ts": now + timedelta(hours=72),
        "event_id": "10",
        "competition_id": "20",
    }
    provider.fetch("odds", **arguments)  # type: ignore[arg-type]
    with pytest.raises(DuplicateSnapshotError):
        provider.fetch("odds", **arguments)  # type: ignore[arg-type]
    assert http.calls == 1


def test_effective_cutoff_equals_real_fetch_time(tmp_path: Path) -> None:
    """El cutoff guardado nunca es el target nominal inventado."""

    now = datetime(2026, 7, 27, 12, tzinfo=timezone.utc)
    engine = create_engine("sqlite+pysqlite:///:memory:")
    PrematchRawBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    repository = SqlAlchemyRawResponseRepository(factory)
    connector = EspnProspectiveConnector(
        EspnConnectorConfig(cache_dir=tmp_path, cache_ttl_seconds=0),
        _Session(),  # type: ignore[arg-type]
        lambda: now,
    )
    EspnRawFirstProvider(connector, repository).fetch(
        "standings",
        entity_type=EntityType.LEAGUE,
        scope_event_id="10",
        snapshot_bucket="T-72h",
        kickoff_ts=now + timedelta(hours=72),
    )
    with factory() as session:
        row = session.execute(select(RawResponse)).scalar_one()
    assert row.cutoff_ts == row.fetched_at


# Version: 1.0.0
# Created: 2026-07-27
