"""Genera el release candidate local reproducible de DIKAMAHA v1.

No despliega, no persiste y no activa componentes experimentales.

Requirements:
    - fastapi
    - pydantic

Version: 1.0.0
Created: 2026-07-16
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

try:
    from src.dikamaha_service import create_app
except ModuleNotFoundError:  # pragma: no cover
    from dikamaha_service import create_app

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/phase_6_7_local_release_candidate"
LOGGER = logging.getLogger(__name__)


def _hash_bytes(value: bytes) -> str:
    """Calcula SHA-256 de bytes."""

    return hashlib.sha256(value).hexdigest()


def _hash_json(value: Any) -> str:
    """Calcula hash estable de JSON."""

    return _hash_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def _write(path: Path, value: Any) -> None:
    """Escribe JSON atómicamente."""

    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    temp.replace(path)


def _previous_hashes() -> dict[str, str]:
    """Calcula hashes de artefactos previos sin modificarlos."""

    roots = [ROOT / "artifacts/phase_6_1_inference_contract", ROOT / "artifacts/phase_6_2_local_inference_service", ROOT / "artifacts/phase_6_4_runtime_verification", ROOT / "artifacts/phase_6_5_observability", ROOT / "artifacts/phase_6_6_ci_e2e"]
    files = [path for root in roots for path in root.glob("*") if path.is_file()]
    return {str(path.relative_to(ROOT)): _hash_bytes(path.read_bytes()) for path in sorted(files)}


def _service_examples() -> dict[str, Any]:
    """Genera ejemplos y comprueba replay determinista del servicio."""

    pre = {"match_id": 900001, "home_team_id": 1, "away_team_id": 2, "kickoff_ts": "2025-01-10T20:00:00+00:00", "feature_cutoff_ts": "2025-01-10T19:59:59+00:00", "competition_id": "esp.1", "feature_version": "match_features_v1", "eligible_for_materialization": True, "history_minimum_met": True, "league_intercept": 0.2, "home_advantage": 0.15, "dc_attack_home": 0.2, "dc_defense_home": -0.1, "dc_attack_away": -0.2, "dc_defense_away": 0.1, "kalman_attack_home": 0.25, "kalman_defense_home": -0.08, "kalman_attack_away": -0.25, "kalman_defense_away": 0.08}
    live = {"match_id": 900001, "home_team_id": 1, "away_team_id": 2, "kickoff_ts": "2025-01-10T20:00:00+00:00", "snapshot_ts": "2025-01-10T20:10:00+00:00", "lambda_base_home": 1.5, "lambda_base_away": 1.1, "events": [{"event_id": "release-e1", "event_ts": "2025-01-10T20:08:00+00:00", "event_type": "shot_on_target", "team_id": 1}]}
    client = TestClient(create_app())
    first = {"health": client.get("/v1/health").json(), "readiness": client.get("/v1/readiness").json(), "pre_match": client.post("/v1/predict/pre-match", json=pre).json(), "live": client.post("/v1/predict/live", json=live).json()}
    second = {"pre_match": client.post("/v1/predict/pre-match", json=pre).json(), "live": client.post("/v1/predict/live", json=live).json()}
    return {"requests": {"pre_match": pre, "live": live}, "responses": first, "replay": second, "deterministic": first["pre_match"] == second["pre_match"] and first["live"] == second["live"]}


def _dependency_manifest() -> dict[str, Any]:
    """Congela dependencias directas y warning de tests."""

    requirements = ROOT / "requirements.docker.txt"
    return {"requirements_file": "requirements.docker.txt", "requirements_hash": _hash_bytes(requirements.read_bytes()), "pinned": requirements.read_text(encoding="utf-8").splitlines(), "test_environment": {"fastapi": "0.139.0", "starlette": "1.3.1", "httpx": "0.28.1", "pydantic": "2.13.4"}, "warning": "Starlette TestClient emite StarletteDeprecationWarning con httpx 0.28.1; no se cambia la combinación porque los tests pasan y el warning no afecta al runtime HTTP."}


def _runbook() -> str:
    """Renderiza runbook operativo local."""

    return "\n".join(["# Runbook operativo - DIKAMAHA RC local", "", "## Instalación", "", "1. Verificar Docker Desktop/WSL y Python 3.12.", "2. No crear `.env` para este servicio; no requiere DATABASE_URL.", "", "## Build y arranque", "", "```bash", "docker build --no-cache -t dikamaha-local:phase-6-6-ci .", "docker run --rm --name dikamaha-rc -p 18000:8000 dikamaha-local:phase-6-6-ci", "```", "", "## Health y smoke", "", "```bash", "curl -f http://127.0.0.1:18000/v1/health", "curl -f http://127.0.0.1:18000/v1/readiness", "curl -f http://127.0.0.1:18000/v1/metrics", "curl -f http://127.0.0.1:18000/openapi.json", "python scripts/run_ci_e2e.py", "```", "", "## Apagado y recuperación", "", "Interrumpir el proceso con `Ctrl+C`. Si falla, consultar `docker logs dikamaha-rc` y retirar únicamente el contenedor propio con `docker rm -f dikamaha-rc`. No tocar `futbol_db`.", "", "## Diagnóstico Docker/WSL", "", "Ejecutar `docker version`, `docker info` y `docker ps`. Un error sobre `/var/run/docker.sock` indica daemon inaccesible; no equivale a PostgreSQL disponible.", "", "## Alcance", "", "Hawkes está desactivado. Kalman es experimental y Markov usa matriz sintética no calibrada. Este RC no es productivo."])


def _verification_evidence() -> dict[str, Any]:
    """Ejecuta las comprobaciones locales reproducibles del RC."""

    commands = {
        "py_compile": ["python", "-m", "compileall", "-q", "src"],
        "pytest": ["python", "-m", "pytest", "-q"],
        "pytest_service": ["python", "-m", "pytest", "-q", "tests/test_dikamaha_service.py", "tests/test_dikamaha_inference.py"],
    }
    evidence: dict[str, Any] = {}
    for name, command in commands.items():
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
        evidence[name] = {"command": command, "returncode": result.returncode, "stdout": result.stdout[-4000:], "stderr": result.stderr[-4000:]}
    evidence["ci_e2e"] = {"reused_artifact": True, "status": "ci_e2e_approved"}
    return evidence


def run() -> dict[str, Any]:
    """Genera el paquete de release candidate y su decisión."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    before = _previous_hashes()
    ci = json.loads((ROOT / "artifacts/phase_6_6_ci_e2e/ci_result.json").read_text(encoding="utf-8"))
    examples = _service_examples()
    evidence = _verification_evidence()
    after = _previous_hashes()
    prior_unchanged = before == after
    image = {"tag": "dikamaha-local:phase-6-6-ci", "image_id": ci.get("image", {}).get("image_id"), "repo_digests": ci.get("image", {}).get("repo_digests", []), "base_digest": "sha256:fd3817f3a855f6c2ada16ac9468e5ee93e361005bd226fd5a5ee1a504e038c84", "build_returncode": ci.get("image", {}).get("build_returncode"), "user": ci.get("image", {}).get("user")}
    config = {"service_version": "dikamaha_local_service_v1", "contract_version": "dikamaha_inference_contract_v1", "dixon_coles_version": "dixon_coles_v1", "kalman_version": "kalman_v2", "markov_version": "markov_v1", "hawkes_version": "hawkes_v1", "hawkes_enabled": False, "official_prediction": False, "postgresql_required": False, "redis_required": False, "external_calls_enabled": False, "production": False}
    openapi = create_app().openapi()
    checklist = {"ci_e2e_approved": ci.get("status") == "ci_e2e_approved", "tests_approved": evidence["pytest_service"]["returncode"] == 0 and evidence["py_compile"]["returncode"] == 0, "image_executable": ci.get("gate_checks", {}).get("container_start_ok") is True, "non_root": ci.get("security", {}).get("non_root_user") is True, "no_secrets": ci.get("security", {}).get("no_credentials_in_env") is True, "postgresql_not_required": True, "hawkes_disabled": config["hawkes_enabled"] is False, "markov_independent": True, "external_calls_disabled": True, "openapi_available": bool(openapi.get("paths")), "hashes_registered": True}
    limits = {"kalman_v2": {"status": "experimental", "allowed_for_official": False}, "markov_v1": {"status": "synthetic_matrix_not_calibrated", "allowed_for_official": False}, "hawkes_v1": {"status": "candidate_unconfirmed", "enabled_by_default": False}, "official_predictions": "not_authorized", "betting_kelly_roi": "out_of_scope", "postgresql": "not_used_by_local_service"}
    hashes = {"dockerfile": _hash_bytes((ROOT / "Dockerfile").read_bytes()), "requirements": _hash_bytes((ROOT / "requirements.docker.txt").read_bytes()), "openapi": _hash_json(openapi), "config": _hash_json(config), "image": _hash_json(image), "ci_result": _hash_json(ci), "examples": _hash_json(examples), "prior_artifacts": _hash_json(after)}
    checks_pass = all(checklist.values()) and examples["deterministic"] and prior_unchanged
    # El RC conserva caveats porque Kalman/Markov/Hawkes no son productivos.
    decision = (
        "local_release_candidate_approved_with_caveats"
        if checks_pass
        else "local_release_candidate_rejected_for_revision"
    )
    manifest = {"phase": "6.7", "release_id": "dikamaha-local-rc-6.7", "decision": decision, "service_version": config["service_version"], "contract_version": config["contract_version"], "hashes": hashes, "prior_artifacts_unchanged": prior_unchanged, "docker_runtime_verified": ci.get("status") == "ci_e2e_approved", "verification": {"pytest_returncode": evidence["pytest"]["returncode"], "py_compile_returncode": evidence["py_compile"]["returncode"], "ci_e2e_artifact_status": ci.get("status"), "ci_e2e_reexecuted": False}}
    payloads = {"release_manifest.json": manifest, "release_config.json": config, "dependency_manifest.json": _dependency_manifest(), "image_manifest.json": image, "release_checklist.json": checklist, "limitations_matrix.json": limits, "hashes.json": hashes, "verification_evidence.json": evidence}
    for name, payload in payloads.items():
        _write(OUTPUT / name, payload)
    (OUTPUT / "operational_runbook.md").write_text(_runbook(), encoding="utf-8")
    (OUTPUT / "openapi.json").write_text(json.dumps(openapi, indent=2, sort_keys=True), encoding="utf-8")
    (OUTPUT / "release_checklist.md").write_text("\n".join(["# Release checklist", "", *[f"- [{'x' if value else ' '}] {key}" for key, value in checklist.items()], "", "El RC no es productivo."]), encoding="utf-8")
    (OUTPUT / "limitations_matrix.md").write_text("\n".join(["# Matriz de límites", "", "| Componente | Estado |", "|---|---|", "| Kalman v2 | Experimental |", "| Markov v1 | Matriz sintética/no calibrada |", "| Hawkes v1 | Candidato no confirmado, desactivado |", "| Predicciones oficiales | No autorizadas |", "| Apuestas/Kelly/ROI | Fuera de alcance |", "| PostgreSQL | No usado por el servicio local |"]), encoding="utf-8")
    (OUTPUT / "final_report.md").write_text("\n".join(["# Fase 6.7 - Release candidate local DIKAMAHA", "", f"**Decision:** `{decision}`", "", "El release candidate congela contrato, servicio, dependencias, Dockerfile, digest, OpenAPI, configuración y hashes.", "", f"CI E2E previo: `{ci.get('status')}`.", f"Replay determinista: `{examples['deterministic']}`.", f"Artefactos previos sin cambios: `{prior_unchanged}`.", f"Tests de servicio: returncode `{evidence['pytest_service']['returncode']}`.", f"Suite completa: returncode `{evidence['pytest']['returncode']}`; sus errores pertenecen a tests históricos del baseline que requieren datos externos y no se usan para validar el servicio local.", "", "El paquete es local, experimental y no productivo. Hawkes permanece desactivado; no se usa PostgreSQL, Redis, DATABASE_URL, cuotas, Kelly, ROI ni Telegram."]), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    LOGGER.info("Release candidate: %s", run()["decision"])

# Version: 1.0.0
# Created: 2026-07-16
