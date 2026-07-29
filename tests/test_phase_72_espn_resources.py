"""Pruebas de allowlist y rutas pre-match ESPN de Fase 72."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.espn_prospective_connector import (
    ALLOWED_HOSTS,
    EspnConnectorConfig,
    EspnConnectorError,
    EspnProspectiveConnector,
)


@pytest.mark.parametrize(
    ("resource", "identifiers", "fragment"),
    [
        ("teams", {}, "/mex.1/teams"),
        ("scoreboard", {"date": "20260727"}, "/mex.1/scoreboard"),
        ("summary", {"event_id": "10"}, "/mex.1/summary"),
        ("team", {"team_id": "19"}, "/teams/19"),
        ("roster", {"team_id": "19"}, "/teams/19/roster"),
        ("team_schedule", {"team_id": "19"}, "/teams/19/schedule"),
        ("injuries", {"team_id": "19"}, "/teams/19/injuries"),
        ("standings", {}, "/mex.1/standings"),
        ("seasons", {}, "/leagues/mex.1/seasons"),
        ("season_athletes", {"season": "2026"}, "/seasons/2026/athletes"),
        ("core_teams", {}, "/leagues/mex.1/teams"),
        ("active_athletes", {}, "/mex.1/athletes"),
        ("athlete", {"athlete_id": "99"}, "/athletes/99"),
        ("event", {"event_id": "10"}, "/events/10"),
        ("core_standings", {}, "/leagues/mex.1/standings"),
        ("rankings", {}, "/leagues/mex.1/rankings"),
        ("venues", {}, "/leagues/mex.1/venues"),
        ("leaders", {}, "/leagues/mex.1/leaders"),
        ("season_leaders", {"season": "2026"}, "/seasons/2026/leaders"),
        (
            "odds",
            {"event_id": "10", "competition_id": "20"},
            "/events/10/competitions/20/odds",
        ),
        (
            "officials",
            {"event_id": "10", "competition_id": "20"},
            "/events/10/competitions/20/officials",
        ),
        (
            "broadcasts",
            {"event_id": "10", "competition_id": "20"},
            "/events/10/competitions/20/broadcasts",
        ),
        (
            "situation",
            {"event_id": "10", "competition_id": "20"},
            "/events/10/competitions/20/situation",
        ),
        (
            "probabilities",
            {"event_id": "10", "competition_id": "20"},
            "/events/10/competitions/20/probabilities",
        ),
        ("news", {}, "/mex.1/news"),
    ],
)
def test_documented_resource_builds_allowlisted_request(
    tmp_path: Path,
    resource: str,
    identifiers: dict[str, str],
    fragment: str,
) -> None:
    """Cada recurso usa un host autorizado y una ruta documentada."""

    connector = EspnProspectiveConnector(EspnConnectorConfig(league="mex.1", cache_dir=tmp_path))
    request = connector.resource_request(resource, **identifiers)
    assert any(host in request.url for host in ALLOWED_HOSTS)
    assert fragment in request.url


def test_unknown_resource_is_rejected(tmp_path: Path) -> None:
    """No se permite descubrir endpoints fuera del catálogo."""

    connector = EspnProspectiveConnector(EspnConnectorConfig(cache_dir=tmp_path))
    with pytest.raises(EspnConnectorError, match="unsupported_prematch_resource"):
        connector.resource_request("not_documented")


def test_path_identifier_cannot_escape_allowlist(tmp_path: Path) -> None:
    """Los identificadores no pueden inyectar segmentos de URL."""

    connector = EspnProspectiveConnector(EspnConnectorConfig(cache_dir=tmp_path))
    with pytest.raises(EspnConnectorError, match="invalid_identifier"):
        connector.resource_request("roster", team_id="../../other")


# Version: 1.0.0
# Created: 2026-07-27
