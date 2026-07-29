"""Ejecuta y documenta el cierre de suites de prueba de Fase 6.8.

Las pruebas PostgreSQL quedan visibles como integración opcional y no se
ejecutan sin ``--run-postgres``. No modifica artefactos históricos.

Requirements:
    pip install pytest

Version: 1.0.0
Created: 2026-07-16
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/phase_6_8_test_suite_closure"
LOGGER = logging.getLogger(__name__)


def _run(name: str, command: list[str]) -> dict[str, Any]:
    """Ejecuta una suite y conserva salida para auditoría."""

    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    output = f"{result.stdout}\n{result.stderr}"
    return {
        "name": name,
        "command": command,
        "returncode": result.returncode,
        "summary": _summary(output),
        "output": output,
    }


def _summary(output: str) -> dict[str, int]:
    """Extrae conteos pytest sin ocultar la salida original."""

    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    match = re.search(r"(\d+) passed", output)
    if match:
        counts["passed"] = int(match.group(1))
    for key in ("failed", "errors", "skipped"):
        match = re.search(rf"(\d+) {key}", output)
        if match:
            counts[key] = int(match.group(1))
    return counts


def _write_json(path: Path, value: Any) -> None:
    """Escribe JSON atómicamente."""

    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _sha256(path: Path) -> str:
    """Calcula SHA-256 de un archivo."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _frozen_artifact_hashes() -> dict[str, str]:
    """Calcula hashes de artefactos que las pruebas no deben regenerar."""

    roots = [
        ROOT / "artifacts/phase_2_4_match_features_v1_dry_run",
        ROOT / "artifacts/phase_2_5_match_features_v1_baseline",
        ROOT / "artifacts/phase_6_7_local_release_candidate",
    ]
    files = [path for root in roots for path in root.glob("*") if path.is_file()]
    return {str(path.relative_to(ROOT)): _sha256(path) for path in sorted(files)}


def _error_inventory() -> list[dict[str, str]]:
    """Documenta los ocho errores previos y su corrección trazable."""

    names = [
        "test_anti_leakage_rules",
        "test_competition_mapping",
        "test_contract_counts",
        "test_flag_names_match_contract",
        "test_regression_manifest",
        "test_reproducibility",
        "test_targets_are_consistent",
        "test_windows_and_nulls",
    ]
    return [
        {
            "file": "tests/test_match_features_baseline.py",
            "test": name,
            "exception": "subprocess.CalledProcessError during setUpClass",
            "root_cause": "El test ejecutaba generate_match_features_dry_run.py, que exige DATABASE_URL y psycopg2.",
            "classification": "test_obsolete_for_local_release",
            "impact": "Bloqueaba ocho tests de contrato y podía regenerar artefactos históricos.",
            "resolution": "Ahora valida exclusivamente artefactos congelados; la regeneración queda en test postgres explícito.",
        }
        for name in names
    ]


def _test_matrix() -> list[dict[str, str]]:
    """Declara el alcance de cada suite para CI y operación local."""

    return [
        {"suite": "unit", "selector": "not historical and not postgres", "database": "none", "purpose": "modelos sintéticos, contrato y servicio local"},
        {"suite": "historical", "selector": "historical", "database": "none", "purpose": "artefactos congelados y dry-runs locales"},
        {"suite": "postgres", "selector": "postgres --run-postgres", "database": "read_only", "purpose": "regeneración explícita del baseline en directorio temporal"},
        {"suite": "docker_e2e", "selector": "scripts/run_ci_e2e.py", "database": "none", "purpose": "imagen, HTTP y seguridad"},
    ]


def _docker_runtime() -> dict[str, Any]:
    """Detecta el daemon Docker sin construir ni ejecutar contenedores."""

    result = subprocess.run(
        ["docker", "version", "--format", "{{.Server.Version}}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "available": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
        "e2e_command": [sys.executable, "scripts/run_ci_e2e.py"],
    }


def _markdown(results: list[dict[str, Any]], decision: str, docker: dict[str, Any]) -> str:
    """Renderiza informe breve y verificable."""

    lines = ["# Fase 6.8 - Cierre de pruebas", "", f"**Decisión:** `{decision}`", "", "| Suite | Passed | Errors | Skipped | Return code |", "|---|---:|---:|---:|---:|"]
    for item in results:
        data = item["summary"]
        lines.append(f"| {item['name']} | {data['passed']} | {data['errors']} | {data['skipped']} | {item['returncode']} |")
    lines.extend(["", "Las ocho incidencias previas eran una única dependencia de setup: el test de baseline ejecutaba una regeneración PostgreSQL. Esa operación ahora es una integración marcada `postgres` y requiere `--run-postgres`.", "", f"Daemon Docker disponible en esta ejecución: `{docker['available']}`."])
    return "\n".join(lines)


def main() -> int:
    """Ejecuta suites locales y escribe los artefactos de Fase 6.8."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    frozen_before = _frozen_artifact_hashes()
    results = [
        _run("pytest_full", [sys.executable, "-m", "pytest", "-ra", "-vv"]),
        _run("pytest_service", [sys.executable, "-m", "pytest", "-q", "tests/test_dikamaha_service.py", "tests/test_dikamaha_inference.py"]),
        _run("py_compile", [sys.executable, "-m", "compileall", "-q", "src"]),
    ]
    frozen_after = _frozen_artifact_hashes()
    frozen_unchanged = frozen_before == frozen_after
    passed = all(item["returncode"] == 0 for item in results)
    docker = _docker_runtime()
    decision = "test_suite_approved" if passed and frozen_unchanged and docker["available"] else "test_suite_approved_with_caveats" if passed and frozen_unchanged else "test_suite_rejected_for_revision"
    _write_json(OUTPUT / "error_inventory.json", _error_inventory())
    _write_json(OUTPUT / "test_matrix.json", _test_matrix())
    _write_json(OUTPUT / "ci_test_summary.json", {"decision": decision, "suites": results, "postgres_execution": "not_run_without_--run-postgres", "docker_runtime": docker, "frozen_artifacts_unchanged": frozen_unchanged, "frozen_artifact_hash": hashlib.sha256(json.dumps(frozen_after, sort_keys=True).encode()).hexdigest()})
    (OUTPUT / "pytest_full_report.md").write_text(_markdown(results, decision, docker), encoding="utf-8")
    (OUTPUT / "final_report.md").write_text(_markdown(results, decision, docker), encoding="utf-8")
    hashes = {
        path.name: _sha256(path)
        for path in sorted(OUTPUT.iterdir())
        if path.is_file() and path.name != "hashes.json"
    }
    _write_json(OUTPUT / "hashes.json", hashes)
    LOGGER.info("Fase 6.8: %s", decision)
    return 0 if passed else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0
# Created: 2026-07-16
