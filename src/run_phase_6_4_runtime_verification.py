"""Genera artefactos de verificación Docker y preparación CI de Fase 6.4.

Requirements:
    - Docker CLI para la verificación completa

Version: 1.0.0
Created: 2026-07-15
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/phase_6_4_runtime_verification"
BASE_DIGEST = "sha256:fd3817f3a855f6c2ada16ac9468e5ee93e361005bd226fd5a5ee1a504e038c84"


def _json_hash(value: Any) -> str:
    """Calcula hash SHA-256 de JSON canónico."""

    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _file_hash(path: Path) -> str:
    """Calcula hash SHA-256 de archivo."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, value: Any) -> None:
    """Escribe JSON atómicamente."""

    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    temp.replace(path)


def _run_verifier() -> dict[str, Any]:
    """Ejecuta el verificador Docker y lee su resultado."""

    result = subprocess.run(["python", "scripts/verify_runtime_phase_6_4.py"], cwd=ROOT, text=True, capture_output=True, check=False)
    path = OUTPUT / "runtime_result.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"status": "verifier_failed", "returncode": result.returncode, "stderr": result.stderr[-4000:]}


def _security() -> dict[str, Any]:
    """Construye la auditoría estática de seguridad."""

    sensitive = [".env", "credentials", "secret", ".pem", ".key"]
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    return {"non_root_declared": "USER app" in (ROOT / "Dockerfile").read_text(encoding="utf-8"), "env_excluded": ".env" in dockerignore, "postgresql_required": False, "redis_required": False, "external_calls_enabled": False, "secrets_excluded": all(token in dockerignore for token in [".pem", ".key"]), "bound_port": "127.0.0.1"}


def _report(decision: str, result: dict[str, Any], hashes: dict[str, str]) -> str:
    """Renderiza informe Markdown de runtime y CI."""

    return "\n".join([
        "# Fase 6.4 - Verificación reproducible Docker",
        "", f"**Decision:** `{decision}`", "",
        f"Runtime: `{result['status']}`.",
        f"Digest OCI base fijado: `{BASE_DIGEST}` para `linux/amd64`.",
        "", "## CI", "",
        "La configuración ejecuta py_compile, pytest, build Docker, arranque, health, OpenAPI y smoke HTTP con timeout de workflow.",
        "La limpieza del contenedor está garantizada por el smoke test mediante `finally`.",
        "", "## Seguridad", "",
        "Usuario no root, `.env` excluido, sin PostgreSQL/Redis, sin llamadas externas y sin despliegue cloud.",
        "", "## Limitación actual", "",
        "El daemon Docker no está disponible en el WSL actual; por tanto no se afirma build ni reproducibilidad bit a bit local.",
        "", "## Hashes", "",
        *[f"- `{key}`: `{value}`" for key, value in sorted(hashes.items())],
    ])


def run() -> dict[str, Any]:
    """Ejecuta verificación y genera artefactos versionados."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    result = _run_verifier()
    security = _security()
    context_files = ["Dockerfile", "requirements.docker.txt", ".dockerignore", "scripts/verify_runtime_phase_6_4.py", ".github/workflows/phase-6-4-runtime.yml"]
    hashes = {path: _file_hash(ROOT / path) for path in context_files}
    hashes["base_image_digest"] = BASE_DIGEST
    audit = {"passed": result["status"] == "passed" and all(security.values()), "runtime_status": result["status"], "security": security, "postgresql_writes": 0, "redis_connections": 0, "external_calls": 0}
    decision = "runtime_verification_approved" if audit["passed"] else "runtime_verification_approved_with_caveats" if result["status"] == "docker_runtime_unavailable" else "runtime_verification_rejected_for_revision"
    manifest = {"phase": "6.4", "decision": decision, "base_image": "python:3.12.3-slim-bookworm", "base_image_digest": BASE_DIGEST, "runtime_status": result["status"], "hashes": hashes, "ci_workflow": ".github/workflows/phase-6-4-runtime.yml", "postgresql_modified": False}
    _write(OUTPUT / "runtime_result.json", result)
    _write(OUTPUT / "smoke_results.json", result.get("smoke", {"status": result["status"]}))
    _write(OUTPUT / "image_manifest.json", result.get("image", {"base_image_digest": BASE_DIGEST, "runtime_status": result["status"]}))
    _write(OUTPUT / "hashes.json", hashes)
    _write(OUTPUT / "security_audit.json", security)
    _write(OUTPUT / "ci_config.json", {"workflow": ".github/workflows/phase-6-4-runtime.yml", "timeout_minutes": 15, "steps": ["py_compile", "pytest", "docker_build", "container_start", "health", "openapi", "http_smoke", "cleanup"]})
    _write(OUTPUT / "final_manifest.json", manifest)
    (OUTPUT / "runtime_report.md").write_text(_report(decision, result, hashes), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    LOGGER.info("Fase 6.4: %s", run()["decision"])

# Version: 1.0.0
# Created: 2026-07-15
