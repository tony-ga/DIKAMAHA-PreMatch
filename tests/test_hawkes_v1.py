"""Pruebas sintéticas de Hawkes v1."""
from datetime import datetime, timedelta, timezone

import pytest

from src.hawkes_v1 import HawkesConfig, HawkesV1


BASE = {"markov_model_hash": "synthetic-markov", "markov_transition_version": "markov_transition_v1", "markov_matrix_synthetic": True}


def event(event_id: str, minutes: int, team_id: int = 10, event_type: str = "shot_on_target") -> dict:
    """Construye un evento sintético."""
    return {"event_id": event_id, "event_ts": f"2025-01-01T12:{minutes:02d}:00+00:00", "team_id": team_id, "event_type": event_type}


def run(events: list[dict], snapshot: int = 20) -> dict:
    """Evalúa un snapshot sintético."""
    return HawkesV1().predict_snapshot(match_id=1, snapshot_ts=f"2025-01-01T12:{snapshot:02d}:00+00:00", lambda_markov_home=1.0, lambda_markov_away=0.8, home_team_id=10, away_team_id=20, events=events, markov_provenance=BASE)


def test_no_events_and_markov_separation() -> None:
    """Sin eventos, Hawkes conserva la baseline Markov."""
    result = run([])
    assert result["lambda_hawkes_home"] == 1.0
    assert result["lambda_hawkes_away"] == 0.8
    assert result["markov_provenance"] == BASE
    assert "probabilities" not in result


def test_self_cross_decay_and_memory() -> None:
    """Comprueba autoexcitación, excitación cruzada, decaimiento y memoria."""
    old = {"event_id": "old", "event_ts": "2025-01-01T11:40:00+00:00", "team_id": 10, "event_type": "shot_on_target"}
    result = run([event("h", 19, 10), event("a", 19, 20), old])
    assert result["lambda_hawkes_home"] > 1.0
    assert result["lambda_hawkes_away"] > 0.8
    assert {x["event_id"] for x in result["events_used"]} == {"h", "a"}
    assert result["event_contributions"][0]["dt_minutes"] == 1.0


def test_future_duplicate_null_unknown_and_annulled() -> None:
    """Los eventos no excitadores quedan auditados y no se cuentan dos veces."""
    events = [event("x", 19), event("x", 19), event("future", 25), event("null", 19, None), event("unknown", 19, 10, "unknown"), {**event("ann", 19), "annulled": True}]
    result = run(events)
    assert len(result["events_used"]) == 1
    assert {x["event_id"] for x in result["events_audit"]} == {"x", "future", "null", "unknown", "ann"}


def test_supercritical_matrix_rejected() -> None:
    """Una matriz G supercrítica produce error controlado."""
    with pytest.raises(ValueError, match="supercrítica"):
        HawkesV1(HawkesConfig(branching_matrix=((1.1, 0.0), (0.0, 1.1))))


def test_deterministic_output() -> None:
    """La misma entrada produce la misma salida."""
    assert run([event("a", 10)]) == run([event("a", 10)])
