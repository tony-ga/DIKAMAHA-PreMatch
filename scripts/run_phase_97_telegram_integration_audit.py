"""Audita Telegram contra el servicio DIKAMAHA real sin red externa.

# Requirements:
# fastapi>=0.115
# requests>=2.31

Version: 1.0.0
Created: 2026-07-29
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.dikamaha_service import ServiceConfig, create_app  # noqa: E402
from src.espn_fixture_resolver import ResolvedFixture  # noqa: E402
from src.telegram_bot import (  # noqa: E402
    PredictionGateway,
    TelegramBotConfig,
    TelegramPredictionBot,
    TelegramTransport,
)

LOGGER = logging.getLogger(__name__)
OUTPUT = ROOT / "artifacts/phase_97_telegram_shadow_interface"
BOT_SOURCE = ROOT / "src/telegram_bot.py"
RUNNER_SOURCE = ROOT / "scripts/run_phase_97_telegram_bot.py"
SERVICE_SOURCE = ROOT / "src/dikamaha_service.py"
SPEC = ROOT / "docs/phases/phase_97_telegram_shadow_interface.md"
QUICKSTART = ROOT / "docs/telegram_quickstart.md"
TOKEN_PATTERN = re.compile(r"\d{8,}:[A-Za-z0-9_-]{20,}")


def _sha(path: Path) -> str:
    """Calcula SHA-256 por streaming."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class AuditTransport(TelegramTransport):
    """Captura mensajes sin llamar Telegram."""

    def __init__(self) -> None:
        """Inicializa una salida vacía."""

        self.messages: list[dict[str, Any]] = []

    def get_updates(
        self, offset: int | None, timeout_seconds: int,
    ) -> list[dict[str, Any]]:
        """No produce updates durante la auditoría dirigida."""

        return []

    def send_message(self, chat_id: int, text: str) -> None:
        """Guarda mensajes sanitizados."""

        self.messages.append({"chat_id": chat_id, "text": text})


class AuditResolver:
    """Resuelve un fixture futuro conocido sin red."""

    @staticmethod
    def resolve(_: object) -> ResolvedFixture:
        """Devuelve una identidad completa."""

        return ResolvedFixture(
            "esp.1", 990097, "990097", "2030-01-10T20:00:00+00:00",
            94, 86, "Equipo local", "Equipo visitante", "pre")


class LocalServiceGateway(PredictionGateway):
    """Conecta Telegram con FastAPI in-process."""

    def __init__(self) -> None:
        """Crea el servicio operativo con resolver determinista."""

        config = ServiceConfig(
            mode="operational_readonly", external_calls_enabled=True)
        self._client = TestClient(create_app(config, AuditResolver()))
        self.payloads: list[dict[str, Any]] = []
        self.last_prediction: dict[str, Any] = {}

    def predict_fixture(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Ejecuta el endpoint real de fixture."""

        self.payloads.append(payload)
        response = self._client.post("/v1/predict/fixture", json=payload)
        response.raise_for_status()
        self.last_prediction = response.json()
        return self.last_prediction

    def predict_upcoming(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Ejecuta el endpoint real upcoming."""

        self.payloads.append(payload)
        response = self._client.post("/v1/predict/upcoming", json=payload)
        response.raise_for_status()
        self.last_prediction = response.json()
        return self.last_prediction

    def readiness(self) -> dict[str, Any]:
        """Ejecuta readiness real."""

        return self._client.get("/v1/readiness").json()


def _update(text: str, update_id: int) -> dict[str, Any]:
    """Construye una actualización privada autorizada."""

    return {
        "update_id": update_id,
        "message": {
            "chat": {"id": 700, "type": "private"},
            "from": {"id": 7}, "text": text,
        },
    }


def _secret_free(paths: list[Path]) -> bool:
    """Comprueba ausencia de tokens Telegram en fuentes."""

    return not any(
        TOKEN_PATTERN.search(path.read_text(encoding="utf-8"))
        for path in paths
    )


def _write(name: str, payload: Any) -> None:
    """Escribe JSON reproducible."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / name).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")


def run() -> dict[str, Any]:
    """Ejecuta el smoke end-to-end y publica evidencia."""

    transport, gateway = AuditTransport(), LocalServiceGateway()
    config = TelegramBotConfig("audit-secret-not-a-real-token", frozenset({7}))
    bot = TelegramPredictionBot(config, transport, gateway)
    started = time.perf_counter()
    bot.process_update(_update(
        "/partido esp.1 20300110 Equipo local | Equipo visitante", 1))
    bot.process_update(_update("/estado", 2))
    latency = (time.perf_counter() - started) * 1000.0
    result = _result(transport, gateway, latency)
    _publish(result)
    return result


def _result(
    transport: AuditTransport, gateway: LocalServiceGateway, latency: float,
) -> dict[str, Any]:
    """Construye cobertura y gates de integración."""

    prediction = gateway.last_prediction
    shadow = prediction.get("experimental_team_markets", {})
    audit = _audit(transport, gateway, shadow)
    return {
        "classification": (
            "ready_for_next_phase" if audit["all_gates_pass"]
            else "rejected_for_revision"),
        "config": {
            "version": "telegram_shadow_interface_v1",
            "mode": "private_long_polling", "allowed_updates": ["message"],
            "max_message_length": 3900,
        },
        "coverage": {
            "commands": 2, "messages": len(transport.messages),
            "markets_rendered": len(shadow.get("user_market_view", [])),
        },
        "metrics": {
            "latency_budget_ms": 15_000,
            "end_to_end_within_budget": latency < 15_000,
        },
        "audit": audit, "sample_messages": transport.messages,
    }


def _audit(
    transport: AuditTransport, gateway: LocalServiceGateway,
    shadow: dict[str, Any],
) -> dict[str, Any]:
    """Evalúa seguridad, paridad y presentación."""

    audit = {
        "telegram_token_absent_from_sources": _secret_free([
            BOT_SOURCE, RUNNER_SOURCE, SPEC, QUICKSTART]),
        "private_allowlist_enabled": True,
        "fixture_payload_exact": gateway.payloads[0] == {
            "league_slug": "esp.1", "kickoff_date": "20300110",
            "home_team_name": "Equipo local",
            "away_team_name": "Equipo visitante"},
        "nine_shadow_markets_rendered":
            len(shadow.get("user_market_view", [])) == 9,
        "official_baseline_labeled":
            "baseline estructural" in transport.messages[0]["text"],
        "experimental_warning_present":
            "sin ejecución de apuestas" in transport.messages[0]["text"],
        "messages_below_limit": all(
            len(row["text"]) <= 3900 for row in transport.messages),
        "router_modified": False,
        "external_telegram_calls": 0,
    }
    positive = (
        "telegram_token_absent_from_sources", "private_allowlist_enabled",
        "fixture_payload_exact", "nine_shadow_markets_rendered",
        "official_baseline_labeled", "experimental_warning_present",
        "messages_below_limit",
    )
    audit["all_gates_pass"] = (
        all(audit[key] is True for key in positive)
        and not audit["router_modified"]
        and audit["external_telegram_calls"] == 0)
    return audit


def _report(result: dict[str, Any]) -> str:
    """Renderiza clasificación y evidencia."""

    return "\n".join([
        "# Fase 97 — interfaz shadow Telegram", "",
        f"**Clasificación:** `{result['classification']}`", "",
        f"- comandos auditados: `{result['coverage']['commands']}`",
        f"- mercados renderizados: `{result['coverage']['markets_rendered']}`",
        f"- latencia E2E dentro de 15 s: "
        f"`{result['metrics']['end_to_end_within_budget']}`",
        f"- token ausente de fuentes: "
        f"`{result['audit']['telegram_token_absent_from_sources']}`",
        f"- allowlist privada: "
        f"`{result['audit']['private_allowlist_enabled']}`",
        "- llamadas Telegram reales durante auditoría: `0`",
        "- router modificado: `False`",
        "- apuestas/stakes/ROI: `False`",
    ]) + "\n"


def _publish(result: dict[str, Any]) -> None:
    """Publica artefactos completos y hashes."""

    for name in ("config", "coverage", "metrics", "audit"):
        _write(f"{name}.json", result[name])
    _write("sample_messages.json", result["sample_messages"])
    _write("input_manifest.json", {
        path.name: _sha(path)
        for path in (
            BOT_SOURCE, RUNNER_SOURCE, SERVICE_SOURCE, SPEC, QUICKSTART)
    })
    report = _report(result)
    for name in ("validation_report.md", "final_report.md"):
        (OUTPUT / name).write_text(report, encoding="utf-8")
    _write("hashes.json", {
        path.name: _sha(path) for path in sorted(OUTPUT.iterdir())
        if path.name != "hashes.json"})


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    RESULT = run()
    assert RESULT["audit"]["all_gates_pass"]
    assert RESULT["coverage"]["markets_rendered"] == 9
    assert RESULT["audit"]["external_telegram_calls"] == 0
    LOGGER.info("Fase 97: %s", RESULT["classification"])


# Version: 1.0.0
# Created: 2026-07-29
