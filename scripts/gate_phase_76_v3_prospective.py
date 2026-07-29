"""Mantiene ciega la cohorte v3 hasta alcanzar cobertura congelada.

Version: 1.0.0
Created: 2026-07-28
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LOCK_DIR = ROOT / "artifacts/phase_76_v3_prospective_lock"
COLLECTION = ROOT / "artifacts/phase_76_v3_prospective_collection"
OUTPUT = ROOT / "artifacts/phase_76_v3_prospective_gate"
MODEL = ROOT / "artifacts/phase_76_domain_robust_reaudit/model_parameters.json"
LOGGER = logging.getLogger(__name__)


def _load(path: Path) -> dict[str, Any]:
    """Carga un objeto JSON requerido."""

    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"invalid_json_object:{path.name}")
    return value


def _hash(path: Path) -> str:
    """Calcula SHA-256 de un archivo."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(name: str, value: Any) -> None:
    """Publica JSON estable."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / name).write_text(
        json.dumps(value, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def run() -> dict[str, Any]:
    """Verifica identidad y decide sin abrir outcomes."""

    lock = _load(LOCK_DIR / "lock.json")
    coverage = _load(COLLECTION / "coverage.json")
    if _hash(MODEL) != lock["model_parameters_sha256"]:
        raise RuntimeError("frozen_model_changed_after_lock")
    ready = (
        int(coverage["matches"]) >= int(lock["minimum_matches"])
        and int(coverage["leagues"]) >= int(lock["minimum_leagues"])
    )
    result = {
        "classification": ("ready_for_evaluation" if ready
                           else "insufficient_coverage"),
        "coverage": coverage,
        "minimum_matches": lock["minimum_matches"],
        "minimum_leagues": lock["minimum_leagues"],
        "metrics_sealed": not ready,
        "outcomes_read": False,
        "model_hash_verified": True,
        "router_modified": False,
    }
    _publish(result, lock)
    return result


def _publish(result: dict[str, Any], lock: dict[str, Any]) -> None:
    """Publica gate, manifiesto, reportes y hashes."""

    _write("coverage.json", result["coverage"])
    _write("audit.json", {
        key: result[key] for key in (
            "metrics_sealed", "outcomes_read", "model_hash_verified",
            "router_modified",
        )
    })
    _write("config.json", {
        "minimum_matches": result["minimum_matches"],
        "minimum_leagues": result["minimum_leagues"],
        "cutoff_utc": lock["cutoff_utc"],
    })
    _write("input_manifest.json", {
        "lock_sha256": _hash(LOCK_DIR / "lock.json"),
        "collection_references_sha256": _hash(COLLECTION / "references.json"),
    })
    report = (
        "# Gate ciego prospectivo v3\n\n"
        f"**Clasificación:** `{result['classification']}`\n\n"
        f"- partidos: `{result['coverage']['matches']}`\n"
        f"- ligas: `{result['coverage']['leagues']}`\n"
        f"- métricas selladas: `{result['metrics_sealed']}`\n"
        "- outcomes leídos: `False`\n"
    )
    (OUTPUT / "final_report.md").write_text(report, encoding="utf-8")
    (OUTPUT / "validation_report.md").write_text(report, encoding="utf-8")
    _write("hashes.json", {
        path.name: _hash(path) for path in sorted(OUTPUT.iterdir())
        if path.is_file() and path.name != "hashes.json"
    })


def main() -> int:
    """Ejecuta el gate; cobertura insuficiente es estado operativo válido."""

    result = run()
    LOGGER.info("Gate prospectivo v3: %s", result["classification"])
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0 - 2026-07-28
