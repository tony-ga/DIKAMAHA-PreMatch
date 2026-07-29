"""Contrato sintético de Fase 7.14 sin red ni PostgreSQL."""

from __future__ import annotations

from src.prospective_ingestion_v2 import (
    IngestionV2Config, ProspectiveIngestionV2Error, STAGING_SCHEMA, SourceReference,
    build_batch, canonical_hash, frozen_config,
)


def _payload() -> tuple[dict, dict, str]:
    """Crea un payload ESPN mínimo para validar el contrato de staging."""

    event = {"date": "2026-08-01T12:00:00Z", "competitions": [{"status": {"type": {"name": "STATUS_FINAL"}}, "competitors": [
        {"homeAway": "home", "team": {"$ref": "https://x/teams/10"}, "score": "1"},
        {"homeAway": "away", "team": {"$ref": "https://x/teams/20"}, "score": "0"},
    ]}]}
    plays = {"items": [{"id": "a", "clock": {"value": 300}, "scoringPlay": True, "team": {"$ref": "https://x/teams/10"}}]}
    return event, plays, "2026-08-01T14:00:00+00:00"


def test_valid_batch_is_deterministic_and_complete() -> None:
    """Normaliza un partido completo sin depender de una fuente externa."""

    event, plays, fetched = _payload()
    first = build_batch(SourceReference("900001", "1"), event, plays, fetched)
    second = build_batch(SourceReference("900001", "1"), event, plays, fetched)
    assert first["identity"]["complete"] is True
    assert first["identity"]["league_slug"] == "esp.1"
    assert first["events"] == second["events"]
    assert canonical_hash(first["events"]) == canonical_hash(second["events"])


def test_batch_preserves_explicit_league_slug() -> None:
    """Conserva la liga de origen para la cohorte global separada."""

    event, plays, fetched = _payload()
    batch = build_batch(SourceReference("900005", "1", "eng.1"), event, plays, fetched)
    assert batch["identity"]["league_slug"] == "eng.1"


def test_missing_clock_is_preserved_as_rejected_raw() -> None:
    """No imputa reloj cero para un evento temporalmente inválido."""

    event, plays, fetched = _payload()
    plays["items"].append({"id": "bad", "team": {"$ref": "https://x/teams/20"}})
    batch = build_batch(SourceReference("900002", "1"), event, plays, fetched)
    assert batch["rejected"][0]["reason"] == "missing_or_invalid_event_clock"


def test_future_event_and_blocked_id_are_rejected() -> None:
    """Rechaza eventos futuros y preserva la exclusión explícita de 704766."""

    event, plays, fetched = _payload()
    plays["items"][0]["clock"]["value"] = 10_000
    batch = build_batch(SourceReference("900003", "1"), event, plays, fetched)
    assert batch["rejected"][0]["reason"] == "future_event_after_ingestion"
    try:
        build_batch(SourceReference("704766", "1"), event, {"items": []}, fetched)
    except ProspectiveIngestionV2Error as error:
        assert str(error) == "blocked_match_704766"
    else:
        raise AssertionError("704766 debe estar excluido")


def test_null_team_unknown_and_duplicate_are_auditable() -> None:
    """Conserva team_id nulo y evita insertar dos veces el mismo evento."""

    event, plays, fetched = _payload()
    plays["items"] = [
        {"id": "same", "clock": {"value": 60}, "type": {"type": "other"}},
        {"id": "same", "clock": {"value": 60}, "type": {"type": "other"}},
    ]
    batch = build_batch(SourceReference("900004", "1"), event, plays, fetched)
    assert len(batch["events"]) == 1
    assert batch["events"][0]["team_provider_id"] is None
    assert batch["events"][0]["event_type"] == "unclassified"


def test_staging_write_is_an_explicit_and_isolated_capability() -> None:
    """Verifica el gate lógico sin crear schema ni abrir una conexión real."""

    disabled = frozen_config(IngestionV2Config(False, False, None))
    enabled = frozen_config(IngestionV2Config(True, True, "manifest.json"))
    assert disabled["staging_writes_permitted"] is False
    assert enabled["staging_writes_permitted"] is True
    assert STAGING_SCHEMA == "prospective_staging_v2"


def test_repository_rejects_missing_explicit_write_flag() -> None:
    """Evita que una llamada de infraestructura eluda el gate del runner."""

    from src.prospective_ingestion_v2 import StagingV2Repository

    try:
        StagingV2Repository("postgresql://ignored", write_enabled=False)
    except ProspectiveIngestionV2Error as error:
        assert str(error) == "staging_write_flag_required"
    else:
        raise AssertionError("la escritura staging debe requerir bandera explícita")


# Version: 1.0.0
# Created: 2026-07-16
