"""Pruebas del servicio autocontenido del canal Telegram."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.telegram_channel_service import (
    ManagedDikamahaApi,
    TelegramChannelService,
    _api_environment,
)


class _Response:
    """Respuesta HTTP mínima y saludable."""

    status_code = 200

    def json(self) -> dict[str, bool]:
        """Devuelve readiness positivo."""

        return {"ready": True}


class _Session:
    """Sesión que evita red real."""

    def get(self, url: str, timeout: tuple[int, int]) -> _Response:
        """Confirma cualquier consulta de readiness."""

        assert url.endswith("/v1/readiness")
        assert timeout == (2, 5)
        return _Response()


class _Process:
    """Proceso determinista para verificar cierre limpio."""

    pid = 123

    def __init__(self, exit_code: int | None = None) -> None:
        """Configura el estado inicial."""

        self.exit_code = exit_code
        self.terminated = False

    def poll(self) -> int | None:
        """Devuelve el estado actual."""

        return self.exit_code

    def wait(self, timeout: int | None = None) -> int:
        """Completa el proceso al finalizar."""

        self.exit_code = 0
        return 0

    def terminate(self) -> None:
        """Marca terminación solicitada."""

        self.terminated = True

    def kill(self) -> None:
        """Marca terminación forzada."""

        self.terminated = True


def test_healthy_external_api_is_reused() -> None:
    """No inicia un proceso cuando DIKAMAHA ya está saludable."""

    started: list[dict[str, Any]] = []

    def factory(*args: Any, **kwargs: Any) -> _Process:
        """Registra cualquier arranque inesperado."""

        started.append({"args": args, "kwargs": kwargs})
        return _Process()

    with ManagedDikamahaApi(
        Path("."), "http://127.0.0.1:8000", _Session(), factory):
        assert started == []


def test_worker_is_stopped_after_normal_exit() -> None:
    """El supervisor conserva un cierre determinista."""

    process = _Process()

    def factory(*args: Any, **kwargs: Any) -> _Process:
        """Devuelve el proceso controlado."""

        return process

    assert TelegramChannelService(
        Path("."), process_factory=factory).run() == 0
    assert process.exit_code == 0


def test_worker_receives_the_managed_api_url() -> None:
    """Railway propaga PORT al publicador en vez del fallback fijo 8000."""

    captured: dict[str, Any] = {}

    def factory(*args: Any, **kwargs: Any) -> _Process:
        captured.update(kwargs)
        return _Process()

    assert TelegramChannelService(
        Path("."), "http://127.0.0.1:8080", factory).run() == 0
    assert captured["env"]["DIKAMAHA_BOT_API_URL"] == (
        "http://127.0.0.1:8080")


def test_api_environment_is_operational_readonly() -> None:
    """Congela el perfil seguro requerido para ESPN."""

    environment = _api_environment()
    assert environment["DIKAMAHA_MODE"] == "operational_readonly"
    assert environment["DIKAMAHA_EXTERNAL_CALLS_ENABLED"] == "true"
    assert int(environment["DIKAMAHA_INFERENCE_TIMEOUT_SECONDS"]) >= 1


# Version: 1.0.0
# Created: 2026-07-29
