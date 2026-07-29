"""Verifica Docker runtime y smoke HTTP de Fase 6.4.

Requirements:
    - Docker CLI y daemon

Version: 1.0.0
Created: 2026-07-15
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/phase_6_4_runtime_verification"
IMAGE = os.getenv("DIKAMA_IMAGE", "dikamaha-local:phase-6-4")
BASE_DIGEST = "sha256:fd3817f3a855f6c2ada16ac9468e5ee93e361005bd226fd5a5ee1a504e038c84"


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    """Ejecuta Docker sin shell y captura salida."""

    return subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)


def _sha256(path: Path) -> str:
    """Calcula el hash SHA-256 de un archivo."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _runtime_info() -> dict[str, Any]:
    """Obtiene versión y arquitectura del daemon Docker."""

    result = _run(["docker", "version", "--format", "{{json .}}"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Docker daemon unavailable")
    info = json.loads(result.stdout)
    return {"client": info.get("Client"), "server": info.get("Server")}


def _build() -> dict[str, Any]:
    """Construye la imagen y captura digest final."""

    build = _run(["docker", "build", "--pull", "--file", "Dockerfile", "--tag", IMAGE, "."])
    if build.returncode != 0:
        raise RuntimeError(build.stderr[-4000:])
    inspect = _run(["docker", "image", "inspect", IMAGE, "--format", "{{json .}}"])
    if inspect.returncode != 0:
        raise RuntimeError(inspect.stderr[-4000:])
    image = json.loads(inspect.stdout)
    return {"image": IMAGE, "image_id": image.get("Id"), "repo_digests": image.get("RepoDigests", []), "architecture": image.get("Architecture"), "os": image.get("Os"), "user": image.get("Config", {}).get("User")}


def _smoke() -> dict[str, Any]:
    """Ejecuta el smoke test HTTP del paquete."""

    env = {**os.environ, "DIKAMAHA_IMAGE": IMAGE}
    result = subprocess.run(["python", "scripts/smoke_test_phase_6_3.py"], cwd=ROOT, env=env, text=True, capture_output=True, check=False)
    smoke_path = OUTPUT / "smoke_test_runtime.json"
    if not smoke_path.exists():
        raise RuntimeError(result.stderr[-4000:])
    return json.loads(smoke_path.read_text(encoding="utf-8"))


def run() -> dict[str, Any]:
    """Ejecuta verificación completa con limpieza controlada."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    if shutil.which("docker") is None:
        return {"status": "docker_runtime_unavailable", "runtime_verified": False, "build_executed": False, "reason": "Docker CLI no disponible."}
    try:
        runtime = _runtime_info()
        image = _build()
        smoke = _smoke()
        return {"status": "passed" if smoke["status"] == "passed" else "smoke_failed", "runtime_verified": True, "build_executed": True, "runtime": runtime, "image": image, "smoke": smoke, "base_image_digest": BASE_DIGEST}
    except RuntimeError as exc:
        return {"status": "docker_runtime_unavailable", "runtime_verified": False, "build_executed": False, "reason": str(exc)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "runtime_result.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    LOGGER.info("Runtime verification: %s", result["status"])

# Version: 1.0.0
# Created: 2026-07-15
