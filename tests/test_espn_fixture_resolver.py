"""Pruebas del resolver de fixtures ESPN sin red."""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.dikamaha_service import ServiceConfig, create_app
from src.espn_fixture_resolver import (
    EspnFixtureResolver, FixtureLookup, allocate_fixtures_fairly,
)


class FakeScoreboard:
    """Proveedor determinista de scoreboard para pruebas."""

    def scoreboard(self, date: str) -> dict[str, object]:
        """Devuelve un fixture sólo para el día central."""

        if date != "20300101":
            return {"events": []}
        return {"events": [{"id": "990001", "date": "2030-01-01T20:00:00Z", "competitions": [{"id": "990001", "status": {"type": {"state": "pre"}}, "competitors": [{"homeAway": "home", "team": {"id": "94", "displayName": "Equipo Á"}}, {"homeAway": "away", "team": {"id": "86", "displayName": "Equipo B"}}]}]}]}


def _fixture(match_id: int, league: str, kickoff: str) -> dict[str, object]:
    """Construye un fixture mínimo para probar el reparto por liga."""

    return {"match_id": match_id, "league_slug": league, "kickoff_ts": kickoff}


def test_allocate_fixtures_fairly_prevents_one_league_from_starving_the_rest() -> None:
    """DEC-192: un torneo con muchos kickoffs simultáneos no debe agotar el cupo.

    Caso real medido en producción: `uefa.europa.conf_qual` con 20 partidos
    a la misma hora. Sin reparto, `sorted(...)[:limit]` los tomaba todos y
    ninguna otra liga aparecía. Con reparto, cada liga activa consigue al
    menos un puesto antes de que cualquiera consiga un segundo.
    """

    crowded = [
        _fixture(index, "uefa.europa.conf_qual", "2026-08-14T19:00:00Z")
        for index in range(20)
    ]
    others = [
        _fixture(100, "mex.1", "2026-08-14T20:00:00Z"),
        _fixture(101, "col.1", "2026-08-14T21:00:00Z"),
        _fixture(102, "arg.1", "2026-08-14T22:00:00Z"),
    ]

    selected, hidden = allocate_fixtures_fairly(crowded + others, limit=5)

    leagues_shown = {str(row["league_slug"]) for row in selected}
    assert leagues_shown == {"uefa.europa.conf_qual", "mex.1", "col.1", "arg.1"}
    assert "uefa.europa.conf_qual" in hidden  # le quedaron 16 partidos fuera
    assert "mex.1" not in hidden and "col.1" not in hidden and "arg.1" not in hidden


def test_allocate_fixtures_fairly_keeps_chronological_order_in_the_output() -> None:
    """El reparto decide qué entra; el orden de salida sigue siendo por kickoff."""

    rows = [
        _fixture(1, "a", "2026-08-14T23:00:00Z"),
        _fixture(2, "b", "2026-08-14T18:00:00Z"),
        _fixture(3, "a", "2026-08-14T19:00:00Z"),
    ]

    selected, _ = allocate_fixtures_fairly(rows, limit=3)

    assert [row["match_id"] for row in selected] == [2, 3, 1]


def test_allocate_fixtures_fairly_reports_no_truncation_when_everything_fits() -> None:
    rows = [_fixture(1, "a", "2026-08-14T18:00:00Z"), _fixture(2, "b", "2026-08-14T19:00:00Z")]

    selected, hidden = allocate_fixtures_fairly(rows, limit=10)

    assert len(selected) == 2
    assert hidden == []


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
