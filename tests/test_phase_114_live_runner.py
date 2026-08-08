"""Pruebas de binding causal y política Hawkes del runner live."""

from __future__ import annotations

from src.dikamaha_inference import DikamahaInferenceEngine
from scripts.run_phase_114_live_shadow import _prediction


def _snapshot() -> dict[str, object]:
    return {
        "contract_version": "live_event_stream_v1",
        "provider_event_id": "900001",
        "home_team_id": 1,
        "away_team_id": 2,
        "kickoff_ts": "2025-01-10T20:00:00+00:00",
        "source_fetched_at": "2025-01-10T20:10:00+00:00",
        "league_slug": "esp.1",
        "competition_id": "900001",
        "period": 1,
        "match_clock_seconds": 600.0,
        "score_home": 0,
        "score_away": 0,
        "events": [],
        "source_hash": "live-source",
    }


def _prior() -> dict[str, object]:
    return {
        "provider_event_id": "900001",
        "home_team_id": 1,
        "away_team_id": 2,
        "league_slug": "esp.1",
        "cutoff_ts": "2025-01-10T19:59:59+00:00",
        "lambda_base_home": 1.5,
        "lambda_base_away": 1.1,
        "source_hash": "prior-source",
    }


def test_non_admitted_league_uses_exact_markov_fallback() -> None:
    policy = {
        "allowed_leagues": ["eng.1"],
        "rho_goal": 1.0,
        "rho_next_event": 0.0,
    }

    result = _prediction(
        DikamahaInferenceEngine(), _snapshot(), _prior(),
        enable_hawkes=True, hawkes_rho=None, hawkes_rho_goal=None,
        hawkes_rho_next_event=None, hawkes_policy=policy,
    )

    assert result["status"] == "shadow_predicted"
    assert result["hawkes_league_admission"] == {
        "policy_applied": True,
        "admitted": False,
        "fallback_exact_markov_live": True,
    }
    assert result["combined_live"]["markets"] == result["markov_live"]["markets"]


def test_prior_identity_and_strict_cutoff_fail_closed() -> None:
    wrong = {**_prior(), "home_team_id": 99}
    result = _prediction(
        DikamahaInferenceEngine(), _snapshot(), wrong,
        enable_hawkes=True, hawkes_rho=None, hawkes_rho_goal=None,
        hawkes_rho_next_event=None, hawkes_policy=None,
    )
    assert result["status"] == "invalid_frozen_prematch_identity"

    same_kickoff = {**_prior(), "cutoff_ts": "2025-01-10T20:00:00+00:00"}
    result = _prediction(
        DikamahaInferenceEngine(), _snapshot(), same_kickoff,
        enable_hawkes=True, hawkes_rho=None, hawkes_rho_goal=None,
        hawkes_rho_next_event=None, hawkes_policy=None,
    )
    assert result["status"] == "invalid_frozen_prematch_cutoff"


def test_validation_policy_cannot_be_overridden_per_cycle() -> None:
    policy = {
        "allowed_leagues": ["esp.1"],
        "rho_goal": 1.0,
        "rho_next_event": 0.0,
    }
    result = _prediction(
        DikamahaInferenceEngine(), _snapshot(), _prior(),
        enable_hawkes=True, hawkes_rho=None, hawkes_rho_goal=0.5,
        hawkes_rho_next_event=None, hawkes_policy=policy,
    )
    assert result["status"] == "invalid_hawkes_policy_override"
