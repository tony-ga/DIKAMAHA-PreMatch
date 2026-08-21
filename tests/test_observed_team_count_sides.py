"""El vocabulario de lados que se valida debe ser el que se puede resolver.

Este archivo existe por un fallo que ocurrió dos veces con la misma forma:
DEC-190 lo corrigió en el eje del *periodo* (`full_match` no existía como clave
de `explorer_statistics`) y volvió a aparecer en el eje del *lado* (`total`
tampoco existía). En ambos casos el sistema **validaba** un vocabulario que
después no sabía resolver, y el síntoma era un `None` indistinguible de un dato
ausente de verdad: 1,645 picks congelados en producción con lado `total` y cero
liquidados jamás.

El guard no comprueba un caso concreto sino la invariante: cada combinación de
lado y periodo que el sistema admite tiene que resolverse contra una estadística
completa. Añadir un lado o un periodo al vocabulario sin enseñar al resolutor a
leerlo rompe estas pruebas en vez de acumular picks muertos en silencio.
"""
from __future__ import annotations

import pytest

from src.high_probability_settlement import _LADDER_SIDES
from src.settlement_store import (
    STATISTICS_SIDES,
    TOTAL_SIDE,
    observed_team_count,
)
from src.team_count_market_runtime import MARKET_METADATA

PERIODS = ("first_half", "second_half", "full_match")
METRICS = ("corners", "shots", "shots_on_target", "yellow_cards")


def _statistics(home: int = 3, away: int = 4) -> dict:
    """Estadística con la forma exacta que produce `_period_statistics`.

    Indexada por lado, y con `total` como *periodo* -suma de ambas mitades-,
    nunca como lado. Reproducir esa forma es el punto de la prueba: un stub que
    inventara un lado `total` ocultaría justamente el fallo que se vigila.
    """

    def side(value: int) -> dict:
        return {
            "first_half": {metric: value for metric in METRICS},
            "second_half": {metric: value for metric in METRICS},
            "total": {metric: value * 2 for metric in METRICS},
        }

    return {"home": side(home), "away": side(away)}


# --- la invariante ---------------------------------------------------------

@pytest.mark.parametrize("side", sorted(_LADDER_SIDES))
@pytest.mark.parametrize("period", PERIODS)
@pytest.mark.parametrize("metric", METRICS)
def test_every_accepted_side_and_period_resolves(side, period, metric):
    """Todo lado/periodo admitido debe dar un número, nunca `None`."""

    observed = observed_team_count(_statistics(), side, period, metric)
    assert observed is not None, (
        f"lado={side} periodo={period} metrica={metric} se valida pero no se "
        "puede resolver: es el fallo de DEC-190 repetido")
    assert isinstance(observed, (int, float))


def test_every_catalog_market_resolves():
    """Ninguna línea del catálogo puede quedar sin liquidar por su lado.

    `shots_on_target_total_over_7_5` es la que destapó el fallo: está en
    `APPROVED_MARKETS` con lado `total` y nunca liquidó.
    """

    statistics = _statistics()
    unresolved = [
        key for key, (metric, side, period, _line, _source)
        in MARKET_METADATA.items()
        if observed_team_count(statistics, side, period, metric) is None
    ]
    assert unresolved == [], f"mercados del catálogo sin resolver: {unresolved}"


# --- semántica del total ---------------------------------------------------

def test_total_side_is_the_sum_of_both_teams():
    """`total` significa ambos equipos, no una clave que el proveedor traiga."""

    statistics = _statistics(home=3, away=4)
    assert observed_team_count(statistics, TOTAL_SIDE, "first_half", "corners") == 7
    assert observed_team_count(statistics, TOTAL_SIDE, "second_half", "corners") == 7
    # `full_match` se traduce a la clave `total` del periodo, que ya es la suma
    # de ambas mitades: 6 del local más 8 del visitante.
    assert observed_team_count(statistics, TOTAL_SIDE, "full_match", "corners") == 14


def test_total_never_reports_a_partial_sum():
    """Con un solo lado publicado no hay total: devuelve `None`.

    Sumar lo disponible daría un número plausible y equivocado, y liquidaría la
    línea contra un dato que no es el total. Es el modo de fallo más caro
    posible aquí, porque nada lo delataría después.
    """

    complete = _statistics()
    for missing in STATISTICS_SIDES:
        partial = {k: v for k, v in complete.items() if k != missing}
        assert observed_team_count(partial, TOTAL_SIDE, "full_match", "corners") is None

    empty_side = {**complete, "away": {"total": {}}}
    assert observed_team_count(empty_side, TOTAL_SIDE, "full_match", "corners") is None


def test_single_sides_are_unaffected_by_the_fix():
    """La lectura de `home`/`away` sigue siendo exactamente la de antes."""

    statistics = _statistics(home=3, away=4)
    assert observed_team_count(statistics, "home", "first_half", "corners") == 3
    assert observed_team_count(statistics, "away", "second_half", "shots") == 4
    assert observed_team_count(statistics, "home", "full_match", "corners") == 6


def test_missing_data_still_returns_none():
    """Un dato realmente ausente sigue devolviendo `None`, no un cero."""

    assert observed_team_count({}, "home", "full_match", "corners") is None
    assert observed_team_count(_statistics(), "home", "full_match", "offsides") is None
    assert observed_team_count(_statistics(), "nonexistent", "full_match", "corners") is None
