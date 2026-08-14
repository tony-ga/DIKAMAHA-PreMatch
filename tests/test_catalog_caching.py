"""El catálogo de próximos y en vivo colapsa peticiones concurrentes.

Existe por una incidencia real de producción: un pico medido a los 8 vCPU del
límite del contenedor, justo después de un despliegue, coincidiendo con
`/v1/upcoming` tardando 9-18s (normalmente milisegundos) y dos llamadas a
`/v1/predict/upcoming`, sin relación alguna con el catálogo, agotando el
timeout de 30s y devolviendo 504. Causa: ningún endpoint de catálogo cacheaba
nada, así que cada cliente (Mini App, bot, worker, varios usuarios) disparaba
su propio barrido completo de hasta 63 ligas en ESPN, y esa contención de CPU
robaba tiempo a predicciones concurrentes que no tenían nada que ver.

El TTL elegido está alineado con el refresco de cliente ya documentado en
Fase 115 (60s para próximos, 20s para en vivo), así que cachear no envejece el
dato más de lo que el usuario ya tolera.

Sobre ese TTL hay un segundo umbral (`stale_ttl_seconds`): pasada la ventana
fresca la entrada se sigue sirviendo al instante mientras se recalcula por
detrás. Sin él, con el tráfico real de un grupo privado casi ninguna apertura
de la Mini App caía dentro de la ventana de 25-45s y en la práctica cada
usuario volvía a pagar el barrido completo, que es justo lo que esta caché
existía para evitar.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi.testclient import TestClient

import src.dikamaha_service as service
from src.dikamaha_service import ServiceConfig, create_app


def test_upcoming_catalog_deduplicates_identical_requests(
    monkeypatch: Any,
) -> None:
    """Dos peticiones idénticas comparten un único barrido ESPN."""

    calls = {"count": 0}

    def _fetch(
        payload: tuple[str, int, str | None],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Cuenta cuántas veces se ejecuta el barrido real."""

        calls["count"] += 1
        return [{"match_id": 1, "kickoff_ts": "2026-08-13T20:00:00+00:00"}], []

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

    def _fetch(
        payload: tuple[str, int, str | None],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Registra los parámetros de cada barrido real."""

        calls.append(payload)
        return [{"match_id": len(calls), "kickoff_ts": "2026-08-13T20:00:00+00:00"}], []

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

    def _flaky(
        payload: tuple[str, int, str | None],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Falla la primera vez, funciona la segunda."""

        calls["count"] += 1
        if calls["count"] == 1:
            raise ValueError("espn_transient")
        return [{"match_id": 1, "kickoff_ts": "2026-08-13T20:00:00+00:00"}], []

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
        progress_key: str | None = None,
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


def test_upcoming_catalog_shares_one_sweep_across_limits(
    monkeypatch: Any,
) -> None:
    """Pedir distinto número de partidos no dispara un barrido nuevo.

    La Mini App pide `limit=4` en el panel y `limit=20` en la vista de
    próximos. Con `limit` dentro de la clave de caché eran dos entradas
    distintas y abrir la app y navegar pagaba dos barridos completos de las
    mismas ligas y fechas, aunque el barrido es idéntico y `limit` sólo
    recorta la lista ya ordenada al final.
    """

    calls = {"count": 0}
    fixtures = [
        {"match_id": index, "kickoff_ts": f"2026-08-13T{18 + index:02d}:00:00+00:00"}
        for index in range(6)
    ]

    def _fetch(
        payload: tuple[str, int, str | None],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Devuelve el catálogo completo y cuenta los barridos reales."""

        calls["count"] += 1
        return fixtures[:payload[1]], []

    monkeypatch.setattr(service, "_upcoming_catalog", _fetch)
    client = TestClient(create_app(
        ServiceConfig(mode="operational_readonly", external_calls_enabled=True)))

    narrow = client.get("/v1/upcoming", params={"leagues": "esp.1", "limit": 4})
    wide = client.get("/v1/upcoming", params={"leagues": "esp.1", "limit": 20})

    assert calls["count"] == 1
    assert narrow.json()["count"] == 4
    assert narrow.json()["fixtures"] == fixtures[:4]
    # El recorte es un prefijo del mismo orden, no un catálogo distinto.
    assert wide.json()["fixtures"][:4] == narrow.json()["fixtures"]
    assert wide.json()["count"] == len(fixtures)


def test_live_catalog_shares_one_sweep_across_limits() -> None:
    """El panel (`limit=4`) y la vista live (`limit=20`) comparten el barrido."""

    runtime = _CountingLiveRuntime()
    client = TestClient(create_app(
        ServiceConfig(mode="operational_readonly", external_calls_enabled=True),
        live_runtime=runtime))

    client.get("/v1/live", params={"leagues": "esp.1", "limit": 4})
    client.get("/v1/live", params={"leagues": "esp.1", "limit": 20})

    assert runtime.calls == 1


def test_catalog_serves_stale_entry_while_refreshing_behind() -> None:
    """Pasado el TTL la respuesta sigue siendo inmediata y se refresca detrás.

    Es la diferencia entre que el usuario espere el barrido completo (~30s
    contra 63 ligas x 3 días) y que reciba un dato de unos segundos de edad al
    instante. Sin esto, con el tráfico de un grupo privado casi ninguna
    apertura de la Mini App caía dentro del TTL y prácticamente cada usuario
    pagaba el barrido entero.
    """

    cache = service.AsyncPredictionCache(
        ttl_seconds=0.2, stale_ttl_seconds=60.0, stamp_age=True)
    calls = {"count": 0}

    async def _factory() -> dict[str, Any]:
        """Cuenta cada cálculo real."""

        calls["count"] += 1
        return {"value": calls["count"]}

    async def _exercise() -> tuple[dict[str, Any], dict[str, Any]]:
        """Calcula, deja vencer la entrada y vuelve a pedirla."""

        await cache.get_or_compute("k", _factory)
        await asyncio.sleep(0.3)
        stale = await cache.get_or_compute("k", _factory)
        # Cede el control para que el refresco en segundo plano termine. La
        # espera es muy inferior al TTL, así que la lectura siguiente encuentra
        # la entrada recién escrita y no dispara otro refresco.
        await asyncio.sleep(0.02)
        refreshed = await cache.get_or_compute("k", _factory)
        return stale, refreshed

    stale, refreshed = asyncio.run(_exercise())

    # La lectura vencida devuelve el valor viejo sin esperar al recálculo...
    assert stale["value"] == 1
    assert stale["data_age_seconds"] > 0
    # ...y el refresco que disparó ya dejó el valor nuevo para el siguiente.
    assert refreshed["value"] == 2
    assert calls["count"] == 2


def test_catalog_background_refresh_failure_keeps_serving() -> None:
    """Si el refresco falla se sigue sirviendo lo cacheado, sin propagar el error."""

    cache = service.AsyncPredictionCache(
        ttl_seconds=0.2, stale_ttl_seconds=60.0, stamp_age=True)
    calls = {"count": 0}

    async def _flaky() -> dict[str, Any]:
        """Funciona la primera vez y falla en cada refresco posterior."""

        calls["count"] += 1
        if calls["count"] > 1:
            raise ValueError("espn_transient")
        return {"value": 1}

    async def _exercise() -> dict[str, Any]:
        """Deja vencer la entrada y la vuelve a pedir tras un refresco fallido."""

        await cache.get_or_compute("k", _flaky)
        await asyncio.sleep(0.3)
        await cache.get_or_compute("k", _flaky)
        await asyncio.sleep(0.02)
        return await cache.get_or_compute("k", _flaky)

    assert asyncio.run(_exercise())["value"] == 1


def test_high_probability_reuses_the_upcoming_catalog_sweep(
    monkeypatch: Any,
) -> None:
    """`/v1/high-probability` no barre ESPN por su cuenta.

    Antes tenía su propia clave de caché y repetía un barrido idéntico al de
    `/v1/upcoming`; sólo cambiaba cuántos fixtures conservaba al final.
    """

    calls = {"count": 0}

    def _fetch(
        payload: tuple[str, int, str | None],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Cuenta cuántas veces se ejecuta el barrido real."""

        calls["count"] += 1
        return [{"match_id": 1, "kickoff_ts": "2026-08-13T20:00:00+00:00"}], []

    class _AvailableView:
        """Vista con ambas fuentes disponibles: obliga a consultar el catálogo.

        Con las dos fuentes caídas el endpoint devuelve `unavailable` sin
        tocar el catálogo, y el test pasaría sin haber comprobado nada.
        """

        def provenance(self) -> dict[str, Any]:
            """Declara el gate y la escalera auditada como disponibles."""

            return {
                "goal_markets_gate_available": True,
                "team_markets_sha256": "deadbeef",
            }

    async def _no_picks(*_: Any) -> tuple[list[dict[str, Any]], int, int]:
        """Salta la inferencia: aquí sólo interesa cuántos barridos hubo."""

        return [], 0, 0

    monkeypatch.setattr(service, "_upcoming_catalog", _fetch)
    monkeypatch.setattr(service, "_high_probability_picks", _no_picks)
    app = create_app(
        ServiceConfig(mode="operational_readonly", external_calls_enabled=True))
    app.state.high_probability_view = _AvailableView()
    client = TestClient(app)

    client.get("/v1/upcoming", params={"leagues": "esp.1"})
    picks = client.get("/v1/high-probability", params={"leagues": "esp.1"})

    assert picks.status_code == 200
    assert picks.json()["status"] == "ok"
    assert picks.json()["fixtures_catalog_size"] == 1
    assert calls["count"] == 1


def test_warmer_fills_the_catalog_before_anyone_asks(monkeypatch: Any) -> None:
    """Con el warmer activo, la primera petición ya encuentra caché caliente.

    Es lo que convierte el barrido en una tarea de fondo: sin él, abrir la
    Mini App tras un despliegue vuelve a costar los ~30s completos porque la
    caché del proceso arranca vacía.
    """

    calls = {"count": 0}

    def _fetch(
        payload: tuple[str, int, str | None],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Cuenta cuántas veces se ejecuta el barrido real."""

        calls["count"] += 1
        return [{"match_id": 1, "kickoff_ts": "2026-08-13T20:00:00+00:00"}], []

    monkeypatch.setattr(service, "_upcoming_catalog", _fetch)
    # Sin esto el bucle dormiría 5 minutos tras su primera vuelta y el test
    # tardaría lo mismo en poder observar nada.
    monkeypatch.setattr(service, "CATALOG_WARM_IDLE_SECONDS", 30.0)
    monkeypatch.setattr(service, "CATALOG_WARM_ACTIVE_SECONDS", 30.0)
    # Aquí se comprueba el warmer, no la persistencia: con `DATABASE_URL` en el
    # entorno el primer barrido intentaría además hablar con PostgreSQL y el
    # test dependería de que esa base exista.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    runtime = _CountingLiveRuntime()
    app = create_app(
        ServiceConfig(
            mode="operational_readonly", external_calls_enabled=True,
            catalog_warmer_enabled=True),
        live_runtime=runtime)

    with TestClient(app) as client:
        # El warmer arranca con el lifespan y cada petición cede el control al
        # bucle. Se espera a que haya dado su vuelta en vez de asumir que una
        # sola llamada basta: bajo la carga de la suite completa no lo es.
        deadline = time.monotonic() + 15.0
        while (not runtime.calls or not calls["count"]) and time.monotonic() < deadline:
            client.get("/v1/health")
        first = client.get("/v1/upcoming", params={"limit": 4})

    assert first.status_code == 200
    # Un único barrido: el del warmer. La petición del usuario sólo leyó caché.
    assert calls["count"] == 1
    assert runtime.calls == 1


def test_warmer_stays_off_for_hand_built_configurations() -> None:
    """Una app construida a mano no empieza a barrer ESPN por arrancar.

    `catalog_warmer_enabled` es `False` por defecto en `ServiceConfig` y sólo
    lo activa `service_config_from_env`, para que tests y scripts que crean una
    aplicación no disparen tráfico externo periódico sin pedirlo.
    """

    runtime = _CountingLiveRuntime()
    app = create_app(
        ServiceConfig(mode="operational_readonly", external_calls_enabled=True),
        live_runtime=runtime)

    with TestClient(app) as client:
        client.get("/v1/health")

    assert app.state.catalog_warmers == ()
    assert runtime.calls == 0
