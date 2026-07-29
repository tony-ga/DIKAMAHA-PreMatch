"""Pruebas del corpus causal multi-resolución."""
from __future__ import annotations

from src.causal_sequence_corpus import (
    SequenceResolution,
    build_resolution,
    score_reconciles,
)


def _match() -> dict[str, object]:
    """Crea un partido mínimo reproducible."""

    return {
        "match_id": 1, "match_date": "2026-01-01T00:00:00Z",
        "home_team_id": 10, "away_team_id": 20, "home_score": 1,
        "away_score": 0, "season": "2025-26", "competition_id": "mex.1",
        "league_slug": "mex.1",
    }


def _events() -> list[dict[str, object]]:
    """Crea eventos a ambos lados de un límite de cinco minutos."""

    return [
        {"event_id": 1, "match_id": 1, "minute": 4, "second": 59,
         "team_id": 10, "event_type": "shot_on_target", "annulled": False},
        {"event_id": 2, "match_id": 1, "minute": 5, "second": 0,
         "team_id": 10, "event_type": "goal", "annulled": False},
    ]


def test_five_minute_boundaries_and_prior_score() -> None:
    """Impide que el gol del objetivo aparezca en su contexto inicial."""

    rows = build_resolution(_match(), _events(), SequenceResolution(5))
    home = [row for row in rows if row["is_home"]]
    assert len(rows) == 36
    assert home[0]["shots_on_target"] == 1
    assert home[1]["score_for_start"] == 0
    assert home[2]["score_for_start"] == 1


def test_all_resolutions_reconcile_the_same_score() -> None:
    """Exige reconciliación idéntica en 5, 10 y 15 minutos."""

    for minutes in (5, 10, 15):
        rows = build_resolution(_match(), _events(), SequenceResolution(minutes))
        assert score_reconciles(rows, 1, 0)


def test_second_half_is_clock_based() -> None:
    """Evita clasificar 15–20 como segunda parte en microventanas."""

    rows = build_resolution(_match(), _events(), SequenceResolution(5))
    home = [row for row in rows if row["is_home"]]
    assert home[3]["period"] == "first_half"
    assert home[9]["period"] == "second_half"


# Version: 1.0.0 - 2026-07-27
