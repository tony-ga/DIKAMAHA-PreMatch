"""Pruebas del contrato visual raw-first de Fase 100A."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from src.dikamaha_service import create_app
from src.espn_fixture_context import (
    FixtureContextService, SqlAlchemyFixtureContextRepository,
)


class _Row:
    """Fila raw mínima para probar el parser sin una base real."""

    def __init__(self, row_id: int, endpoint: str, payload: dict[str, Any]) -> None:
        """Conserva los campos consumidos por el contrato visual."""

        self.id, self.endpoint, self.response_json = row_id, endpoint, payload
        self.fetched_at = datetime(2026, 7, 29, tzinfo=timezone.utc)


class _Repository:
    """Repositorio in-memory que nunca hace red ni modifica snapshots."""

    def rows(self, _league: str, _event: str) -> list[_Row]:
        """Devuelve summary, officials y broadcasts ya persistidos."""

        competition = {"date": "2026-08-01T01:00:00Z", "groups": {"name": "Apertura"}, "competitors": [
            {"homeAway": "home", "team": {"id": "1", "displayName": "Puebla", "color": "fff"}},
            {"homeAway": "away", "team": {"id": "2", "displayName": "Guadalajara", "color": "f00"}},
        ]}
        summary = {"header": {"league": {"shortName": "Liga MX"}, "season": {"name": "2026"}, "competitions": [competition]}, "gameInfo": {"venue": {"fullName": "Cuauhtémoc", "address": {"city": "Puebla", "country": "Mexico"}}}}
        return [_Row(1, "https://site.api.espn.com/summary", summary),
                _Row(2, "https://core/officials", {"items": [{"displayName": "Árbitro A", "role": "Referee"}]}),
                _Row(3, "https://core/broadcasts", {"items": [{"name": "ESPN"}]})]

    def context_rows(self, _league: str, _team_ids: list[str]) -> list[_Row]:
        """Devuelve standings y calendarios ya capturados para ambos equipos."""

        entry = {"team": {"id": "1"}, "stats": [
            {"name": "rank", "displayValue": "2"}, {"name": "points", "displayValue": "6"},
        ]}
        standings = {"children": [{"standings": {"entries": [entry]}}]}
        schedule = {"team": {"id": "1"}, "events": [{"id": "old", "date": "2026-07-20T00:00:00Z", "name": "Puebla vs Rival"}]}
        roster = {"team": {"id": "1"}, "athletes": [{"id": "p1", "fullName": "Jugador A", "status": {"type": "active"}, "injuries": [{"status": "Questionable", "description": "Muscular"}]}]}
        news = {"articles": [{"id": "n1", "headline": "Titular de prueba", "published": "2026-07-29T00:00:00Z"}]}
        return [_Row(4, "https://site.api.espn.com/standings", standings),
                _Row(5, "https://site.api.espn.com/teams/1/schedule", schedule),
                _Row(6, "https://site.api.espn.com/teams/1/roster", roster),
                _Row(7, "https://site.api.espn.com/teams/1/injuries", {}),
                _Row(8, "https://site.api.espn.com/news", news)]


def test_context_preserves_published_identity_and_marks_display_only() -> None:
    """La ficha usa nombres publicados y nunca declara una feature."""

    result = FixtureContextService(_Repository()).context("mex.1", "10")  # type: ignore[arg-type]
    assert result["status"] == "available"
    assert result["display_only"] is True and result["model_feature"] is False
    assert result["teams"]["home"]["name"] == "Puebla"
    assert result["venue"]["city"] == "Puebla"
    assert result["team_context"]["home"]["standing"]["rank"] == "2"
    assert result["availability"]["home"]["active_roster_count"] == 1
    assert result["availability"]["home"]["injury_report_status"] == "not_published"
    assert result["editorial"]["model_feature"] is False
    assert result["editorial"]["articles"][0]["headline"] == "Titular de prueba"


def test_context_endpoint_uses_injected_raw_snapshot_service() -> None:
    """El API expone sólo el contrato visual, sin invocar ESPN desde el bot."""

    app = create_app(); app.state.fixture_context = FixtureContextService(_Repository())  # type: ignore[arg-type]
    response = TestClient(app).get("/v1/explorer/fixture/context", params={"league": "mex.1", "event_id": "10"})
    assert response.status_code == 200
    assert response.json()["competition"]["name"] == "Liga MX"


class _BrokenRepository:
    """Simula el ledger de auditoría ausente en producción.

    `data/phase_100/raw_responses.sqlite` está fuera de git y de la imagen
    Docker por diseño (mismo patrón que `phase_72/73/86`), así que en
    producción el archivo nunca existe y SQLite falla con
    `OperationalError: unable to open database file` al primer intento de
    lectura.
    """

    def rows(self, _league: str, _event: str) -> list[Any]:
        """Reproduce el fallo real de abrir un archivo inexistente."""

        raise OperationalError(
            "SELECT", {}, sqlite3.OperationalError("unable to open database file"))

    def context_rows(self, _league: str, _team_ids: list[str]) -> list[Any]:
        """No debe alcanzarse: `rows()` falla primero."""

        raise AssertionError("context_rows no debe llamarse si rows() falla")


def test_missing_ledger_degrades_to_unavailable_instead_of_crashing() -> None:
    """Un ledger ausente es una ausencia de dato, no un error de servidor.

    Antes de esta corrección, `OperationalError` se propagaba sin capturar y
    la ruta HTTP devolvía 500 para todo fixture, siempre, en producción.
    """

    result = FixtureContextService(_BrokenRepository()).context("mex.1", "10")  # type: ignore[arg-type]
    assert result["status"] != "available"
    assert result == FixtureContextService(_EmptyRepository()).context("mex.1", "10")  # type: ignore[arg-type]


def test_context_endpoint_never_returns_500_for_a_missing_ledger() -> None:
    """El endpoint HTTP responde 200 con ausencia explícita, no 500."""

    app = create_app()
    app.state.fixture_context = FixtureContextService(_BrokenRepository())  # type: ignore[arg-type]
    response = TestClient(app).get(
        "/v1/explorer/fixture/context", params={"league": "mex.1", "event_id": "10"})
    assert response.status_code == 200
    assert response.json()["status"] != "available"


def test_sqlite_repository_over_a_missing_directory_reports_unavailable(
    tmp_path: Path,
) -> None:
    """Prueba de extremo a extremo con un motor SQLite real, no simulado.

    Reproduce exactamente el defecto de producción: apunta a una ruta cuyo
    directorio padre no existe, igual que `/app/data/phase_100/` en el
    contenedor, donde sólo `/app/data` se crea.
    """

    target = tmp_path / "no_creado" / "raw_responses.sqlite"
    repository = SqlAlchemyFixtureContextRepository(f"sqlite+pysqlite:///{target}")
    result = FixtureContextService(repository).context("mex.1", "10")
    assert result["status"] != "available"


class _EmptyRepository:
    """Ledger presente pero sin snapshots capturados para este fixture."""

    def rows(self, _league: str, _event: str) -> list[Any]:
        """No hay filas: mismo resultado que debe producir un ledger roto."""

        return []

    def context_rows(self, _league: str, _team_ids: list[str]) -> list[Any]:
        """No se alcanza cuando `rows()` está vacío."""

        return []


# Version: 1.0.0
# Created: 2026-07-29
