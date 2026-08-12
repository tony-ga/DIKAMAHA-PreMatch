"""Ancla que `/v1/high-probability` tiene el mismo timeout extendido que
`/v1/live` y `/v1/upcoming`.

Incidente real: `_call_with_timeout` (`src/dikamaha_service.py`) envuelve
cada request con un timeout que depende de la ruta -7x para los catálogos
multi-fixture, 1x por defecto-. Fase 122 nunca agregó su propia ruta a la
lista de 7x pese a barrer el mismo tipo de catálogo multi-liga, así que el
propio servidor se cortaba a sí mismo con 504 `inference_timeout` antes de
terminar el barrido. Detectado por el primer ciclo real de Fase 123 contra
producción (`docs/decision_log.md`).
"""

from __future__ import annotations

from typing import Any

import pytest

from src.dikamaha_service import ServiceConfig, _call_with_timeout


class _Url:
    def __init__(self, path: str) -> None:
        self.path = path


class _Request:
    def __init__(self, path: str) -> None:
        self.url = _Url(path)


@pytest.mark.asyncio
async def test_high_probability_gets_the_same_timeout_multiplier_as_upcoming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El multiplicador real usado debe coincidir para ambas rutas."""

    captured: list[float] = []

    async def _fake_wait_for(coro: Any, timeout: float) -> Any:
        captured.append(timeout)
        coro.close()
        return None

    monkeypatch.setattr(
        "src.dikamaha_service.asyncio.wait_for", _fake_wait_for)
    config = ServiceConfig(inference_timeout_seconds=10.0)

    async def _call_next(_: _Request) -> None:
        return None

    await _call_with_timeout(_Request("/v1/upcoming"), _call_next, config)
    await _call_with_timeout(
        _Request("/v1/high-probability"), _call_next, config)

    assert len(captured) == 2
    assert captured[0] == captured[1] == 10.0 * 7.0 + 1.0


@pytest.mark.asyncio
async def test_unlisted_routes_keep_the_default_multiplier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Una ruta sin barrido multi-fixture no debe heredar el timeout largo."""

    captured: list[float] = []

    async def _fake_wait_for(coro: Any, timeout: float) -> Any:
        captured.append(timeout)
        coro.close()
        return None

    monkeypatch.setattr(
        "src.dikamaha_service.asyncio.wait_for", _fake_wait_for)
    config = ServiceConfig(inference_timeout_seconds=10.0)

    async def _call_next(_: _Request) -> None:
        return None

    await _call_with_timeout(_Request("/v1/health"), _call_next, config)

    assert captured == [10.0 * 1.0 + 1.0]


# Version: 1.0.0
# Created: 2026-08-12
