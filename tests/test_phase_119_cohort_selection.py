"""Pruebas de la selección de cohortes disjuntas de Fase 119."""

from __future__ import annotations

from scripts.run_phase_119_bias_diagnosis_500 import _load_cohorts


def test_cohorts_are_disjoint_and_reproducible() -> None:
    """La cohorte de ajuste y la de prueba nunca se solapan."""

    tuning_a, test_a = _load_cohorts()
    tuning_b, test_b = _load_cohorts()

    assert len(tuning_a) == 500
    assert len(test_a) == 500
    tuning_ids = {row["match_id"] for row in tuning_a}
    test_ids = {row["match_id"] for row in test_a}
    assert not tuning_ids & test_ids
    assert tuning_ids == {row["match_id"] for row in tuning_b}
    assert test_ids == {row["match_id"] for row in test_b}


def test_test_cohort_is_strictly_more_recent_than_tuning_cohort() -> None:
    """La cohorte de prueba nunca es anterior a la de ajuste."""

    tuning_rows, test_rows = _load_cohorts()

    assert max(row["match_date"] for row in tuning_rows) <= min(
        row["match_date"] for row in test_rows)


# Version: 1.0.0
# Created: 2026-08-10
