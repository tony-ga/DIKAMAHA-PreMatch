"""Pruebas causales del gate histórico Fase 114."""

from __future__ import annotations

from datetime import datetime, timezone

from src.evaluate_live_markov_hawkes_historical import (
    HistoricalLiveConfig,
    _next_event_target,
    select_hawkes_league_admission,
    temporal_partition,
    walkforward_priors,
)


def _match(match_id: int, kickoff: str, home: int, away: int, hg: int, ag: int) -> dict[str, object]:
    return {
        "provider_match_id": str(match_id),
        "kickoff_ts": datetime.fromisoformat(kickoff).replace(tzinfo=timezone.utc),
        "home_team_id": home,
        "away_team_id": away,
        "home_score": hg,
        "away_score": ag,
        "league_slug": "test.1",
    }


def test_walkforward_priors_do_not_update_inside_same_kickoff() -> None:
    config = HistoricalLiveConfig(minimum_league_history=1, minimum_team_history=1)
    matches = [
        _match(1, "2025-01-01T12:00:00", 1, 2, 2, 0),
        _match(2, "2025-01-02T12:00:00", 2, 1, 1, 1),
        _match(3, "2025-01-03T12:00:00", 1, 2, 7, 0),
        _match(4, "2025-01-03T12:00:00", 1, 2, 0, 7),
    ]

    priors, audit = walkforward_priors(matches, config)

    assert priors[3]["source_hash"] == priors[4]["source_hash"]
    assert priors[3]["cutoff_ts"] < priors[3]["kickoff_ts"]
    assert audit["strictly_prior"] is True


def test_temporal_partition_keeps_atomic_kickoffs_together() -> None:
    config = HistoricalLiveConfig(development_fraction=0.5, validation_fraction=0.25)
    matches = [
        _match(1, "2025-01-01T12:00:00", 1, 2, 1, 0),
        _match(2, "2025-01-02T12:00:00", 1, 2, 1, 0),
        _match(3, "2025-01-03T12:00:00", 1, 2, 1, 0),
        _match(4, "2025-01-03T12:00:00", 3, 4, 0, 1),
        _match(5, "2025-01-04T12:00:00", 1, 2, 1, 1),
    ]

    blocks, audit = temporal_partition(matches, config)

    assert blocks[3] == blocks[4]
    assert audit["kickoff_overlap"] == 0


def test_next_event_target_uses_first_supported_event_in_horizon() -> None:
    events = [
        {"event_id": "f", "event_type": "foul", "team_id": 1, "match_clock_seconds": 610.0},
        {"event_id": "c", "event_type": "yellow", "team_id": 2, "match_clock_seconds": 620.0},
        {"event_id": "g", "event_type": "goal", "team_id": 1, "match_clock_seconds": 700.0},
    ]

    assert _next_event_target(events, 600.0, 5.0, 1, 2) == "away:card"
    assert _next_event_target(events, 900.0, 5.0, 1, 2) == "no_event"


def test_hawkes_league_admission_uses_validation_support_and_improvement() -> None:
    config = HistoricalLiveConfig(minimum_hawkes_admission_matches=2)
    rows = [
        {
            "match_id": index,
            "league_slug": league,
            "models": {
                "markov": {"goal_market_log_loss": 1.0},
                "combined": {"goal_market_log_loss": combined},
            },
        }
        for index, league, combined in (
            (1, "admitted.1", 0.9),
            (2, "admitted.1", 0.8),
            (3, "unsupported.1", 0.5),
            (4, "degraded.1", 1.1),
            (5, "degraded.1", 1.2),
        )
    ]

    policy = select_hawkes_league_admission(
        rows, config, rho_goal=1.0, rho_next_event=0.0,
    )

    assert policy["selection_split"] == "validation_only"
    assert policy["confirmation_used_for_selection"] is False
    assert policy["allowed_leagues"] == ["admitted.1"]
