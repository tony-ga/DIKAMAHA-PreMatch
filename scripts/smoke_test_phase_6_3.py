"""Smoke test Docker del empaquetado local DIKAMAHA v1.

No escribe en PostgreSQL ni realiza llamadas de aplicación externas.

Requirements:
    - Docker CLI (opcional para ejecutar el smoke test)

Version: 1.0.0
Created: 2026-07-15
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/phase_6_3_local_packaging"
IMAGE = os.getenv("DIKAMAHA_IMAGE", "dikamaha-local:phase-6-3")
BASE_URL = os.getenv("DIKAMAHA_BASE_URL", "http://127.0.0.1:18000")


def _run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    """Ejecuta un comando sin shell y captura salida."""

    return subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False, **kwargs)


def _request(path: str, payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    """Ejecuta una solicitud HTTP JSON contra el contenedor."""

    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(BASE_URL + path, data=body, method="POST" if body else "GET", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _pre_payload() -> dict[str, Any]:
    """Devuelve request pre-match válido."""

    return {"match_id": 900001, "home_team_id": 1, "away_team_id": 2, "kickoff_ts": "2025-01-10T20:00:00+00:00", "feature_cutoff_ts": "2025-01-10T19:59:59+00:00", "competition_id": "esp.1", "feature_version": "match_features_v1", "eligible_for_materialization": True, "history_minimum_met": True, "league_intercept": 0.2, "home_advantage": 0.15, "dc_attack_home": 0.2, "dc_defense_home": -0.1, "dc_attack_away": -0.2, "dc_defense_away": 0.1, "kalman_attack_home": 0.25, "kalman_defense_home": -0.08, "kalman_attack_away": -0.25, "kalman_defense_away": 0.08}


def _live_payload() -> dict[str, Any]:
    """Devuelve request live válido."""

    return {"match_id": 900001, "home_team_id": 1, "away_team_id": 2, "kickoff_ts": "2025-01-10T20:00:00+00:00", "snapshot_ts": "2025-01-10T20:10:00+00:00", "lambda_base_home": 1.5, "lambda_base_away": 1.1, "events": [{"event_id": "smoke-e1", "event_ts": "2025-01-10T20:08:00+00:00", "event_type": "shot_on_target", "team_id": 1}]}


def _checks() -> dict[str, bool]:
    """Comprueba endpoints y rechazos de contrato."""

    health_status, health = _request("/v1/health")
    openapi_status, openapi = _request("/openapi.json")
    pre_status, pre = _request("/v1/predict/pre-match", _pre_payload())
    live_status, live = _request("/v1/predict/live", _live_payload())
    blocked = _pre_payload()
    blocked["match_id"] = 704766
    blocked_status, _ = _request("/v1/predict/pre-match", blocked)
    leaked = _pre_payload()
    leaked["feature_cutoff_ts"] = "2025-01-10T20:00:01+00:00"
    leaked_status, _ = _request("/v1/predict/pre-match", leaked)
    official = _live_payload()
    official.update({"official_prediction": True, "hawkes_enabled": True})
    official_status, _ = _request("/v1/predict/live", official)
    return {"health": health_status == 200 and health["hawkes_enabled"] is False, "openapi": openapi_status == 200 and "/v1/predict/live" in openapi["paths"], "pre_match": pre_status == 200 and pre["audit"]["passed"], "live": live_status == 200 and live["hawkes_applied"] is False and live["audit"]["passed"], "blocked_match_rejected": blocked_status == 422, "leakage_rejected": leaked_status == 422, "official_hawkes_rejected": official_status == 422}


def run() -> dict[str, Any]:
    """Construye, ejecuta y audita el contenedor local."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    if shutil.which("docker") is None:
        return {"status": "docker_runtime_unavailable", "build_executed": False, "smoke_executed": False, "reason": "Docker CLI no está disponible en el entorno actual."}
    runtime = _run(["docker", "version", "--format", "{{.Server.Version}}"])
    if runtime.returncode != 0:
        return {"status": "docker_runtime_unavailable", "build_executed": False, "smoke_executed": False, "reason": "Docker CLI existe, pero el daemon no está disponible.", "runtime_stderr": runtime.stderr[-4000:]}
    build = _run(["docker", "build", "--file", "Dockerfile", "--tag", IMAGE, "."])
    if build.returncode != 0:
        return {"status": "build_failed", "build_executed": True, "smoke_executed": False, "build_stderr": build.stderr[-4000:]}
    container = _run(["docker", "run", "--detach", "--rm", "--publish", "127.0.0.1:18000:8000", IMAGE])
    if container.returncode != 0:
        return {"status": "container_start_failed", "build_executed": True, "smoke_executed": False, "container_stderr": container.stderr[-4000:]}
    try:
        time.sleep(2)
        checks = _checks()
    finally:
        _run(["docker", "stop", container.stdout.strip()])
    return {"status": "passed" if all(checks.values()) else "smoke_failed", "build_executed": True, "smoke_executed": True, "checks": checks}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "smoke_test_runtime.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    LOGGER.info("Smoke test: %s", result["status"])

# Version: 1.0.0
# Created: 2026-07-15
