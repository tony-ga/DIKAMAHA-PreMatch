"""Pruebas del contrato visual raw-first de Fase 100A."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi.testclient import TestClient

from src.dikamaha_service import create_app
from src.espn_fixture_context import FixtureContextService


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


# Version: 1.0.0
# Created: 2026-07-29
