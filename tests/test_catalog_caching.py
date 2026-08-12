"""El catálogo de próximos y en vivo colapsa peticiones concurrentes.

Existe por una incidencia real de producción: un pico medido a los 8 vCPU del
límite del contenedor, justo después de un despliegue, coincidiendo con
`/v1/upcoming` tardando 9-18s (normalmente milisegundos) y dos llamadas a
`/v1/predict/upcoming`, sin relación alguna con el catálogo, agotando el
timeout de 30s y devolviendo 504. Causa: ningún endpoint de catálogo cacheaba
nada, así que cada cliente (Mini App, bot, worker, varios usuarios) disparaba
su propio barrido completo de hasta 63 ligas en ESPN, y esa contención de CPU
robaba tiempo a predicciones concurrentes que no tenían nada que ver.

El TTL elegido (45s para próximos, 15s para en vivo) es menor que el refresco
de cliente ya documentado en Fase 115 (60s y 20s respectivamente), así que
cachear no envejece el dato más de lo que el usuario ya tolera.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

import src.dikamaha_service as service
from src.dikamaha_service import ServiceConfig, create_app


def test_upcoming_catalog_deduplicates_identical_requests(
    monkeypatch: Any,
) -> None:
    """Dos peticiones idénticas comparten un único barrido ESPN."""

    calls = {"count": 0}

    def _fetch(payload: tuple[str, int, str | None]) -> list[dict[str, Any]]:
        """Cuenta cuántas veces se ejecuta el barrido real."""

        calls["count"] += 1
        return [{"match_id": 1, "kickoff_ts": "2026-08-13T20:00:00+00:00"}]

    monkeypatch.setattr(service, "_upcoming_catalog", _fetch)
    client = TestClient(create_app(
        ServiceConfig(mode="operational_readonly", external_calls_enabled=True)))

    first = client.get("/v1/upcoming", params={"leagues": "esp.1"})
    second = client.get("/v1/upcoming", params={"leagues": "esp.1"})

    assert first.status_code == second.status_code == 200
    assert first.json()["fixtures"] == second.json()["fixtures"]
    assert calls["count"] == 1


def test_upcoming_catalog_does_not_collide_across_filters(
    monkeypatch: Any,
) -> None:
    """Ligas o fechas distintas no comparten entrada de caché."""

    calls: list[tuple[str, int, str | None]] = []

    def _fetch(payload: tuple[str, int, str | None]) -> list[dict[str, Any]]:
        """Registra los parámetros de cada barrido real."""

        calls.append(payload)
        return [{"match_id": len(calls), "kickoff_ts": "2026-08-13T20:00:00+00:00"}]

    monkeypatch.setattr(service, "_upcoming_catalog", _fetch)
    client = TestClient(create_app(
        ServiceConfig(mode="operational_readonly", external_calls_enabled=True)))

    client.get("/v1/upcoming", params={"leagues": "esp.1"})
    client.get("/v1/upcoming", params={"leagues": "eng.1"})
    client.get("/v1/upcoming", params={"leagues": "esp.1", "date": "20260813"})

    assert len(calls) == 3


def test_upcoming_catalog_failure_is_not_cached(monkeypatch: Any) -> None:
    """Un barrido fallido no envenena la caché para el siguiente intento."""

    calls = {"count": 0}

    def _flaky(payload: tuple[str, int, str | None]) -> list[dict[str, Any]]:
        """Falla la primera vez, funciona la segunda."""

        calls["count"] += 1
        if calls["count"] == 1:
            raise ValueError("espn_transient")
        return [{"match_id": 1, "kickoff_ts": "2026-08-13T20:00:00+00:00"}]

    monkeypatch.setattr(service, "_upcoming_catalog", _flaky)
    client = TestClient(create_app(
        ServiceConfig(mode="operational_readonly", external_calls_enabled=True)))

    first = client.get("/v1/upcoming", params={"leagues": "esp.1"})
    second = client.get("/v1/upcoming", params={"leagues": "esp.1"})

    assert first.status_code == 422
    assert second.status_code == 200
    assert calls["count"] == 2


class _CountingLiveRuntime:
    """Runtime live que cuenta cuántas veces se le consulta de verdad."""

    policy: dict[str, Any] = {}

    def __init__(self) -> None:
        """Arranca el contador de llamadas reales."""

        self.calls = 0

    def list_active(
        self, leagues: str, limit: int, selected_date: str | None,
    ) -> dict[str, Any]:
        """Registra la llamada y devuelve un catálogo mínimo."""

        self.calls += 1
        return {"fixtures": [], "count": 0, "status": "ok"}


def test_live_catalog_deduplicates_identical_requests() -> None:
    """El catálogo en vivo también colapsa peticiones concurrentes idénticas."""

    runtime = _CountingLiveRuntime()
    client = TestClient(create_app(
        ServiceConfig(mode="operational_readonly", external_calls_enabled=True),
        live_runtime=runtime))

    client.get("/v1/live", params={"leagues": "esp.1"})
    client.get("/v1/live", params={"leagues": "esp.1"})
    client.get("/v1/live", params={"leagues": "eng.1"})

    assert runtime.calls == 2
