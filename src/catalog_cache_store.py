"""Nivel persistente de la caché de catálogos ESPN.

La caché en memoria del proceso resuelve el caso de varios clientes pidiendo
lo mismo a la vez, pero muere en cada despliegue o reinicio del contenedor, de
modo que el primer usuario posterior volvía a pagar el barrido completo -~30 s
contra 63 ligas x 3 días-. Persistir el resultado en PostgreSQL hace que el
servicio arranque con el catálogo ya calculado y permite que varias réplicas
compartan un único barrido en vez de uno por réplica.

Lo que se guarda aquí es una copia derivada y reconstruible: no es evidencia,
no entra en ningún contrato causal y la tabla puede truncarse sin pérdida. De
ahí que ningún fallo se propague: si PostgreSQL no está disponible, el servicio
degrada a caché sólo en memoria, que es autosuficiente. Una caché no puede
convertirse en un punto de fallo nuevo para un endpoint que ya sabe calcular su
respuesta sin ella.

Version: 1.0.0
Created: 2026-08-12
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import DateTime, Text, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.types import JSON

LOGGER = logging.getLogger(__name__)


class CatalogCacheBase(DeclarativeBase):
    """Base ORM aislada de la caché de catálogos."""


class CatalogCacheEntry(CatalogCacheBase):
    """Catálogo ya calculado, con la ventana en que puede seguir sirviéndose."""

    __tablename__ = "catalog_cache"

    # `Text`, no `String(n)`: la migración 014 declara la columna como TEXT y
    # `create_all` sólo actúa sobre bases vacías (desarrollo, tests). Un tipo
    # distinto aquí haría que el esquema de desarrollo divergiera del de
    # producción sin que nada lo señalara.
    cache_key: Mapped[str] = mapped_column(Text, primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=dict)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False)


def _aware(value: datetime) -> datetime:
    """Normaliza a UTC lo que SQLite devuelve sin zona horaria.

    PostgreSQL devuelve `TIMESTAMPTZ` ya consciente, pero el mismo modelo corre
    sobre SQLite en los tests y allí la zona se pierde al leer; restarle un
    `datetime` con zona lanzaría `TypeError` justo en el camino que debe ser
    imposible de romper.
    """

    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class CatalogCacheStore:
    """Lectura y escritura tolerantes a fallo de catálogos persistidos."""

    def __init__(self, factory: Callable[[], Session]) -> None:
        """Recibe cualquier fábrica de sesiones, incluso una aún no conectada."""

        self._factory = factory

    def load(self, key: str) -> tuple[dict[str, Any], float] | None:
        """Devuelve el catálogo vigente y su edad real en segundos.

        La edad viaja con el payload porque es lo que decide si el llamador
        puede servirlo tal cual o debe además refrescarlo por detrás: una
        entrada recuperada tras un reinicio puede tener minutos de antigüedad.
        """

        try:
            with self._factory() as session:
                entry = session.get(CatalogCacheEntry, key)
                if entry is None:
                    return None
                payload = dict(entry.payload or {})
                computed_at = _aware(entry.computed_at)
                expires_at = _aware(entry.expires_at)
        except Exception as exc:  # noqa: BLE001 - ver docstring del módulo
            LOGGER.warning("Lectura de catalog_cache no disponible: %s", exc)
            return None
        now = datetime.now(timezone.utc)
        if expires_at <= now or not payload:
            return None
        return payload, max(0.0, (now - computed_at).total_seconds())

    def save(self, key: str, payload: dict[str, Any], ttl_seconds: float) -> None:
        """Sobrescribe la entrada de esta clave con el catálogo recién calculado."""

        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=max(ttl_seconds, 1.0))
        try:
            with self._factory() as session:
                with session.begin():
                    entry = session.get(CatalogCacheEntry, key)
                    if entry is None:
                        session.add(CatalogCacheEntry(
                            cache_key=key, payload=dict(payload),
                            computed_at=now, expires_at=expires_at))
                    else:
                        entry.payload = dict(payload)
                        entry.computed_at = now
                        entry.expires_at = expires_at
        except Exception as exc:  # noqa: BLE001 - ver docstring del módulo
            LOGGER.warning("Escritura de catalog_cache no disponible: %s", exc)

    def purge_expired(self) -> int:
        """Elimina entradas agotadas y devuelve cuántas se borraron."""

        try:
            with self._factory() as session:
                with session.begin():
                    deleted = session.query(CatalogCacheEntry).filter(
                        CatalogCacheEntry.expires_at <= datetime.now(timezone.utc)
                    ).delete(synchronize_session=False)
            return int(deleted or 0)
        except Exception as exc:  # noqa: BLE001 - ver docstring del módulo
            LOGGER.warning("Limpieza de catalog_cache no disponible: %s", exc)
            return 0


#: Tras un fallo de conexión, cuánto esperar antes de volver a intentarlo.
CONNECTION_RETRY_SECONDS = 60.0


class LazySessionFactory:
    """Difiere la conexión hasta la primera lectura o escritura real.

    Conectar al construir la aplicación añadía ~2 s al arranque, y otros tantos
    cada vez que la base no respondía, para algo que es puro caché y que en un
    proceso concreto puede no usarse nunca. El servicio tiene que poder
    arrancar y responder sin haber tocado PostgreSQL.

    Tras un fallo deja de intentarlo durante `CONNECTION_RETRY_SECONDS`. Sin
    ese corte, una base caída convertiría cada petición de catálogo en dos
    segundos de espera antes de degradar a memoria: exactamente la latencia que
    toda esta caché existe para eliminar.
    """

    def __init__(self, database_url: str) -> None:
        """Guarda la URL sin abrir todavía ninguna conexión."""

        self._database_url = database_url
        self._factory: sessionmaker[Session] | None = None
        self._blocked_until = 0.0

    def __call__(self) -> Session:
        """Devuelve una sesión, conectando y creando la tabla si hace falta."""

        if self._factory is None:
            if time.monotonic() < self._blocked_until:
                raise ConnectionError("catalog_cache_recently_unavailable")
            try:
                self._factory = self._connect()
            except Exception:
                self._blocked_until = time.monotonic() + CONNECTION_RETRY_SECONDS
                raise
        return self._factory()

    def _connect(self) -> sessionmaker[Session]:
        """Abre el motor y asegura la tabla sin tocar otras fases.

        `create_all` es idempotente y complementa a
        `014_create_catalog_cache.sql`: la migración es la fuente de verdad del
        esquema en producción -con sus comentarios y su CHECK-, y esto sólo
        evita que un entorno de desarrollo o de test tenga que aplicarla a mano.
        """

        engine = create_engine(self._database_url, future=True, pool_pre_ping=True)
        CatalogCacheBase.metadata.create_all(engine)
        return sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


def build_store(database_url: str) -> CatalogCacheStore:
    """Crea el almacén sin conectar: la conexión espera al primer uso."""

    return CatalogCacheStore(LazySessionFactory(database_url))
