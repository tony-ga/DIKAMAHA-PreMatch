"""Pruebas de preproducción para Fase 107."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from fastapi.testclient import TestClient

from src.dikamaha_service import AsyncPredictionCache, ServiceConfig, create_app
from src.telegram_channel_publisher import (
    FrozenPrediction,
    _card_text,
    _market_texts,
)


def _row() -> FrozenPrediction:
    """Construye una predicción congelada representativa."""

    fixture = {
        "league_name": "Liga MX", "home_team_name": "Puebla",
        "away_team_name": "Guadalajara"}
    prediction = {
        "probability_home": 0.5, "probability_draw": 0.3,
        "probability_away": 0.2, "probability_over_2_5": 0.6,
        "probability_btts": 0.55,
        "experimental_team_markets": {"user_market_view": [{
            "period": "first_half", "metric": "corners",
            "team_side": "home", "line": 2.5, "probability": 0.62,
            "baseline_probability": 0.51,
        }]},
    }
    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    return FrozenPrediction(
        "mex.1:1", "2030-01-01", "mex.1", 1, "1", now,
        fixture, prediction, "hash", now)


def test_public_telegram_prediction_hides_internal_terms() -> None:
    """Evita vocabulario técnico en tarjeta y mercados públicos."""

    output = "\n".join([_card_text(_row()), *_market_texts(_row())]).lower()
    forbidden = (
        "espn", "experimental", "shadow", "baseline", "dixon",
        "kalman", "markov", "poisson")
    assert not any(term in output for term in forbidden)


def test_prediction_cache_single_flight_computes_once() -> None:
    """Comparte una inferencia concurrente idéntica."""

    calls = 0

    async def scenario() -> list[dict[str, Any]]:
        """Lanza veinte consumidores de la misma clave."""

        cache = AsyncPredictionCache()

        async def calculate() -> dict[str, Any]:
            """Cuenta y retrasa el único cálculo esperado."""

            nonlocal calls
            calls += 1
            await asyncio.sleep(0.02)
            return {"probability": 0.6}

        return await asyncio.gather(*[
            cache.get_or_compute("fixture", calculate) for _ in range(20)])

    results = asyncio.run(scenario())
    assert calls == 1
    assert all(row == {"probability": 0.6} for row in results)


def test_capacity_rejection_includes_retry_after() -> None:
    """Permite que clientes reintenten una saturación controlada."""

    app = create_app(ServiceConfig(max_concurrent_requests=1))
    gate = app.state.request_gate
    assert gate.enter("busy", 0.0) is None
    response = TestClient(app).get("/v1/metrics")
    gate.leave()
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "1"


# Version: 1.0.0
# Created: 2026-07-29
