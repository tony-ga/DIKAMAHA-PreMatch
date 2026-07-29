"""Pruebas unitarias de la calibración temporal in-play de Fase 7.6."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np

from src.calibrate_inplay_models import (
    CalibrationConfig,
    LabelingConfig,
    _calibrated_matrix,
    _competition_map,
    _hawkes_candidates,
    _label_state,
    _partition,
    _state_scores,
    _transition_coverage,
)
from src.hawkes_v1 import _radius


def _matches(count: int) -> list[dict[str, object]]:
    """Construye partidos sintéticos ordenados."""

    return [
        {
            "id": index,
            "match_date": datetime(2025, 1, index, tzinfo=timezone.utc),
        }
        for index in range(1, count + 1)
    ]


def test_partition_is_disjoint_and_complete() -> None:
    """La partición conserva todos los partidos exactamente una vez."""

    rows = _matches(20)
    partition = _partition(rows, CalibrationConfig())
    ids = [[row["id"] for row in values] for values in partition.values()]
    flattened = [item for values in ids for item in values]
    assert sorted(flattened) == list(range(1, 21))
    assert len(flattened) == len(set(flattened))
    assert all(values for values in ids)


def test_dirichlet_matrix_is_valid_and_not_synthetic() -> None:
    """El suavizado produce una matriz 3x3 normalizada desde conteos."""

    counts = np.asarray([[30, 2, 1], [3, 20, 4], [2, 5, 25]])
    matrix = _calibrated_matrix(counts, 1.0)
    assert matrix.shape == (3, 3)
    assert np.all(matrix >= 0.06)
    assert np.allclose(matrix.sum(axis=1), 1.0)


def test_unknown_is_preserved_when_confidence_is_low() -> None:
    """Las reglas ambiguas no fuerzan una etiqueta táctica."""

    config = LabelingConfig()
    state, confidence = _label_state({0: 0.55, 1: 0.50, 2: 0.45}, config)
    assert state == -1
    assert confidence == 0.55


def test_observable_state_scores_use_no_future_target() -> None:
    """Los scores dependen solo de minuto, marcador y presión observable."""

    scores = _state_scores(-1, 80, 4.0, 1.0, 7.0, 2.0, LabelingConfig())
    state, confidence = _label_state(scores, LabelingConfig())
    assert state == 2
    assert confidence >= 0.60


def test_hawkes_grid_contains_only_subcritical_candidates() -> None:
    """La cuadrícula descarta configuraciones Hawkes explosivas."""

    candidates = _hawkes_candidates(CalibrationConfig())
    assert candidates
    assert all(_radius(candidate.branching_matrix) < 1.0 for candidate in candidates)
    assert all(candidate.memory_minutes == 30.0 for candidate in candidates)


def test_competition_map_reads_versioned_dataset_envelope() -> None:
    """El baseline conserva `competition_id` dentro de la clave `rows`."""

    competitions = _competition_map()
    assert len(competitions) == 381
    assert set(competitions.values()) == {"esp.1"}


def test_sparse_transition_cells_are_not_silently_accepted() -> None:
    """La cobertura reporta celdas escasas y no las oculta con suavizado."""

    counts = np.asarray([[100, 2, 1], [3, 100, 4], [2, 5, 100]])
    states = {"equilibrio": 100, "repliegue": 100, "asedio": 100, "unknown": 10}
    coverage = _transition_coverage(counts, states, CalibrationConfig())
    assert not coverage["all_transition_cells_meet_minimum"]
    assert coverage["sparse_cells_below_30"]


# Version: 1.0.0
# Created: 2026-07-16
