"""Configuración explícita de suites para DIKAMAHA.

Las pruebas PostgreSQL son integración de solo lectura y nunca se ejecutan por
defecto. Requieren una decisión explícita mediante ``--run-postgres``.
"""

from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    """Registra la bandera de integración PostgreSQL."""

    parser.addoption(
        "--run-postgres",
        action="store_true",
        default=False,
        help="Ejecuta pruebas PostgreSQL de solo lectura con DATABASE_URL.",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Omite solo las pruebas PostgreSQL cuando no se autorizaron explícitamente."""

    if config.getoption("--run-postgres"):
        return
    marker = pytest.mark.skip(
        reason="requiere PostgreSQL de solo lectura y la bandera explícita --run-postgres"
    )
    for item in items:
        if "postgres" in item.keywords:
            item.add_marker(marker)
