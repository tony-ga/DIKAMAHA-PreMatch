"""Pruebas de la auditoría de fiabilidad de la escalera over/under.

El criterio que verifica este módulo es el del objetivo
(`docs/objetivo_auditoria_modelos_v1.md`): una línea es apta cuando está
calibrada y además supera al comparador, con intervalo bootstrap que no cruce
cero. El porcentaje de aciertos por sí solo no basta -en la línea 0.5 de
córners acierta ~98% sin que el modelo aporte nada-, y estas pruebas anclan
justo esa distinción.
"""

from __future__ import annotations

import numpy as np

from src.ladder_audit import (
    MINIMUM_SAMPLE,
    VERDICT_BASE_RATE,
    VERDICT_EDGE,
    VERDICT_INSUFFICIENT,
    VERDICT_MISCALIBRATED,
    audit_cell,
    combined_phi,
    over_probability,
    summarize,
)


def _rng() -> np.random.Generator:
    return np.random.default_rng(12345)


def _cell(predicted, baseline, observed):
    n = len(observed)
    return audit_cell(
        predicted, baseline, observed,
        ["esp.1"] * n, list(range(n)), _rng())


def test_small_sample_never_emits_a_verdict() -> None:
    n = MINIMUM_SAMPLE - 1
    result = _cell([0.7] * n, [0.7] * n, [True] * n)

    assert result["verdict"] == VERDICT_INSUFFICIENT


def test_a_perfectly_calibrated_line_without_edge_is_base_rate_driven() -> None:
    """Declara 70% y entrega 70%, pero no mejora al baseline de liga."""

    n = 600
    observed = [index % 10 < 7 for index in range(n)]
    result = _cell([0.7] * n, [0.7] * n, observed)

    assert result["verdict"] == VERDICT_BASE_RATE
    assert abs(result["observed_rate"] - 0.7) < 0.01
    assert result["model_is_league_baseline"] is True


def test_a_line_that_declares_more_than_it_delivers_is_miscalibrated() -> None:
    """Declara 90% y entrega 60%: no publicable como cifra de confianza."""

    n = 600
    observed = [index % 10 < 6 for index in range(n)]
    result = _cell([0.9] * n, [0.9] * n, observed)

    assert result["verdict"] == VERDICT_MISCALIBRATED
    assert result["calibration_gap"] > 0.05


def test_a_calibrated_model_that_beats_the_league_baseline_has_edge() -> None:
    """El modelo distingue partidos; el baseline usa la media de todos."""

    rng = np.random.default_rng(7)
    n = 800
    truth_probability = rng.uniform(0.15, 0.85, size=n)
    observed = rng.random(n) < truth_probability
    # El modelo conoce la probabilidad real de cada partido; el baseline sólo
    # conoce la media global. Ese es exactamente el valor de una salida
    # adaptativa por partido.
    predicted = truth_probability
    baseline = np.full(n, float(truth_probability.mean()))

    result = audit_cell(
        predicted, baseline, observed,
        ["esp.1"] * n, list(range(n)), rng)

    assert result["verdict"] == VERDICT_EDGE
    assert result["skill_brier_ci95"][0] > 0.0
    assert result["model_is_league_baseline"] is False


def test_extreme_line_high_accuracy_is_not_mistaken_for_edge() -> None:
    """El caso que motiva todo el criterio.

    En una línea extrema el acierto es ~98% prediciendo siempre el mismo
    lado. Un criterio basado en aciertos declararía un éxito rotundo; el
    criterio real debe verlo como ausencia de ventaja.
    """

    rng = np.random.default_rng(3)
    n = 800
    observed = rng.random(n) < 0.98
    predicted = np.full(n, 0.98)
    baseline = np.full(n, 0.98)

    result = audit_cell(
        predicted, baseline, observed, ["esp.1"] * n, list(range(n)), rng)

    assert result["model_accuracy"] > 0.95
    assert result["verdict"] == VERDICT_BASE_RATE
    assert result["skill_brier_ci95"][0] <= 0.0


def test_model_identical_to_baseline_is_declared_explicitly() -> None:
    """Si el peso de mezcla es 0, la salida servida ES el baseline de liga.

    Sin esta señal, comparar el modelo contra sí mismo daría ventaja cero y
    parecería un empate técnico en vez de la ausencia de modelo que es.
    """

    n = 400
    observed = [index % 4 < 3 for index in range(n)]
    result = _cell([0.75] * n, [0.75] * n, observed)

    assert result["model_is_league_baseline"] is True
    assert result["skill_brier_vs_league_baseline"] == 0.0


def test_over_probability_decreases_along_the_ladder() -> None:
    """La escalera debe ser monótona: P(over) no puede crecer con la línea."""

    values = [over_probability(5.4, 0.38, threshold) for threshold in range(13)]

    assert all(
        earlier >= later for earlier, later in zip(values, values[1:]))
    assert values[0] > 0.9
    assert values[-1] < 0.1


def test_combined_phi_preserves_variance_when_summing_two_teams() -> None:
    phi = combined_phi(0.4, 5.0, 5.0)

    assert 0.0 < phi < 0.4


def test_summary_separates_edge_from_base_rate() -> None:
    cells = [
        {"metric": "shots", "verdict": VERDICT_EDGE},
        {"metric": "corners", "verdict": VERDICT_BASE_RATE,
         "model_is_league_baseline": True},
        {"metric": "corners", "verdict": VERDICT_MISCALIBRATED,
         "model_is_league_baseline": True},
    ]

    summary = summarize(cells)

    assert summary["cells"] == 3
    assert summary["publishable"] == 2
    assert summary["with_model_edge"] == 1
    assert summary["metrics_served_as_league_baseline"] == ["corners"]


# Version: 1.0.0
# Created: 2026-08-12
