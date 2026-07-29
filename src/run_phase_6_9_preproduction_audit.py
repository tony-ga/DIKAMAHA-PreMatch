"""Genera la auditoría Go/No-Go preproducción de DIKAMAHA.

Lee evidencia local versionada; no ejecuta Docker, PostgreSQL ni modelos.

Version: 1.0.0
Created: 2026-07-16
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/phase_6_9_preproduction_audit"
LOGGER = logging.getLogger(__name__)


def _read(relative: str) -> dict[str, Any]:
    """Carga un artefacto JSON versionado."""

    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def _hash(path: Path) -> str:
    """Calcula SHA-256 de un archivo."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, payload: Any) -> None:
    """Escribe JSON atómicamente."""

    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _readiness(test: dict[str, Any], ci: dict[str, Any]) -> list[dict[str, str]]:
    """Construye la matriz de pruebas y evidencia de ejecución."""

    full = next(item for item in test["suites"] if item["name"] == "pytest_full")
    service = next(item for item in test["suites"] if item["name"] == "pytest_service")
    compile_check = next(item for item in test["suites"] if item["name"] == "py_compile")
    return [
        {"area": "pytest completo", "status": "pass", "evidence": f"{full['summary']['passed']} passed, {full['summary']['skipped']} skipped", "deployment_effect": "ready"},
        {"area": "contrato/servicio", "status": "pass", "evidence": f"{service['summary']['passed']} passed", "deployment_effect": "ready"},
        {"area": "histórico congelado", "status": "pass", "evidence": "8 tests de baseline sin regeneración", "deployment_effect": "ready"},
        {"area": "PostgreSQL explícito", "status": "caveat", "evidence": test["postgres_execution"], "deployment_effect": "data integration unverified"},
        {"area": "py_compile", "status": "pass", "evidence": f"returncode {compile_check['returncode']}", "deployment_effect": "ready"},
        {"area": "Docker E2E", "status": "caveat", "evidence": f"artefacto={ci['status']}; ejecución CI reciente no demostrable", "deployment_effect": "requires fresh CI run"},
    ]


def _security(ci: dict[str, Any]) -> dict[str, Any]:
    """Resume controles presentes y brechas preproducción."""

    return {
        "verified": ci["security"],
        "payload_validation": {"pre_match_extra_forbidden": True, "live_extra_forbidden": True, "event_extra_allowed": True, "risk": "event metadata adicional se acepta; revisar antes de exponer a clientes no confiables"},
        "logs": {"structured_json": True, "payloads_not_logged": True, "secrets_not_logged": True, "request_id": True},
        "http_surface": {"endpoints": ["/v1/health", "/v1/readiness", "/v1/metrics", "/v1/predict/pre-match", "/v1/predict/live", "/openapi.json"], "authentication": "not_implemented", "tls": "not_implemented", "rate_limit": "not_implemented"},
        "preproduction_gaps": ["autenticación/autorización", "TLS o proxy de perímetro", "límite de tamaño de request", "rate limiting"],
    }


def _dependencies(release: dict[str, Any]) -> dict[str, Any]:
    """Audita fijación de dependencias e imagen base."""

    manifest = _read("artifacts/phase_6_7_local_release_candidate/dependency_manifest.json")
    image = _read("artifacts/phase_6_7_local_release_candidate/image_manifest.json")
    return {
        "requirements_pinned": all("==" in item for item in manifest["pinned"]),
        "requirements": manifest["pinned"],
        "requirements_hash": manifest["requirements_hash"],
        "base_image_digest": image["base_digest"],
        "image_digest": image["image_id"],
        "transitive_lockfile": "absent",
        "sbom": "absent",
        "vulnerability_scan": "absent",
        "test_warning": manifest["warning"],
        "release_config_hash": release["hashes"]["config"],
    }


def _models() -> dict[str, Any]:
    """Declara límites no negociables de los modelos vigentes."""

    return {
        "dixon_coles_v1": {"status": "approved_baseline", "official_predictions": False},
        "kalman_v2": {"status": "experimental", "official_predictions": False},
        "markov_v1": {"status": "synthetic_matrix_not_calibrated", "official_predictions": False},
        "hawkes_v1": {"status": "candidate_unconfirmed_disabled", "enabled": False, "official_predictions": False},
        "prohibited": ["official predictions", "odds", "Kelly", "ROI", "Telegram", "Hawkes default activation"],
    }


def _operational_risks(test: dict[str, Any]) -> list[dict[str, str]]:
    """Registra riesgos operativos y criterios de resolución."""

    return [
        {"risk": "CI Docker sin ejecución reciente verificable", "severity": "high", "evidence": test["docker_runtime"]["stderr"], "required_action": "ejecutar workflow phase-6-8-test-closure en runner Docker y conservar artefactos con timestamp"},
        {"risk": "modelos experimentales", "severity": "high", "evidence": "Kalman experimental; Markov sintético; Hawkes desactivado", "required_action": "calibración/validación independiente antes de cualquier predicción oficial"},
        {"risk": "sin límites CPU/memoria", "severity": "high", "evidence": "Dockerfile y workflows no declaran recursos", "required_action": "definir límites y prueba de carga antes de staging"},
        {"risk": "sin controles de perímetro HTTP", "severity": "high", "evidence": "sin auth, TLS ni rate limit", "required_action": "añadir proxy/autenticación/rate limit en diseño de staging"},
        {"risk": "warning Starlette/httpx", "severity": "low", "evidence": "1 warning en pytest", "required_action": "fijar combinación soportada o migrar TestClient tras prueba de contrato"},
        {"risk": "integración PostgreSQL no ejecutada", "severity": "medium", "evidence": test["postgres_execution"], "required_action": "ejecutar --run-postgres con credenciales read-only fuera del release local"},
    ]


def _report(decision: str, blockers: list[str]) -> str:
    """Renderiza el dictamen final de auditoría."""

    lines = ["# Fase 6.9 - Auditoría Go/No-Go preproducción", "", f"**Decisión:** `{decision}`", "", "El servicio local es reproducible y las suites aplicables pasan, pero no está autorizado para staging ni despliegue controlado.", "", "## Bloqueos"]
    lines.extend(f"- {item}" for item in blockers)
    lines.extend(["", "## Evidencia", "", "- Fase 6.6 conserva `ci_e2e_approved`, pero no prueba una ejecución reciente de GitHub Actions.", "- Fase 6.8 registra `65 passed, 1 skipped, 0 errors`; PostgreSQL fue omitido explícitamente.", "- Hawkes sigue desactivado y no se habilitaron predicciones oficiales.", "", "PostgreSQL no fue modificado ni usado por esta auditoría."])
    return "\n".join(lines)


def main() -> int:
    """Genera artefactos de auditoría sin ejecutar infraestructura."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    release = _read("artifacts/phase_6_7_local_release_candidate/release_manifest.json")
    test = _read("artifacts/phase_6_8_test_suite_closure/ci_test_summary.json")
    ci = _read("artifacts/phase_6_6_ci_e2e/ci_result.json")
    blockers = ["evidencia CI Docker reciente no disponible", "Kalman/Markov/Hawkes no son productivos", "faltan límites de recursos y controles de perímetro HTTP"]
    decision = "no_go_experimental_only"
    matrix = _readiness(test, ci)
    payloads = {
        "go_no_go_decision.json": {"decision": decision, "release": release["release_id"], "blockers": blockers, "ci_evidence_versioned": ci["status"], "ci_evidence_recent": False, "deployment_authorized": False, "official_predictions_authorized": False, "postgresql_modified": False},
        "readiness_matrix.json": matrix,
        "security_audit.json": _security(ci),
        "model_limitations.json": _models(),
        "dependency_audit.json": _dependencies(release),
        "operational_risks.json": _operational_risks(test),
    }
    for name, value in payloads.items():
        _write(OUTPUT / name, value)
    (OUTPUT / "final_report.md").write_text(_report(decision, blockers), encoding="utf-8")
    hashes = {path.name: _hash(path) for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"}
    _write(OUTPUT / "hashes.json", hashes)
    _write(OUTPUT / "manifest.json", {"phase": "6.9", "decision": decision, "inputs": {"release": _hash(ROOT / "artifacts/phase_6_7_local_release_candidate/release_manifest.json"), "tests": _hash(ROOT / "artifacts/phase_6_8_test_suite_closure/ci_test_summary.json"), "ci": _hash(ROOT / "artifacts/phase_6_6_ci_e2e/ci_result.json")}, "hashes": hashes, "postgresql_modified": False})
    LOGGER.info("Fase 6.9: %s", decision)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0
# Created: 2026-07-16
