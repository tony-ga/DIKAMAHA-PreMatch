"""Pruebas del resolver de fixtures ESPN sin red."""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.dikamaha_service import ServiceConfig, create_app
from src.espn_fixture_resolver import EspnFixtureResolver, FixtureLookup


class FakeScoreboard:
    """Proveedor determinista de scoreboard para pruebas."""

    def scoreboard(self, date: str) -> dict[str, object]:
        """Devuelve un fixture sólo para el día central."""

        if date != "20300101":
            return {"events": []}
        return {"events": [{"id": "990001", "date": "2030-01-01T20:00:00Z", "competitions": [{"id": "990001", "status": {"type": {"state": "pre"}}, "competitors": [{"homeAway": "home", "team": {"id": "94", "displayName": "Equipo Á"}}, {"homeAway": "away", "team": {"id": "86", "displayName": "Equipo B"}}]}]}]}


def test_resolver_matches_ids_and_names() -> None:
    """Resuelve exactamente un fixture y conserva orientación."""

    resolver = EspnFixtureResolver(FakeScoreboard())
    fixture = resolver.resolve(FixtureLookup("esp.1", "20300101", home_team_name="equipo a", away_team_id=86))
    assert fixture.match_id == 990001
    assert fixture.home_team_id == 94 and fixture.away_team_id == 86


def test_fixture_endpoint_uses_injected_resolver() -> None:
    """El endpoint operativo resuelve y predice sin persistir."""

    config = ServiceConfig(mode="operational_readonly", external_calls_enabled=True)
    app = create_app(config, fixture_resolver=EspnFixtureResolver(FakeScoreboard()))
    response = TestClient(app).post("/v1/predict/fixture", json={"league_slug": "esp.1", "kickoff_date": "20300101", "home_team_id": 94, "away_team_id": 86})
    assert response.status_code == 200
    data = response.json()
    assert data["fixture"]["match_id"] == 990001
    assert data["audit"]["target_match_data_used"] is False


def test_fixture_endpoint_is_disabled_in_local_mode() -> None:
    """El modo local no puede abrir red por request."""

    response = TestClient(create_app()).post("/v1/predict/fixture", json={"league_slug": "esp.1", "kickoff_date": "20300101", "match_id": 990001})
    assert response.status_code == 422
