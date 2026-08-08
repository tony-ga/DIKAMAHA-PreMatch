"""Regresiones para causalidad de kickoffs simultáneos."""
from __future__ import annotations

import pandas as pd

from scripts.run_phase_84a_team_count_markets import METRICS, _examples
from src.temporal_integrity import (
    atomic_warmup_end,
    aligned_fraction_boundaries,
    normalize_kickoff_splits,
    split_boundary_is_causal,
)


def _match(match_id: int, split: str, home: int, away: int) -> dict[str, object]:
    """Construye un agregado mínimo compatible con Fase 84A."""

    targets = {spec.name: match_id % 5 + 1 for spec in METRICS}
    return {
        "match_id": match_id,
        "match_date": "2026-01-01T18:00:00+00:00",
        "league_slug": "test.1",
        "split": split,
        "home_team_id": home,
        "away_team_id": away,
        "home": targets,
        "away": {name: value + 1 for name, value in targets.items()},
    }


def test_phase84_features_are_invariant_inside_same_kickoff() -> None:
    """Ningún resultado simultáneo alimenta las features de otro partido."""

    first = _match(1, "fit", 1, 2)
    second = _match(2, "fit", 3, 4)
    normal = _examples([first, second])
    reversed_order = _examples([second, first])
    by_identity = lambda rows: {
        (row["match_id"], row["is_home"]): (
            row["features"], row["baselines"])
        for row in rows
    }
    assert by_identity(normal) == by_identity(reversed_order)


def test_mixed_same_kickoff_moves_whole_bucket_to_later_split() -> None:
    """Una frontera heredada no divide partidos simultáneos."""

    rows = normalize_kickoff_splits([
        _match(1, "fit", 1, 2),
        _match(2, "selection", 3, 4),
    ])
    assert {row["split"] for row in rows} == {"selection"}


def test_fractional_boundaries_are_aligned_to_complete_kickoffs() -> None:
    """Los índices 60/80 retroceden al inicio del timestamp compartido."""

    dates = [
        "2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04",
        "2026-01-04", "2026-01-05", "2026-01-05", "2026-01-06",
        "2026-01-06", "2026-01-07",
    ]
    frame = pd.DataFrame({"match_date": dates})
    boundaries = aligned_fraction_boundaries(frame, (0.60, 0.80))
    assert boundaries == (5, 7)
    assert all(split_boundary_is_causal(frame, value) for value in boundaries)


def test_warmup_extends_to_end_of_shared_kickoff() -> None:
    """La primera evaluación comienza después del timestamp de warm-up."""

    rows = [
        {"match_date": "2026-01-01T18:00:00Z"},
        {"match_date": "2026-01-02T18:00:00Z"},
        {"match_date": "2026-01-02T18:00:00Z"},
        {"match_date": "2026-01-03T18:00:00Z"},
    ]
    assert atomic_warmup_end(rows, 2) == 3
