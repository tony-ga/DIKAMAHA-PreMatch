"""Pruebas de persistencia causal y replay de Fase 72."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from src.espn_prospective_connector import (
    EspnConnectorConfig,
    EspnConnectorError,
    EspnProspectiveConnector,
)
from src.espn_raw_first_provider import EspnRawFirstProvider
from src.prematch_data_contracts import EntityType
from src.prematch_raw_store import (
    PrematchRawBase,
    RawResponse,
    SqlAlchemyRawResponseRepository,
)
from scripts.run_phase_72_markov_causal_contract import _classification


class _Response:
    """Respuesta HTTP sintética."""

    status_code = 200

    def __init__(self, payload: dict[str, Any]) -> None:
        """Conserva un payload determinista."""

        self._payload = payload

    def raise_for_status(self) -> None:
        """No falla para el estado 200 sintético."""

    def json(self) -> dict[str, Any]:
        """Devuelve el objeto JSON configurado."""

        return self._payload


class _Session:
    """Sesión HTTP sin red."""

    def __init__(self, payload: dict[str, Any]) -> None:
        """Inicializa headers y registro de llamadas."""

        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._response = _Response(payload)

    def get(self, url: str, **kwargs: Any) -> _Response:
        """Registra la solicitud y devuelve una respuesta fija."""

        self.calls.append((url, kwargs))
        return self._response


def _repository() -> tuple[SqlAlchemyRawResponseRepository, sessionmaker[Session]]:
    """Crea un repositorio SQLite aislado."""

    engine = create_engine("sqlite+pysqlite:///:memory:")
    PrematchRawBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return SqlAlchemyRawResponseRepository(factory), factory


def _provider(
    tmp_path: Path,
    now: datetime,
) -> tuple[EspnRawFirstProvider, sessionmaker[Session], _Session]:
    """Construye proveedor completo sin tráfico externo."""

    repository, factory = _repository()
    http = _Session({"items": [{"id": "7"}]})
    config = EspnConnectorConfig(league="mex.1", cache_dir=tmp_path, cache_ttl_seconds=0)
    connector = EspnProspectiveConnector(config, http, lambda: now)  # type: ignore[arg-type]
    return EspnRawFirstProvider(connector, repository), factory, http


def test_raw_first_provider_persists_before_replay(tmp_path: Path) -> None:
    """El proveedor entrega un ID persistido y el replay conserva el hash."""

    now = datetime(2026, 7, 27, 12, tzinfo=timezone.utc)
    provider, factory, _ = _provider(tmp_path, now)
    stored = provider.fetch("roster", entity_type=EntityType.TEAM, entity_id="19", team_id="19")
    assert provider.replay(stored.id) == {"items": [{"id": "7"}]}
    with factory() as session:
        row = session.execute(select(RawResponse)).scalar_one()
    assert row.entity_type == "team"
    assert row.response_hash == stored.response_hash
    assert row.request_hash == stored.request_hash


def test_prospective_capture_rejects_post_kickoff(tmp_path: Path) -> None:
    """Una captura tardía nunca se etiqueta como prospectiva."""

    now = datetime(2026, 7, 27, 12, tzinfo=timezone.utc)
    provider, factory, _ = _provider(tmp_path, now)
    with pytest.raises(ValueError, match="prospective_capture_not_before_kickoff"):
        provider.fetch(
            "officials",
            entity_type=EntityType.EVENT,
            event_id="10",
            competition_id="20",
            kickoff_ts=now - timedelta(minutes=1),
        )
    with factory() as session:
        assert session.execute(select(RawResponse)).scalars().all() == []


def test_repository_requires_timezone(tmp_path: Path) -> None:
    """El reloj sin zona no puede entrar al ledger causal."""

    naive = datetime(2026, 7, 27, 12)
    provider, _, _ = _provider(tmp_path, naive)
    with pytest.raises(EspnConnectorError, match="timezone_required"):
        provider.fetch("teams", entity_type=EntityType.LEAGUE)


def test_replay_is_stable_across_repeated_reads(tmp_path: Path) -> None:
    """El payload persistido produce replay determinista."""

    now = datetime(2026, 7, 27, 12, tzinfo=timezone.utc)
    provider, _, _ = _provider(tmp_path, now)
    stored = provider.fetch("standings", entity_type=EntityType.LEAGUE)
    assert provider.replay(stored.id) == provider.replay(stored.id)


def test_gate_requires_positive_controls() -> None:
    """El gate distingue controles seguros de flags de incidentes."""

    assert _classification({"raw_first": True, "historical_unchanged": True}) == "ready_for_next_phase"
    assert _classification({"raw_first": True, "historical_unchanged": False}) == "insufficient_coverage"


def test_cache_preserves_original_fetch_timestamp(tmp_path: Path) -> None:
    """Una lectura de caché no fabrica un timestamp prospectivo nuevo."""

    first = datetime(2026, 7, 27, 10, tzinfo=timezone.utc)
    later = first + timedelta(hours=2)
    repository, factory = _repository()
    config = EspnConnectorConfig(cache_dir=tmp_path, cache_ttl_seconds=86400)
    first_http = _Session({"events": []})
    first_connector = EspnProspectiveConnector(config, first_http, lambda: first)  # type: ignore[arg-type]
    EspnRawFirstProvider(first_connector, repository).fetch("teams", entity_type=EntityType.LEAGUE)
    second_http = _Session({"should": "not_be_used"})
    second_connector = EspnProspectiveConnector(config, second_http, lambda: later)  # type: ignore[arg-type]
    EspnRawFirstProvider(second_connector, repository).fetch("teams", entity_type=EntityType.LEAGUE)
    with factory() as session:
        rows = session.execute(select(RawResponse).order_by(RawResponse.id)).scalars().all()
    assert len(rows) == 1
    assert second_http.calls == []


# Version: 1.0.0
# Created: 2026-07-27
