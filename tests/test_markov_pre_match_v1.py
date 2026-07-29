"""Pruebas unitarias de la calibración Markov pre-partido v1.

Version: 1.0.0
Created: 2026-07-26
"""
from __future__ import annotations

from src.markov_pre_match_v1 import (
    HierarchicalMarkovCalibrator,
    MarkovCalibrationConfig,
    Transition,
    _score_bucket,
    split_match_ids,
)


def _transition(match_id: int, state: str = "equilibrio", next_state: str = "presion") -> Transition:
    """Construye una transición mínima y válida para pruebas."""
    return Transition(match_id, f"2024-01-{match_id:02d}", 1, True, 0, "level", state, "equilibrio", next_state)


def test_score_bucket_groups_extremes() -> None:
    """El marcador observado se agrupa sin perder dirección."""
    assert _score_bucket(-3) == "behind_2_plus"
    assert _score_bucket(0) == "level"
    assert _score_bucket(2) == "ahead_2_plus"


def test_calibrator_normalizes_and_backs_off() -> None:
    """El prior jerárquico normaliza incluso cuando falta equipo específico."""
    calibrator = HierarchicalMarkovCalibrator(MarkovCalibrationConfig(min_support_team=5))
    calibrator.fit([_transition(1), _transition(2), _transition(3)])
    probabilities, tier, support = calibrator.predict(_transition(9))
    assert abs(sum(probabilities.values()) - 1.0) < 1e-9
    assert tier != "team"
    assert support >= 1


def test_split_keeps_matches_disjoint() -> None:
    """Los bloques temporales nunca comparten un partido completo."""
    splits = split_match_ids([_transition(index) for index in range(1, 11)], MarkovCalibrationConfig())
    assert not (splits["development"] & splits["validation"])
    assert not (splits["development"] & splits["confirmation"])
    assert sum(len(rows) for rows in splits.values()) == 10


# Version: 1.0.0
# Created: 2026-07-26
