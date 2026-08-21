"""Pruebas de los endpoints del Constructor de Parlays (Fase 137)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.dikamaha_service import create_app, service_config_from_env


@pytest.fixture()
def client():
    """Servicio con llamadas externas apagadas."""

    return TestClient(create_app(service_config_from_env()))


def _quote(client, legs):
    """Atajo para pedir una cotización."""

    return client.post("/v1/parlay/quote", json={"legs": legs})


def _leg(fixture: str, key: str = "home_corners_over_4_5",
         probability: float = 0.72) -> dict:
    """Pierna válida del gate sellado."""

    return {"key": key, "probability": probability, "fixture_key": fixture}


def test_quote_combines_and_publishes_delivery_ratio(client):
    """La cotización multiplica y acompaña la cifra con su ratio."""

    response = _quote(client, [_leg("esp.1:1", probability=0.8),
                               _leg("esp.1:2", probability=0.7)])
    assert response.status_code == 200
    payload = response.json()
    assert payload["joint_probability"] == pytest.approx(0.56)
    assert payload["status"] == "experimental_shadow_not_promoted"
    assert 0.0 < payload["delivery_ratio"] <= 1.5
    assert payload["expected_delivery"] < payload["joint_probability"]


def test_quote_refuses_excluded_market(client):
    """Un mercado que el gate descartó no se puede combinar por API.

    Es el hallazgo central de DEC-222: «Ambos marcan» declara 0.88 y entrega
    0.51, así que aceptarlo devolvería una conjunta con apariencia de estar
    respaldada.
    """

    response = _quote(client, [_leg("esp.1:1", key="btts", probability=0.97),
                               _leg("esp.1:2")])
    assert response.status_code == 422
    assert "not_eligible" in response.text


def test_quote_refuses_two_legs_from_the_same_match(client):
    """La regla estructural se aplica también en el borde HTTP."""

    response = _quote(client, [_leg("esp.1:1"), _leg("esp.1:1")])
    assert response.status_code == 422
    assert "same_match" in response.text


def test_quote_refuses_leg_below_threshold(client):
    """Una pierna por debajo de su umbral congelado se rechaza."""

    response = _quote(client, [_leg("esp.1:1", probability=0.45),
                               _leg("esp.1:2")])
    assert response.status_code == 422
    assert "below_threshold" in response.text


def test_quote_enforces_leg_count_bounds(client):
    """Menos de dos o más de cinco piernas no es un parlay válido."""

    assert _quote(client, [_leg("esp.1:1")]).status_code == 422
    many = [_leg(f"esp.1:{index}") for index in range(6)]
    assert _quote(client, many).status_code == 422


def test_quote_rejects_unknown_fields(client):
    """Un campo desconocido señala otro contrato: se rechaza."""

    leg = _leg("esp.1:1")
    leg["stake"] = 100
    response = _quote(client, [leg, _leg("esp.1:2")])
    assert response.status_code == 422


def test_quote_rejects_probability_out_of_range(client):
    """Una probabilidad imposible no llega siquiera al gate."""

    response = _quote(client, [_leg("esp.1:1", probability=1.4),
                               _leg("esp.1:2")])
    assert response.status_code == 422


def test_menu_requires_external_calls(client):
    """Sin llamadas externas el menú no barre el catálogo."""

    response = client.get("/v1/parlay/menu")
    assert response.status_code == 422
    assert "external_calls_disabled" in response.text


def test_menu_is_declared_in_the_openapi_contract(client):
    """Ambas rutas quedan publicadas en el contrato."""

    schema = client.get("/openapi.json").json()
    assert "/v1/parlay/menu" in schema["paths"]
    assert "/v1/parlay/quote" in schema["paths"]
