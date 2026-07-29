"""Pruebas unitarias del flujo canónico R3 sin PostgreSQL."""
from __future__ import annotations

from src.espn_phase_7_15_r3 import _summary_identity, _team_refs
from src.prospective_ingestion_v2 import team_ref_audit


def _summary() -> dict:
    """Construye summary ESPN mínimo con equipos y estado."""

    return {"header": {"id": "77", "competitions": [{"status": {"type": {"state": "post", "completed": True}}, "competitors": [{"homeAway": "home", "team": {"id": "86", "displayName": "Home FC"}, "score": "2"}, {"homeAway": "away", "team": {"id": "83", "displayName": "Away FC"}, "score": "1"}]}]}}


def test_summary_identity_preserves_provider_names_and_scores() -> None:
    """No infiere nombres ni marcadores fuera de summary."""

    identity = _summary_identity(_summary())
    assert identity["status"] == "post"
    assert identity["teams"]["home"] == {"team_id": 86, "name": "Home FC", "score": 2}
    assert identity["teams"]["away"]["score"] == 1


def test_team_refs_match_summary_ids_with_query_strings() -> None:
    """Alinea local/visitante y conserva query keys del ref."""

    event = {"competitions": [{"competitors": [{"homeAway": "home", "team": {"$ref": "https://x/teams/86?lang=es&region=us"}}, {"homeAway": "away", "team": {"$ref": "https://x/teams/83?lang=es"}}]}]}
    refs = _team_refs(event, _summary_identity(_summary()))
    assert refs["home"]["id_consistent"] is True
    assert refs["home"]["ref"]["query_keys"] == ["lang", "region"]


def test_unresolved_ref_is_explicit() -> None:
    """Un path inválido no produce un ID alternativo."""

    audit = team_ref_audit({"$ref": "https://x/clubs/86?lang=es"})
    assert audit["team_id"] is None
    assert audit["provenance"] == "unresolved"


def test_r3_entrypoint_requires_explicit_write_flag() -> None:
    """La persistencia canónica no se activa implícitamente."""

    from src.espn_phase_7_15_r3 import main

    assert main([]) == 1

# Version: 1.0.0
# Created: 2026-07-16
