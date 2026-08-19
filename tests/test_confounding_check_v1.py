"""Pruebas del detector de confusión por fuerza relativa (`DEC-218`).

El criterio que verifica este módulo es el que `DEC-218` dejó documentado: un
intervalo bootstrap que no cruza cero mide precisión, no identificación, y por
sí solo no autoriza tratar un efecto como causal. La prueba principal no usa
datos sintéticos: reproduce con las cifras reales el caso de la Eurocopa
Femenina 2025 donde "más defensores que el rival" parecía predecir ganar por
más goles y resultó ser fuerza del equipo disfrazada de táctica.

Version: 1.0.0
Created: 2026-08-18
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from src.confounding_check_v1 import (
    ConfoundingObservation,
    VERDICT_CONFOUNDED,
    VERDICT_INDISTINGUISHABLE,
    VERDICT_INSUFFICIENT,
    VERDICT_POSITIVE,
    check_confounding,
)


def _formation_case() -> list[ConfoundingObservation]:
    """Los 12 partidos reales con línea de 3 contra línea de 4.

    `exposure` es la brecha de defensores (local menos visitante) y `effect`
    la diferencia de goles del local. `group_id` es el equipo local, de modo
    que los dos partidos de España caen en un único grupo -que es
    precisamente lo que permite descubrir que el efecto vivía en ellos-.

    La fuerza se deja constante a propósito: en el caso original no existía un
    Elo pre-match disponible, así que esta prueba ejercita la vía de
    influencia (excluir un grupo), no la de control por fuerza. El control por
    fuerza se prueba aparte en
    `test_an_effect_created_by_strength_is_flagged_as_confounded`.
    """

    raw = [
        ("Denmark", -1.0, -1.0),
        ("Switzerland", -1.0, -1.0),
        ("England", 1.0, 1.0),
        ("Poland", 1.0, 1.0),
        ("Italy", -1.0, -2.0),
        ("Portugal", 1.0, -1.0),
        ("Finland", 1.0, 0.0),
        ("Portugal", 1.0, 0.0),
        ("Spain", 1.0, 4.0),
        ("Switzerland", -1.0, 2.0),
        ("Wales", -1.0, -3.0),
        ("Spain", 1.0, 5.0),
    ]
    return [
        ConfoundingObservation(
            group_id=team, effect=goal_difference, exposure=gap, strength=0.0)
        for team, gap, goal_difference in raw
    ]


def test_the_defenders_formation_case_is_flagged_as_confounded() -> None:
    result = check_confounding(_formation_case(), replicates=2000)

    # El efecto crudo es el que se midió en su momento: +2.43 goles.
    assert result["baseline_effect"] == pytest.approx(2.4286, abs=1e-3)

    # Y el grupo que lo sostiene es España, cuya exclusión lo deja en +1.20.
    influence = result["influence"]
    assert influence["most_influential_group"] == "Spain"
    assert influence["effect_without_it"] == pytest.approx(1.2, abs=1e-3)
    assert influence["influence_ratio"] >= 0.5

    assert result["verdict"] == VERDICT_CONFOUNDED


def test_a_clean_effect_survives_every_check() -> None:
    generator = np.random.default_rng(20260818)
    observations = []
    for index in range(60):
        group = f"group_{index % 20}"
        strength = float(generator.normal())
        # El efecto es el mismo para todos los grupos y no depende de la
        # fuerza: es exactamente lo que una señal causal limpia parecería.
        for exposure in (1.0, -1.0):
            effect = 2.0 * exposure + float(generator.normal(scale=0.3))
            observations.append(ConfoundingObservation(
                group_id=group, effect=effect, exposure=exposure,
                strength=strength))

    result = check_confounding(observations, replicates=2000)

    assert not result["crosses_zero"]
    assert result["influence"]["influence_ratio"] < 0.5
    assert result["verdict"] == VERDICT_POSITIVE


def test_pure_noise_is_indistinguishable() -> None:
    generator = np.random.default_rng(7)
    observations = [
        ConfoundingObservation(
            group_id=f"group_{index % 15}",
            effect=float(generator.normal()),
            exposure=1.0 if index % 2 == 0 else -1.0,
            strength=float(generator.normal()),
        )
        for index in range(120)
    ]

    result = check_confounding(observations, replicates=2000)

    assert result["crosses_zero"]
    assert result["verdict"] == VERDICT_INDISTINGUISHABLE


def test_an_effect_created_by_strength_is_flagged_as_confounded() -> None:
    """El resultado lo produce la fuerza, y la exposición sólo la acompaña.

    Es la forma pura del caso de las formaciones: quien está expuesto es
    además el más fuerte, así que el contraste crudo encuentra un efecto que
    desaparece al comparar sólo entre rivales de fuerza parecida.
    """

    generator = np.random.default_rng(99)
    observations = []
    for index in range(80):
        group = f"group_{index % 20}"
        strength = float(generator.normal())
        # La exposición es una función determinista de la fuerza: los fuertes
        # están expuestos, los débiles no. El resultado depende SÓLO de la
        # fuerza -la exposición no entra en la fórmula-.
        exposure = 1.0 if strength > 0.0 else -1.0
        effect = 3.0 * strength + float(generator.normal(scale=0.2))
        observations.append(ConfoundingObservation(
            group_id=group, effect=effect, exposure=exposure,
            strength=strength))

    result = check_confounding(observations, replicates=2000)

    assert not result["crosses_zero"], "el contraste crudo sí encuentra efecto"
    controlled = result["strength_controlled"]["effect"]
    assert abs(controlled) < abs(result["baseline_effect"])
    assert result["verdict"] == VERDICT_CONFOUNDED


def test_a_single_sided_exposure_cannot_be_contrasted() -> None:
    observations = [
        ConfoundingObservation(
            group_id=f"group_{index}", effect=1.0, exposure=1.0, strength=0.0)
        for index in range(10)
    ]

    result = check_confounding(observations, replicates=200)

    assert result["verdict"] == VERDICT_INSUFFICIENT
    assert math.isnan(result["baseline_effect"])


def test_too_few_groups_yields_no_verdict() -> None:
    observations = [
        ConfoundingObservation(
            group_id="only", effect=1.0, exposure=1.0, strength=0.0),
        ConfoundingObservation(
            group_id="other", effect=-1.0, exposure=-1.0, strength=0.0),
    ]

    result = check_confounding(observations, replicates=200)

    assert result["verdict"] == VERDICT_INSUFFICIENT


def test_non_finite_values_are_rejected() -> None:
    observations = [
        ConfoundingObservation(
            group_id="a", effect=float("inf"), exposure=1.0, strength=0.0),
        ConfoundingObservation(
            group_id="b", effect=0.0, exposure=-1.0, strength=0.0),
    ]

    with pytest.raises(ValueError, match="non_finite"):
        check_confounding(observations, replicates=200)


def test_empty_input_is_rejected() -> None:
    with pytest.raises(ValueError, match="empty_observations"):
        check_confounding([], replicates=200)


def test_the_result_is_reproducible_under_a_fixed_seed() -> None:
    observations = _formation_case()

    first = check_confounding(observations, replicates=500, seed=11)
    second = check_confounding(observations, replicates=500, seed=11)

    assert first["ci_low"] == second["ci_low"]
    assert first["ci_high"] == second["ci_high"]


def test_too_many_groups_fails_loudly_instead_of_hanging() -> None:
    """El análisis de fragilidad es cuadrático; colgarse sería el peor fallo.

    Se descubrió usando la herramienta de verdad: con ~2,600 equipos como
    grupo la llamada no terminaba en 10 minutos y no decía por qué. Ahora
    rechaza la entrada explicando cómo arreglarla.
    """

    observations = [
        ConfoundingObservation(
            group_id=f"group_{index}", effect=float(index % 3),
            exposure=1.0 if index % 2 else -1.0, strength=float(index % 5))
        for index in range(1200)
    ]

    with pytest.raises(ValueError, match="too_many_groups"):
        check_confounding(observations, replicates=100)
