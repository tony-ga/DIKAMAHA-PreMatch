"""Pruebas del contrato `model_composition v1`.

Estas pruebas no verifican que el sistema sea correcto: **fijan hallazgos** de la
auditoría de composición (`DEC-196`, `DEC-197`, `DEC-198`) como propiedades
ejecutables, de modo que corregir cualquiera de ellos haga fallar la prueba y
obligue a volver al `decision_log`.

Una prueba que falla aquí no significa necesariamente una regresión. Puede
significar que alguien resolvió el hallazgo -que es el resultado deseado-, en
cuyo caso lo correcto es actualizar la decisión correspondiente y esta prueba
junto con ella, no silenciarla.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.hawkes_v1 import HawkesV1
from src.kalman_v2 import KalmanV2Config, KalmanV2Filter, poisson_matrix


def _trace(state) -> float:
    """Devuelve la traza de la covarianza de un estado Kalman."""

    return float(np.trace(np.asarray(state.covariance, dtype=float)))


def _seeded_state(filter_: KalmanV2Filter):
    """Construye un estado inicial con cuatro equipos."""

    return filter_._init_state([1, 2, 3, 4], "2026-01-01T00:00:00+00:00")


# --------------------------------------------------------------------------
# DEC-197 — el paso de predicción temporal existe y escala con Δt (R1)
# --------------------------------------------------------------------------


def _rate_config(rate: float) -> KalmanV2Config:
    """Configuración con tasa de ruido de proceso uniforme."""

    return KalmanV2Config(
        process_noise_attack=rate,
        process_noise_defense=rate,
        process_noise_home_advantage=rate * 0.2,
    )


def test_zero_rates_reproduce_the_pre_dec197_filter_exactly() -> None:
    """Con las tasas en cero el paso de predicción es la identidad.

    Es la vía de desactivación exacta, no una aproximación: permite comparar
    contra el comportamiento anterior sin mantener dos ramas de código.
    """

    filter_ = KalmanV2Filter(_rate_config(0.0))
    state = _seeded_state(filter_)
    before = _trace(state)

    predicted = filter_._predict_step(state, elapsed_days=45.0)

    assert _trace(predicted) == pytest.approx(before)


def test_predict_step_grows_covariance_proportionally_to_elapsed_time() -> None:
    """`Q` escala con el intervalo, no es constante por partido.

    R1 y la evidencia del corpus (Murphy p.1042): con observaciones a intervalos
    desiguales, la difusión acumulada depende de la duración del intervalo. Un
    `Q` constante por observación supondría que todos los partidos están
    igualmente separados, y el calendario real no cumple eso nunca.
    """

    filter_ = KalmanV2Filter(_rate_config(0.01))
    state = _seeded_state(filter_)
    base = _trace(state)

    short = _trace(filter_._predict_step(state, elapsed_days=3.0)) - base
    normal = _trace(filter_._predict_step(state, elapsed_days=7.0)) - base
    long_gap = _trace(filter_._predict_step(state, elapsed_days=21.0)) - base

    assert short > 0.0
    assert normal == pytest.approx(short * 7.0 / 3.0, rel=1e-6)
    assert long_gap == pytest.approx(short * 21.0 / 3.0, rel=1e-6)


def test_elapsed_time_is_capped_for_the_summer_break() -> None:
    """Un parón largo no borra todo lo aprendido.

    Sin tope, cuatro meses de pretemporada inyectarían tanta varianza que el
    estado equivaldría a no saber nada del equipo, que es tan falso como no
    olvidar nada.
    """

    config = _rate_config(0.01)
    filter_ = KalmanV2Filter(config)
    state = _seeded_state(filter_)

    capped = _trace(filter_._predict_step(state, config.max_elapsed_days))
    beyond = _trace(filter_._predict_step(state, config.max_elapsed_days * 3))

    assert beyond == pytest.approx(capped)


def test_update_runs_prediction_before_absorbing_the_observation() -> None:
    """El orden de R1 está garantizado por construcción, no por convención.

    `_update_batch` ejecuta el paso de predicción internamente, de modo que un
    llamador no puede invertir el orden ni olvidarlo. Se comprueba comparando
    contra la composición explícita predicción-luego-actualización.
    """

    filter_ = KalmanV2Filter(_rate_config(0.01))
    state = _seeded_state(filter_)
    updates = [(1, 2, 2, 1, 1.4, 1.1)]

    combined = filter_._update_batch(state, updates, elapsed_days=9.0)
    explicit = filter_._update_batch(
        filter_._predict_step(state, elapsed_days=9.0), updates)

    assert _trace(combined) == pytest.approx(_trace(explicit))
    assert np.allclose(
        np.asarray(filter_._vector_from_state(combined), dtype=float),
        np.asarray(filter_._vector_from_state(explicit), dtype=float))


def test_process_noise_keeps_the_filter_responsive_to_recent_matches() -> None:
    """Con `Q > 0` la ganancia deja de decaer hacia cero.

    Éste es el defecto que `DEC-197` describía: sin ruido de proceso, el mismo
    residuo mueve el estado cada vez menos y el filtro converge a la estimación
    de un parámetro estático. Con `Q` escalado por Δt, un partido reciente sigue
    moviendo el estado tras acumular historia.
    """

    def _displacement_after_history(rate: float) -> float:
        """Mide cuánto mueve una observación fija tras absorber seis partidos."""

        filter_ = KalmanV2Filter(_rate_config(rate))
        state = _seeded_state(filter_)
        for home, away in ((1, 2), (3, 4), (1, 3), (2, 4), (1, 4), (2, 3)):
            state = filter_._update_batch(
                state, [(home, away, 1, 1, 1.3, 1.2)], elapsed_days=7.0)
        before = np.asarray(filter_._vector_from_state(state), dtype=float)
        moved = filter_._update_batch(
            state, [(1, 2, 3, 0, 1.2, 1.2)], elapsed_days=7.0)
        after = np.asarray(filter_._vector_from_state(moved), dtype=float)
        return float(np.linalg.norm(after - before))

    assert _displacement_after_history(0.01) > _displacement_after_history(0.0)


def test_process_noise_preserves_covariance_symmetry_and_psd() -> None:
    """El paso de predicción no rompe las invariantes de la covarianza."""

    filter_ = KalmanV2Filter(_rate_config(0.02))
    state = filter_._update_batch(
        _seeded_state(filter_), [(1, 2, 2, 0, 1.3, 1.0)], elapsed_days=7.0)

    covariance = np.asarray(state.covariance, dtype=float)

    assert np.allclose(covariance, covariance.T, atol=1e-9)
    assert float(np.min(np.linalg.eigvalsh(covariance))) >= -1e-9


# --------------------------------------------------------------------------
# DEC-198 — Hawkes rompe la conservación de masa de DEC-092
# --------------------------------------------------------------------------


def _exciting_events() -> list[dict[str, object]]:
    """Dos eventos excitantes dentro de la memoria del kernel."""

    return [
        {
            "event_id": "e1",
            "event_ts": "2026-01-01T00:50:00+00:00",
            "event_type": "shot_on_target",
            "team_id": 10,
        },
        {
            "event_id": "e2",
            "event_ts": "2026-01-01T00:55:00+00:00",
            "event_type": "corner",
            "team_id": 20,
        },
    ]


def test_hawkes_breaks_markov_mass_conservation() -> None:
    """`lambda_hawkes` excede estrictamente a `lambda_markov`.

    `DEC-092` congela que Markov redistribuye la masa de las lambdas
    Dixon-Coles/Kalman sin alterarla. Hawkes suma un término de excitación no
    negativo, que es la definición correcta de un proceso autoexcitado. Ambas
    piezas son correctas por separado y no pueden serlo encadenadas: sumar algo
    positivo a una masa conservada la deja de conservar.

    Esta prueba documenta la incompatibilidad de `DEC-198`. No es un defecto de
    `hawkes_v1.py`: es la razón por la que Hawkes no puede reconectarse sobre
    la salida de Markov sin renormalizar o cambiar de estimador.
    """

    lambda_markov_home, lambda_markov_away = 1.30, 1.10

    result = HawkesV1().predict_snapshot(
        match_id=1,
        snapshot_ts="2026-01-01T01:00:00+00:00",
        lambda_markov_home=lambda_markov_home,
        lambda_markov_away=lambda_markov_away,
        home_team_id=10,
        away_team_id=20,
        events=_exciting_events(),
        markov_provenance={"model": "test"},
    )

    assert result["events_used"], (
        "La prueba exige eventos dentro de la memoria del kernel para que "
        "haya excitación que medir.")

    mass_in = lambda_markov_home + lambda_markov_away
    mass_out = result["lambda_hawkes_home"] + result["lambda_hawkes_away"]

    assert mass_out > mass_in, (
        "Sin excitación no hay nada que demostrar; revisa el fixture.")
    assert result["lambda_hawkes_home"] > lambda_markov_home
    assert result["lambda_hawkes_away"] > lambda_markov_away


def test_hawkes_is_conservative_only_without_exciting_events() -> None:
    """Sin eventos excitantes la masa se conserva, y sólo entonces.

    Delimita el alcance de `DEC-198`: la incompatibilidad no es accidental ni
    depende de parámetros, aparece exactamente cuando el proceso hace su
    trabajo.
    """

    lambda_markov_home, lambda_markov_away = 1.30, 1.10

    result = HawkesV1().predict_snapshot(
        match_id=1,
        snapshot_ts="2026-01-01T01:00:00+00:00",
        lambda_markov_home=lambda_markov_home,
        lambda_markov_away=lambda_markov_away,
        home_team_id=10,
        away_team_id=20,
        events=[],
        markov_provenance={"model": "test"},
    )

    assert result["lambda_hawkes_home"] == pytest.approx(lambda_markov_home)
    assert result["lambda_hawkes_away"] == pytest.approx(lambda_markov_away)


# --------------------------------------------------------------------------
# R6 — `τ` de Dixon-Coles es generativo, no una recalibración
# --------------------------------------------------------------------------


def test_dixon_coles_tau_reshapes_the_joint_matrix_not_an_aggregate() -> None:
    """`τ` altera marcadores concretos, no sólo un resumen agregado.

    R6 separa el modelo generativo de la recalibración posterior. Una
    recalibración opera sobre probabilidades ya formadas y no puede redistribuir
    masa entre marcadores individuales; `τ` sí lo hace, y sólo en las cuatro
    celdas bajas. Esta prueba fija esa distinción para que `τ` no se mueva a la
    capa de calibración.
    """

    plain = poisson_matrix(1.4, 1.1, 6, 0.0, False)
    corrected = poisson_matrix(1.4, 1.1, 6, -0.08, True)

    low_cells = {(0, 0), (1, 0), (0, 1), (1, 1)}
    changed = {
        (x, y)
        for x in range(plain.shape[0])
        for y in range(plain.shape[1])
        if abs(float(plain[x, y] - corrected[x, y])) > 1e-9
    }

    assert low_cells <= changed, (
        "`τ` debe alterar las cuatro celdas de baja puntuación.")
    assert float(corrected.sum()) == pytest.approx(1.0, abs=1e-12)
    assert float(plain.sum()) == pytest.approx(1.0, abs=1e-12)
