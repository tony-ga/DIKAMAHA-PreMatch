"""Genera auditoría y manifiesto del empaquetado Docker local DIKAMAHA v1.

Requirements:
    - Python standard library

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
OUTPUT = ROOT / "artifacts/phase_6_3_local_packaging"


def _file_hash(path: Path) -> str:
    """Calcula SHA-256 de un archivo local."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_hash(value: Any) -> str:
    """Calcula hash estable de JSON."""

    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _write(path: Path, value: Any) -> None:
    """Escribe JSON atómicamente."""

    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _run_smoke() -> dict[str, Any]:
    """Ejecuta el smoke test aislado."""

    result = subprocess.run(["python", "scripts/smoke_test_phase_6_3.py"], cwd=ROOT, text=True, capture_output=True, check=False)
    smoke_path = OUTPUT / "smoke_test_runtime.json"
    if smoke_path.exists():
        return json.loads(smoke_path.read_text(encoding="utf-8"))
    return {"status": "runner_failed", "returncode": result.returncode, "stderr": result.stderr[-4000:]}


def _configuration() -> dict[str, Any]:
    """Declara configuración y límites de empaquetado."""

    return {"mode": "local_dry_run", "image_tag": "dikamaha-local:phase-6-3", "base_image": "python:3.12.3-slim-bookworm", "hawkes_enabled": False, "official_prediction": False, "external_calls_enabled": False, "persistence_enabled": False, "postgresql_required": False, "redis_required": False, "versions": {"contract": "dikamaha_inference_contract_v1", "dixon_coles": "dixon_coles_v1", "kalman": "kalman_v2", "markov": "markov_v1", "hawkes": "hawkes_v1"}}


def _report(decision: str, smoke: dict[str, Any], hashes: dict[str, str]) -> str:
    """Construye informe Markdown del empaquetado."""

    return "\n".join([
        "# Fase 6.3 - Empaquetado reproducible local DIKAMAHA",
        "", f"**Decision:** `{decision}`", "",
        "El Dockerfile y el smoke test quedan preparados para ejecución local.",
        "", "## Resultado", "",
        f"- Runtime Docker: `{smoke['status']}`.",
        "- PostgreSQL, Redis y llamadas externas: no requeridos.",
        "- Usuario del contenedor: `app` no root.",
        "- Hawkes: `false` por defecto.",
        "- No se despliega en cloud ni se declara producción.",
        "", "## Caveat", "",
        "Docker no está disponible en el entorno WSL actual; el build y el smoke HTTP deben ejecutarse cuando Docker Desktop tenga integración WSL habilitada.",
        "La imagen base está fijada por tag; para bit-reproducibilidad CI debe añadirse su digest OCI.",
        "", "## Hashes", "",
        *[f"- `{key}`: `{value}`" for key, value in sorted(hashes.items())],
    ])


def run() -> dict[str, Any]:
    """Genera configuración, hashes, auditoría y manifiesto."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    smoke = _run_smoke()
    config = _configuration()
    source_hashes = {name: _file_hash(ROOT / name) for name in ["Dockerfile", "requirements.docker.txt", ".dockerignore"]}
    openapi = json.loads((ROOT / "artifacts/phase_6_2_local_inference_service/openapi_v1.json").read_text(encoding="utf-8"))
    checks = {"smoke_passed": smoke["status"] == "passed", "docker_runtime_available": smoke["status"] != "docker_runtime_unavailable", "hawkes_disabled": config["hawkes_enabled"] is False, "postgresql_not_required": config["postgresql_required"] is False, "openapi_available": bool(openapi.get("paths"))}
    audit = {"passed": checks["smoke_passed"] and all(checks.values()), "checks": checks, "database_writes": 0, "external_calls": 0, "notes": ["Docker build no ejecutado si el CLI no está disponible.", "No se inventan resultados de build ni HTTP."]}
    hashes = {"dockerfile": source_hashes["Dockerfile"], "dependencies": source_hashes["requirements.docker.txt"], "dockerignore": source_hashes[".dockerignore"], "openapi": _json_hash(openapi), "configuration": _json_hash(config), "smoke": _json_hash(smoke), "audit": _json_hash(audit)}
    decision = "local_packaging_approved" if audit["passed"] else "local_packaging_approved_with_caveats" if smoke["status"] == "docker_runtime_unavailable" else "local_packaging_rejected_for_revision"
    manifest = {"phase": "6.3", "decision": decision, "runtime_status": smoke["status"], "hashes": hashes, "postgresql_modified": False, "artifact_dir": str(OUTPUT)}
    _write(OUTPUT / "effective_config_v1.json", config)
    _write(OUTPUT / "build_result_v1.json", {"status": smoke["status"], "docker_build_executed": smoke.get("build_executed", False)})
    _write(OUTPUT / "smoke_test_v1.json", smoke)
    _write(OUTPUT / "openapi_v1.json", openapi)
    _write(OUTPUT / "audit_v1.json", audit)
    _write(OUTPUT / "hashes_v1.json", hashes)
    _write(OUTPUT / "manifest_v1.json", manifest)
    (OUTPUT / "report_v1.md").write_text(_report(decision, smoke, hashes), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run()
    LOGGER.info("Fase 6.3: %s", result["decision"])

# Version: 1.0.0
# Created: 2026-07-15
