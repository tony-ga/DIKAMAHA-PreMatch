"""Pruebas sintéticas de contrato para la ingesta prospectiva staging."""

from __future__ import annotations

from src.prospective_staging_ingestion import (
    FetchedMatch,
    IngestionConfig,
    ProspectiveIngestionError,
    SourceMatchRef,
    _deduplicate_events,
    _event_timestamp,
    build_batch,
    source_config_from_env,
)


def _payload(match_id: str = "900001") -> FetchedMatch:
    """Construye un evento ESPN mínimo, UTC y orientado para pruebas."""

    event = {"date": "2025-11-01T12:00:00Z", "competitions": [{"id": "1", "status": {"type": {"name": "STATUS_FINAL"}}, "competitors": [
        {"homeAway": "home", "team": {"$ref": "https://x/teams/10"}, "score": "1"},
        {"homeAway": "away", "team": {"$ref": "https://x/teams/20"}, "score": "0"},
    ]}]}
    plays = {"items": [{"clock": {"value": 300}, "type": {"type": "goal"}, "team": {"$ref": "https://x/teams/10"}, "scoringPlay": True}]}
    return FetchedMatch(SourceMatchRef(match_id, "1"), event, plays, "2025-11-01T14:00:00+00:00")


def test_source_is_disabled_without_explicit_environment(monkeypatch) -> None:
    """No habilita red ni escritura staging por una variable implícita."""

    monkeypatch.delenv("DIKAMAHA_PROSPECTIVE_SOURCE_ENABLED", raising=False)
    monkeypatch.delenv("DIKAMAHA_PROSPECTIVE_STAGING_WRITE_ENABLED", raising=False)
    config = source_config_from_env()
    assert config.source_enabled is False
    assert config.staging_write_enabled is False


def test_build_batch_preserves_null_team_and_final_state() -> None:
    """Conserva equipo nulo sin inventar orientación o equipo interno."""

    fetched = _payload()
    fetched.plays_payload["items"][0].pop("team")
    batch = build_batch(fetched)
    assert batch["identity"]["complete"] is True
    assert batch["events"][0]["team_provider_id"] is None
    assert batch["events"][0]["event_ts"] == "2025-11-01T12:05:00+00:00"


def test_duplicate_event_and_future_clock_are_controlled() -> None:
    """Deduplica replay y rechaza clocks fuera de la semántica temporal."""

    row = {"provider_match_id": "1", "event_index": 0, "event_hash": "a"}
    assert _deduplicate_events([row, dict(row)]) == [row]
    assert _event_timestamp("2025-11-01T12:00:00+00:00", 5, 0).endswith("+00:00")


def test_event_after_ingestion_time_is_rejected() -> None:
    """No permite que un evento futuro entre a la zona staging."""

    fetched = _payload()
    fetched = FetchedMatch(fetched.reference, fetched.event_payload, fetched.plays_payload, "2025-11-01T12:01:00+00:00")
    try:
        build_batch(fetched)
    except ProspectiveIngestionError as error:
        assert str(error) == "future_event_after_ingestion"
    else:
        raise AssertionError("evento futuro debía rechazarse")


def test_704766_is_explicitly_blocked() -> None:
    """Mantiene la exclusión del identificador fallido en toda ingesta nueva."""

    try:
        build_batch(_payload("704766"))
    except ProspectiveIngestionError as error:
        assert str(error) == "blocked_match_704766"
    else:
        raise AssertionError("704766 debía estar bloqueado")


def test_malformed_or_incomplete_payload_is_not_accepted() -> None:
    """No completa artificialmente un payload sin orientación verificable."""

    fetched = _payload()
    fetched.event_payload["competitions"][0]["competitors"] = []
    try:
        build_batch(fetched)
    except ProspectiveIngestionError as error:
        assert str(error) == "missing_home_away_orientation"
    else:
        raise AssertionError("payload malformado debía rechazarse")


# Version: 1.0.0
# Created: 2026-07-16
