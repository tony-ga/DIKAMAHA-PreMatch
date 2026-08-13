"""La caché de catálogos sobrevive al reinicio del proceso.

La caché en memoria resolvía el caso de varios clientes pidiendo lo mismo a la
vez, pero moría en cada despliegue: el primer usuario tras cada publicación
volvía a pagar el barrido completo (~30s contra 63 ligas x 3 días). El nivel
persistente existe para que ese arranque en frío deje de ocurrir.

Nada de lo que guarda es evidencia: son copias derivadas y reconstruibles, de
modo que la tabla puede truncarse sin pérdida y ningún fallo suyo debe llegar
al usuario.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import src.dikamaha_service as service
from src.catalog_cache_store import (
    CatalogCacheBase,
    CatalogCacheEntry,
    CatalogCacheStore,
    LazySessionFactory,
    build_store,
)


def _store() -> CatalogCacheStore:
    """Crea un almacén sobre SQLite en memoria.

    `StaticPool` y `check_same_thread=False` no son un detalle cosmético: una
    base SQLite en memoria vive dentro de su conexión, y la caché escribe y lee
    desde un hilo aparte (`asyncio.to_thread`, para no bloquear el event loop
    con SQLAlchemy síncrono). Con el pool por defecto ese hilo abriría una base
    vacía y el test comprobaría exactamente nada. En producción es PostgreSQL y
    la cuestión no se plantea.
    """

    engine = create_engine(
        "sqlite://", future=True, poolclass=StaticPool,
        connect_args={"check_same_thread": False})
    CatalogCacheBase.metadata.create_all(engine)
    return CatalogCacheStore(
        sessionmaker(bind=engine, expire_on_commit=False, class_=Session))


def test_store_round_trips_payload_with_its_age() -> None:
    """Lo guardado vuelve intacto y con la edad real del cálculo."""

    store = _store()
    store.save("live:{}", {"fixtures": [{"match_id": 7}], "count": 1}, 600.0)

    restored = store.load("live:{}")

    assert restored is not None
    payload, age = restored
    assert payload["fixtures"] == [{"match_id": 7}]
    assert 0.0 <= age < 5.0


def test_store_ignores_expired_entries() -> None:
    """Una entrada agotada no se sirve aunque siga en la tabla."""

    store = _store()
    store.save("live:{}", {"count": 1}, 600.0)
    with store._factory() as session:  # noqa: SLF001 - fija el reloj sin esperar
        with session.begin():
            entry = session.get(CatalogCacheEntry, "live:{}")
            assert entry is not None
            entry.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

    assert store.load("live:{}") is None


def test_store_overwrites_the_same_key() -> None:
    """Un barrido nuevo reemplaza al anterior en vez de acumular filas."""

    store = _store()
    store.save("live:{}", {"count": 1}, 600.0)
    store.save("live:{}", {"count": 2}, 600.0)

    restored = store.load("live:{}")

    assert restored is not None and restored[0]["count"] == 2


def test_store_failure_never_reaches_the_caller() -> None:
    """Un PostgreSQL caído degrada a caché en memoria, no a un error.

    Es la propiedad que justifica todo el módulo: el endpoint ya sabe calcular
    su respuesta sin caché, así que la caché no puede ser un punto de fallo
    nuevo.
    """

    class _BrokenFactory:
        """Fábrica de sesiones que siempre falla al abrir."""

        def __call__(self) -> Any:
            """Simula una base de datos inalcanzable."""

            raise OSError("database_unreachable")

    store = CatalogCacheStore(_BrokenFactory())  # type: ignore[arg-type]

    store.save("live:{}", {"count": 1}, 600.0)
    assert store.load("live:{}") is None
    assert store.purge_expired() == 0


def test_building_the_store_does_not_connect() -> None:
    """Construir el almacén no toca la base de datos.

    Conectar aquí añadía ~2s al arranque del servicio -y otros tantos cada vez
    que la base no respondía- para algo que es puro caché y que un proceso
    concreto puede no llegar a usar. El servicio tiene que poder arrancar sin
    haber hablado con PostgreSQL.
    """

    store = build_store("postgresql://nadie@127.0.0.1:1/inexistente")

    assert isinstance(store, CatalogCacheStore)


def test_failed_connection_is_not_retried_on_every_call() -> None:
    """Una base caída se descarta un rato en vez de costar el timeout cada vez.

    Sin este corte, PostgreSQL inalcanzable convertiría cada petición de
    catálogo en segundos de espera antes de degradar a memoria: exactamente la
    latencia que esta caché existe para eliminar.
    """

    factory = LazySessionFactory("postgresql://nadie@127.0.0.1:1/inexistente")
    attempts = {"count": 0}
    original = factory._connect  # noqa: SLF001 - cuenta intentos reales

    def _counting_connect() -> Any:
        """Cuenta cada intento de conexión real."""

        attempts["count"] += 1
        return original()

    factory._connect = _counting_connect  # type: ignore[method-assign]  # noqa: SLF001
    store = CatalogCacheStore(factory)

    assert store.load("k") is None
    assert store.load("k") is None
    assert store.load("k") is None
    assert attempts["count"] == 1


def test_cache_restores_from_the_store_instead_of_recomputing() -> None:
    """Un proceso recién arrancado sirve el catálogo persistido, sin barrer.

    Reproduce el arranque en frío tras un despliegue: la caché en memoria está
    vacía y la única forma de no hacer esperar al primer usuario los ~30s del
    barrido es recuperar lo que dejó el proceso anterior.
    """

    store = _store()
    store.save("live:{}", {"count": 3}, 600.0)
    cache = service.AsyncPredictionCache(
        ttl_seconds=60.0, stale_ttl_seconds=600.0, stamp_age=True,
        store=store, namespace="live")
    calls = {"count": 0}

    async def _factory() -> dict[str, Any]:
        """Cuenta barridos reales; aquí no debería ejecutarse."""

        calls["count"] += 1
        return {"count": 99}

    payload = asyncio.run(cache.get_or_compute("{}", _factory))

    assert payload["count"] == 3
    assert calls["count"] == 0


def test_cache_persists_what_it_computes() -> None:
    """El resultado de un barrido real queda disponible para el próximo arranque."""

    store = _store()
    cache = service.AsyncPredictionCache(
        ttl_seconds=60.0, stale_ttl_seconds=600.0, stamp_age=True,
        store=store, namespace="upcoming")

    async def _factory() -> dict[str, Any]:
        """Simula el barrido ESPN."""

        return {"count": 5}

    async def _exercise() -> None:
        """Calcula y cede el control para que la escritura diferida termine."""

        await cache.get_or_compute("{}", _factory)
        await asyncio.sleep(0.05)

    asyncio.run(_exercise())

    restored = store.load("upcoming:{}")
    assert restored is not None and restored[0]["count"] == 5


def test_cache_namespaces_keep_live_and_upcoming_apart() -> None:
    """Dos catálogos con filtros idénticos no se pisan en la tabla compartida.

    Las claves de próximos y en vivo son la misma cadena -ligas y fecha-, así
    que sin prefijo el segundo en escribir sobrescribiría al primero y la Mini
    App recibiría fixtures en vivo donde esperaba próximos.
    """

    store = _store()
    live = service.AsyncPredictionCache(
        ttl_seconds=60.0, stale_ttl_seconds=600.0, store=store, namespace="live")
    upcoming = service.AsyncPredictionCache(
        ttl_seconds=60.0, stale_ttl_seconds=600.0, store=store,
        namespace="upcoming")

    async def _exercise() -> None:
        """Escribe ambos catálogos bajo la misma clave de filtros."""

        await live.get_or_compute("{}", _payload("live"))
        await upcoming.get_or_compute("{}", _payload("upcoming"))
        await asyncio.sleep(0.05)

    def _payload(scope: str) -> Any:
        """Fabrica un catálogo identificable por su origen."""

        async def _factory() -> dict[str, Any]:
            """Devuelve el catálogo del ámbito indicado."""

            return {"scope": scope}

        return _factory

    asyncio.run(_exercise())

    assert store.load("live:{}")[0]["scope"] == "live"  # type: ignore[index]
    assert store.load("upcoming:{}")[0]["scope"] == "upcoming"  # type: ignore[index]


def test_restored_entry_keeps_its_age_and_triggers_a_refresh() -> None:
    """Lo recuperado no rejuvenece: si venía vencido, se refresca por detrás.

    Reinsertar con edad cero haría que una entrada de hace diez minutos
    pareciera recién calculada, y el refresco en segundo plano no se dispararía
    hasta que volviera a vencer.
    """

    store = _store()
    store.save("live:{}", {"count": 1}, 600.0)
    with store._factory() as session:  # noqa: SLF001 - envejece sin esperar
        with session.begin():
            entry = session.get(CatalogCacheEntry, "live:{}")
            assert entry is not None
            entry.computed_at = datetime.now(timezone.utc) - timedelta(seconds=120)
    cache = service.AsyncPredictionCache(
        ttl_seconds=30.0, stale_ttl_seconds=600.0, stamp_age=True,
        store=store, namespace="live")
    calls = {"count": 0}

    async def _factory() -> dict[str, Any]:
        """Cuenta los refrescos en segundo plano."""

        calls["count"] += 1
        return {"count": 2}

    async def _exercise() -> dict[str, Any]:
        """Lee la entrada vieja y espera al refresco que debe disparar."""

        restored = await cache.get_or_compute("{}", _factory)
        await asyncio.sleep(0.05)
        return restored

    restored = asyncio.run(_exercise())

    # Se sirve lo persistido al instante, con su edad real...
    assert restored["count"] == 1
    assert restored["data_age_seconds"] > 60
    # ...y el refresco se disparó porque la entrada llegó ya vencida.
    assert calls["count"] == 1
