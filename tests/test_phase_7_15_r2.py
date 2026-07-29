"""Pruebas de normalización ESPN R2 sin red ni PostgreSQL."""
from __future__ import annotations

from src.prospective_ingestion_v2 import ProspectiveIngestionV2Error, SourceReference, _team_id, build_batch, team_ref_audit
from src.espn_prospective_connector import extract_event_id, payload_hash


def _event(team_ref: str = "https://x/teams/86?lang=es&region=us") -> dict:
    """Construye evento sintético con query string en refs."""

    return {"date": "2025-10-26T15:15:00Z", "competitions": [{"status": {"type": {"state": "post", "completed": True}}, "competitors": [{"homeAway": "home", "team": {"$ref": team_ref}, "score": "2"}, {"homeAway": "away", "team": {"$ref": "https://x/teams/83?lang=es&region=us"}, "score": "1"}]}]}


def test_team_ref_query_string_and_multiple_parameters() -> None:
    """Valida path antes de ignorar query params y conserva provenance."""

    audit = team_ref_audit({"$ref": "https://x/v2/teams/86?lang=es&region=us"})
    assert audit["team_id"] == 86
    assert audit["query_keys"] == ["lang", "region"]
    assert _team_id({"$ref": "https://x/v2/teams/86?lang=es&region=us"}) == 86


def test_team_id_accepts_direct_provider_identity() -> None:
    """El fallback commentary conserva IDs directos sin fabricar refs."""

    assert _team_id({"id": "94", "displayName": "Equipo"}) == 94


def test_invalid_team_ref_is_unresolved_not_invented() -> None:
    """Refs ajenos a /teams/{id} se conservan como no resueltos."""

    audit = team_ref_audit({"$ref": "https://x/v2/clubs/86?lang=es"})
    assert audit["team_id"] is None
    assert audit["provenance"] == "unresolved"


def test_event_id_direct_reference_and_invalid() -> None:
    """Extrae event_id sólo de ubicaciones ESPN válidas."""

    assert extract_event_id({"id": "748236"}) == "748236"
    assert extract_event_id({"$ref": "https://x/events/748236?lang=es"}) == "748236"
    assert extract_event_id({"id": "not-valid"}) is None


def test_known_event_types_goals_cards_substitutions_normalize() -> None:
    """Normaliza eventos timeline relevantes con timestamps UTC."""

    plays = {"items": [{"id": "g", "clock": {"value": 300}, "scoringPlay": True, "team": {"$ref": "https://x/teams/86?lang=es"}}, {"id": "y", "clock": {"value": 600}, "type": {"type": "yellow-card"}}, {"id": "s", "clock": {"value": 900}, "type": {"type": "substitution"}}]}
    batch = build_batch(SourceReference("748236", "748236"), _event(), plays, "2025-10-26T20:00:00+00:00")
    assert [row["event_type"] for row in batch["events"]] == ["goal", "yellow", "substitution"]
    assert all(row["event_ts"].endswith("+00:00") for row in batch["events"])


def test_missing_team_is_rejected_explicitly() -> None:
    """Un equipo irresoluble no se convierte en un ID ficticio."""

    event = _event("https://x/clubs/86?lang=es")
    try:
        build_batch(SourceReference("748236", "748236"), event, {"items": []}, "2025-10-26T20:00:00+00:00")
    except ProspectiveIngestionV2Error as error:
        assert str(error) == "missing_provider_team_id"
    else:
        raise AssertionError("debe rechazar team ref no resoluble")


def test_schema_unexpected_and_replay_hash() -> None:
    """Schemas incompletos fallan y hashes de payload son deterministas."""

    try:
        build_batch(SourceReference("748236", "748236"), _event(), {}, "2025-10-26T20:00:00+00:00")
    except ProspectiveIngestionV2Error as error:
        assert str(error) == "malformed_plays_items"
    else:
        raise AssertionError("plays sin items debe rechazarse")
    payload = {"id": "748236", "items": [1, 2]}
    assert payload_hash(payload) == payload_hash(payload)

# Version: 1.0.0
# Created: 2026-07-16
