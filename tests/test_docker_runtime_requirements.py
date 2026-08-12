"""Protege el contrato entre el código del runtime y la imagen Docker.

Existe por un fallo real de producción: `requirements.docker.txt` declaraba
SQLAlchemy pero ningún driver DBAPI de PostgreSQL, de modo que
`build_repository("postgresql://...")` de Fase 118 fallaba dentro del contenedor
con `ModuleNotFoundError: No module named 'psycopg2'`. Como `_settlement_store()`
captura la excepción para que la API no caiga, el historial de aciertos quedaba
degradado a `unavailable` de forma permanente y silenciosa.

La suite normal no lo detecta porque en local sí existe el driver. Estas pruebas
verifican el manifiesto, no el entorno.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKER_REQUIREMENTS = ROOT / "requirements.docker.txt"
DOCKERFILE = ROOT / "Dockerfile"


def _requirements() -> list[str]:
    """Lee los paquetes declarados, sin comentarios ni líneas vacías."""

    return [
        line.strip()
        for line in DOCKER_REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _names() -> set[str]:
    """Extrae los nombres de paquete normalizados."""

    return {
        re.split(r"[<>=!\[]", line, maxsplit=1)[0].strip().casefold()
        for line in _requirements()
    }


def test_image_declares_a_postgres_driver() -> None:
    """SQLAlchemy sin driver DBAPI no puede abrir una URL `postgresql://`."""

    names = _names()
    assert "sqlalchemy" in names
    drivers = {"psycopg2-binary", "psycopg2", "psycopg", "pg8000",
               "asyncpg"}
    assert names & drivers, (
        "requirements.docker.txt declara SQLAlchemy pero ningún driver de "
        "PostgreSQL; el historial de Fase 118 degradaría en silencio")


def test_image_declares_every_runtime_import() -> None:
    """Las dependencias que el servicio importa al arrancar están declaradas."""

    names = _names()
    for package in ("fastapi", "uvicorn", "numpy", "pandas", "pydantic",
                    "scipy", "requests", "joblib", "scikit-learn"):
        assert package in names, f"falta {package} en requirements.docker.txt"


def test_dockerfile_copies_the_phase_122_eligibility_artifact() -> None:
    """El menú de Fase 122 queda vacío en silencio si el artefacto no viaja.

    Mismo modo de fallo que el hotfix `a7833a4` del snapshot de Fase 160: la
    vista degrada a lista vacía, así que la ausencia no produce ningún error
    visible en producción.
    """

    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    for name in ("eligibility.json", "hashes.json"):
        assert f"artifacts/phase_122_confidence_reliability/{name}" in dockerfile, (
            f"el Dockerfile no copia {name} de Fase 122")


def test_dockerfile_copies_the_metric_coverage_map() -> None:
    """El guard de cobertura falla abierto: sin el mapa, el bug vuelve mudo.

    Si `coverage_map.json` no viaja en la imagen, `MetricCoverage.is_absent`
    devuelve `False` para todo y los mercados sin datos reales -córners en
    `esp.2`, `eng.3-5`, etc.- se vuelven a publicar con certezas inventadas,
    sin ningún error visible. Es el mismo modo de fallo silencioso que ya
    afectó a `eligibility.json` de Fase 122.
    """

    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert "artifacts/metric_coverage/coverage_map.json" in dockerfile, (
        "el Dockerfile no copia el mapa de cobertura de métricas")


def test_ledger_path_is_writable_by_the_runtime_user() -> None:
    """El ledger no puede apuntar a un punto de montaje ajeno al usuario `app`.

    Montar un volumen Railway en `/data` dejó el punto de montaje propiedad de
    root; el usuario `app` no pudo crear el fichero SQLite y el worker murió
    arrastrando a la API a un crash-loop. La ruta por defecto debe vivir bajo
    `/app`, que la imagen crea y cede explícitamente.
    """

    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert "TELEGRAM_CHANNEL_LEDGER_PATH=/app/data/" in dockerfile
    assert "mkdir -p /app/data" in dockerfile
    assert "chown -R app:app" in dockerfile
