"""Un ledger inaccesible degrada la difusión, no tumba la inferencia.

Existe por una incidencia real de producción. El publicador corre como proceso
hijo del supervisor que también levanta la API, de modo que su muerte termina el
contenedor completo. Al montar un volumen Railway en `/data`, el punto de
montaje quedó propiedad de root mientras el contenedor corre como el usuario
`app`, así que `create_all` fallaba con

    sqlite3.OperationalError: unable to open database file

y como esa construcción ocurría *antes* del `try` del bucle, el worker moría al
instante y arrastraba a la API a un crash-loop. El bot premium cayó después
porque su readiness de arranque contra la API dejó de responder.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.exc import OperationalError

from scripts import run_phase_101_telegram_channel_publisher as worker


def _args(**changes: Any) -> argparse.Namespace:
    """Construye argumentos equivalentes a una corrida única."""

    payload = {
        "dry_run": False, "mode": "lite", "ledger_path": None, "once": True,
    }
    payload.update(changes)
    return argparse.Namespace(**payload)


def test_unwritable_ledger_does_not_kill_the_worker(
    monkeypatch: pytest.MonkeyPatch, caplog: Any,
) -> None:
    """Una ruta de ledger inaccesible se registra y no propaga excepción."""

    def _explode(*_: Any, **__: Any) -> Any:
        """Simula `create_all` sobre un directorio no escribible."""

        raise OperationalError(
            "create table", {}, Exception("unable to open database file"))

    monkeypatch.setattr(worker, "_publisher", _explode)

    with caplog.at_level(logging.ERROR):
        code = worker._run(_args())

    assert code == 1
    assert any(
        "channel_publisher_unavailable" in record.getMessage()
        for record in caplog.records)


def test_worker_recovers_once_the_ledger_becomes_writable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La construcción se reintenta en el ciclo siguiente, no se abandona."""

    attempts = {"count": 0}
    cycles = {"count": 0}

    class _Publisher:
        """Publicador mínimo que sólo cuenta ciclos."""

        def run_cycle(self, _: Any) -> dict[str, int]:
            """Registra un ciclo ejecutado."""

            cycles["count"] += 1
            return {"summaries": 0}

    def _flaky(*_: Any, **__: Any) -> Any:
        """Falla la primera vez y construye la segunda."""

        attempts["count"] += 1
        if attempts["count"] == 1:
            raise OSError("unable to open database file")
        return _Publisher()

    sleeps = {"count": 0}

    def _sleep(_: float) -> None:
        """Corta el bucle tras dos vueltas para que la prueba termine."""

        sleeps["count"] += 1
        if sleeps["count"] >= 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(worker, "_publisher", _flaky)
    monkeypatch.setattr(worker.time, "sleep", _sleep)
    monkeypatch.setenv("TELEGRAM_CHANNEL_POLL_SECONDS", "60")

    with pytest.raises(KeyboardInterrupt):
        worker._run(_args(once=False))

    assert attempts["count"] == 2, "debe reintentar construir el publicador"
    assert cycles["count"] == 1, "debe ejecutar el ciclo tras recuperarse"


def test_cycle_failure_keeps_the_publisher_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un fallo de ciclo no reconstruye el publicador ni mata el worker."""

    builds = {"count": 0}

    class _Publisher:
        """Publicador que siempre falla su ciclo."""

        def run_cycle(self, _: Any) -> dict[str, int]:
            """Simula un fallo transitorio del ciclo."""

            raise ValueError("channel_cycle_boom")

    def _build(*_: Any, **__: Any) -> Any:
        """Cuenta cuántas veces se construye el publicador."""

        builds["count"] += 1
        return _Publisher()

    sleeps = {"count": 0}

    def _sleep(_: float) -> None:
        """Corta el bucle tras dos vueltas."""

        sleeps["count"] += 1
        if sleeps["count"] >= 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(worker, "_publisher", _build)
    monkeypatch.setattr(worker.time, "sleep", _sleep)

    with pytest.raises(KeyboardInterrupt):
        worker._run(_args(once=False))

    assert builds["count"] == 1, "no debe reconstruirse tras un fallo de ciclo"


def test_dockerfile_ledger_directory_is_owned_by_the_runtime_user() -> None:
    """El directorio del ledger debe pertenecer al usuario que ejecuta.

    Si una fase futura vuelve a montar un volumen sobre esa ruta, el montaje
    llega propiedad de root y el usuario `app` no puede escribir. La imagen
    debe crear y ceder el directorio explícitamente.
    """

    dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text(
        encoding="utf-8")
    assert "chown -R app:app" in dockerfile
    assert "TELEGRAM_CHANNEL_LEDGER_PATH=" in dockerfile
