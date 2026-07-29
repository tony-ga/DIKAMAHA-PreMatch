"""Infraestructura PostgreSQL read-only para auditorías de staging.

El módulo importa SQLAlchemy de forma diferida para poder informar
dependencias ausentes sin fallar durante la importación.

Requirements:
    - SQLAlchemy==2.0.41
    - psycopg2-binary==2.9.10

Version: 1.0.0
Created: 2026-07-16
"""

from __future__ import annotations

import importlib.util
import os
import re
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Any, Callable, Iterator

FORBIDDEN_SQL = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|CREATE|ALTER|DROP|TRUNCATE|GRANT|REVOKE|"
    r"COPY|CALL|DO|VACUUM|ANALYZE|REFRESH|REINDEX|CLUSTER|COMMENT)\b",
    re.IGNORECASE,
)
URL_CREDENTIALS = re.compile(r"(postgres(?:ql)?(?:\+\w+)?://)[^@\s]+@", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class DatabaseCapabilities:
    """Capacidades necesarias para ejecutar la auditoría."""

    database_url_present: bool
    sqlalchemy_available: bool
    psycopg2_available: bool

    @property
    def ready(self) -> bool:
        """Indica si puede abrirse una conexión reproducible."""

        return all(asdict(self).values())

    def missing(self) -> list[str]:
        """Lista dependencias o configuración ausentes."""

        return [key for key, value in asdict(self).items() if not value]


def detect_capabilities(
    database_url: str | None = None,
    finder: Callable[[str], Any] = importlib.util.find_spec,
) -> DatabaseCapabilities:
    """Detecta configuración y drivers sin abrir conexiones."""

    url = database_url if database_url is not None else os.getenv("DATABASE_URL")
    return DatabaseCapabilities(
        database_url_present=bool(url),
        sqlalchemy_available=finder("sqlalchemy") is not None,
        psycopg2_available=finder("psycopg2") is not None,
    )


def sanitize_error(error: BaseException | str, database_url: str | None = None) -> str:
    """Elimina credenciales y limita el diagnóstico."""

    message = str(error)
    if database_url:
        message = message.replace(database_url, "[REDACTED_DATABASE_URL]")
    return URL_CREDENTIALS.sub(r"\1***:***@", message)[:1000]


def ensure_select(statement: str) -> str:
    """Acepta únicamente una sentencia SELECT sin palabras de escritura."""

    normalized = " ".join(statement.strip().split())
    if not normalized.upper().startswith("SELECT "):
        raise ValueError("readonly_query_must_start_with_select")
    if FORBIDDEN_SQL.search(normalized):
        raise ValueError("readonly_query_contains_forbidden_sql")
    if ";" in normalized.rstrip(";"):
        raise ValueError("readonly_query_multiple_statements")
    return normalized.rstrip(";")


def counts_identical(before: dict[str, int], after: dict[str, int]) -> bool:
    """Compara conteos exactos sin imputar valores ausentes."""

    return bool(before) and before == after


def _load_sqlalchemy() -> tuple[Any, Any, Any]:
    """Importa SQLAlchemy y sus componentes requeridos."""

    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import NullPool

    return create_engine, text, NullPool


def database_error_types() -> tuple[type[BaseException], ...]:
    """Devuelve errores de conexión/driver que justifican estado incompleto."""

    import psycopg2
    from sqlalchemy.exc import SQLAlchemyError

    return (OSError, psycopg2.Error, SQLAlchemyError)


class ReadonlySession:
    """Wrapper que impide emitir SQL fuera de SELECT."""

    def __init__(self, connection: Any, text_factory: Callable[[str], Any]) -> None:
        """Inicializa una sesión sobre una conexión read-only."""

        self._connection = connection
        self._text = text_factory
        self.statements: list[str] = []

    def rows(
        self, statement: str, parameters: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Ejecuta SELECT y devuelve mappings serializables."""

        normalized = ensure_select(statement)
        self.statements.append(normalized)
        result = self._connection.execute(self._text(normalized), parameters or {})
        return [dict(row) for row in result.mappings().all()]

    def scalar(self, statement: str, parameters: dict[str, Any] | None = None) -> Any:
        """Ejecuta SELECT y devuelve el primer escalar."""

        normalized = ensure_select(statement)
        self.statements.append(normalized)
        return self._connection.execute(
            self._text(normalized), parameters or {}
        ).scalar_one()


class ReadonlyDatabase:
    """Motor SQLAlchemy con conexión efímera y server read-only."""

    def __init__(
        self, database_url: str,
        engine_factory: Callable[..., Any] | None = None,
        text_factory: Callable[[str], Any] | None = None,
        pool_class: Any | None = None,
    ) -> None:
        """Crea el motor sin registrar ni exponer la URL."""

        if engine_factory is None or text_factory is None or pool_class is None:
            engine_factory, text_factory, pool_class = _load_sqlalchemy()
        self._text_factory = text_factory
        self._engine = engine_factory(
            database_url,
            poolclass=pool_class,
            connect_args={
                "connect_timeout": 5,
                "application_name": "dikamaha_phase_7_5_readonly",
                "options": (
                    "-c default_transaction_read_only=on "
                    "-c statement_timeout=15000 -c lock_timeout=2000"
                ),
            },
        )
        self.closed = False
        self.statements: list[str] = []

    @contextmanager
    def session(self) -> Iterator[ReadonlySession]:
        """Abre y cierra una única conexión mediante context manager."""

        try:
            with self._engine.connect() as connection:
                session = ReadonlySession(connection, self._text_factory)
                yield session
                self.statements.extend(session.statements)
                if connection.in_transaction():
                    connection.rollback()
        finally:
            self._engine.dispose()
            self.closed = True


# Version: 1.0.0
# Created: 2026-07-16
