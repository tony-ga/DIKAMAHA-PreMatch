"""Ciclo de vida del servicio permanente del canal Telegram.

# Requirements:
#   requests>=2.31

Version: 1.0.0
Created: 2026-07-29
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from types import TracebackType
from typing import Any, Callable

import requests

LOGGER = logging.getLogger(__name__)
ProcessFactory = Callable[..., subprocess.Popen[str]]


class ManagedDikamahaApi:
    """Supervisa una API local y conserva instancias externas saludables."""

    def __init__(
        self, root: Path, base_url: str,
        session: requests.Session | None = None,
        process_factory: ProcessFactory = subprocess.Popen,
    ) -> None:
        """Inyecta infraestructura para operación y pruebas aisladas."""

        self._root = root
        self._base_url = base_url.rstrip("/")
        self._session = session or requests.Session()
        self._process_factory = process_factory
        self._process: subprocess.Popen[str] | None = None

    def __enter__(self) -> ManagedDikamahaApi:
        """Reutiliza la API o inicia una instancia administrada."""

        if self._ready():
            LOGGER.info("dikamaha_api_reused url=%s", self._base_url)
            return self
        self._process = self._process_factory(
            _api_command(), cwd=self._root, env=_api_environment(),
            text=True)
        self._wait_until_ready()
        LOGGER.info("dikamaha_api_started pid=%s", self._process.pid)
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Detiene sólo la API creada por este servicio."""

        self.stop()

    def stop(self) -> None:
        """Finaliza limpiamente el proceso administrado."""

        process = self._process
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        LOGGER.info("dikamaha_api_stopped")

    def _ready(self) -> bool:
        """Consulta readiness con timeout corto y error explícito."""

        try:
            response = self._session.get(
                f"{self._base_url}/v1/readiness", timeout=(2, 5))
            payload = response.json()
        except (requests.RequestException, ValueError):
            return False
        return response.status_code == 200 and bool(payload.get("ready"))

    def _wait_until_ready(self) -> None:
        """Espera hasta treinta segundos a que la API acepte tráfico."""

        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self._process is not None and self._process.poll() is not None:
                raise RuntimeError("dikamaha_api_process_exited")
            if self._ready():
                return
            time.sleep(0.5)
        self.stop()
        raise TimeoutError("dikamaha_api_start_timeout")


class TelegramChannelService:
    """Supervisa el worker continuo de difusión Telegram."""

    def __init__(
        self, root: Path,
        process_factory: ProcessFactory = subprocess.Popen,
    ) -> None:
        """Configura el proceso hijo del publicador."""

        self._root = root
        self._process_factory = process_factory
        self._process: subprocess.Popen[str] | None = None

    def run(self) -> int:
        """Ejecuta el worker hasta señal o terminación inesperada."""

        self._process = self._process_factory(
            _publisher_command(self._root), cwd=self._root,
            env=os.environ.copy(), text=True)
        LOGGER.info("telegram_channel_worker_started pid=%s", self._process.pid)
        try:
            return int(self._process.wait())
        except KeyboardInterrupt:
            LOGGER.info("telegram_channel_service_interrupt")
            return 0
        finally:
            self.stop()

    def stop(self) -> None:
        """Detiene el worker si continúa activo."""

        process = self._process
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        LOGGER.info("telegram_channel_worker_stopped")


def _api_command() -> list[str]:
    """Construye el comando estable de Uvicorn."""

    host = os.getenv("DIKAMAHA_BIND_HOST", "127.0.0.1")
    port = os.getenv("PORT", os.getenv("DIKAMAHA_PORT", "8000"))
    return [
        sys.executable, "-m", "uvicorn", "src.dikamaha_service:app",
        "--host", host, "--port", port, "--no-server-header",
        "--timeout-keep-alive", "5", "--no-access-log",
    ]


def _publisher_command(root: Path) -> list[str]:
    """Construye el comando del worker continuo sin `--once`."""

    script = root / "scripts" / "run_phase_101_telegram_channel_publisher.py"
    return [sys.executable, str(script)]


def _api_environment() -> dict[str, str]:
    """Fuerza el perfil causal read-only para llamadas ESPN."""

    environment = os.environ.copy()
    environment.update({
        "DIKAMAHA_MODE": "operational_readonly",
        "DIKAMAHA_EXTERNAL_CALLS_ENABLED": "true",
        "DIKAMAHA_INFERENCE_TIMEOUT_SECONDS": os.getenv(
            "DIKAMAHA_INFERENCE_TIMEOUT_SECONDS", "30"),
    })
    port = environment.get("PORT", environment.get("DIKAMAHA_PORT", "8000"))
    environment.setdefault("DIKAMAHA_BOT_API_URL", f"http://127.0.0.1:{port}")
    return environment


# Version: 1.0.0
# Created: 2026-07-29
