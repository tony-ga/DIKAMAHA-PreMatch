"""Audita si la cohorte prospectiva puede servir como confirmación independiente.

La fase no calcula métricas ni pérdidas: sólo compara identificadores de partido
contra los bloques de calibración, confirmación y router ya publicados.

Version: 1.0.0
Created: 2026-07-26
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
COHORT = ROOT / "artifacts/phase_7_11_prospective_collection/collected_matches.json"
CALIBRATION = ROOT / "artifacts/phase_20_full_preconfirmation_retraining/calibration.json"
CONFIRMATION = ROOT / "artifacts/phase_20_full_preconfirmation_retraining/confirmation.json"
ROUTER = ROOT / "artifacts/phase_21_target_model_router/predictions.json"
OUTPUT = ROOT / "artifacts/phase_29_confirmatory_eligibility_audit"
LOGGER = logging.getLogger(__name__)


def _load(path: Path) -> Any:
    """Carga un artefacto JSON local."""

    return json.loads(path.read_text(encoding="utf-8"))


def _hash(path: Path) -> str:
    """Calcula el hash SHA-256 de un archivo."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _match_ids(path: Path) -> set[int]:
    """Extrae IDs desde listas directas o contenedores de predicciones."""

    payload = _load(path)
    rows = payload.get("predictions", payload) if isinstance(payload, dict) else payload
    return {int(row["match_id"]) for row in rows if row.get("match_id") is not None}


def _audit() -> dict[str, Any]:
    """Compara la cohorte contra cada bloque de decisión ya congelado."""

    cohort = _match_ids(COHORT)
    calibration = _match_ids(CALIBRATION)
    confirmation = _match_ids(CONFIRMATION)
    router = _match_ids(ROUTER)
    overlaps = {
        "phase20_calibration": sorted(cohort & calibration),
        "phase20_confirmation": sorted(cohort & confirmation),
        "phase21_router": sorted(cohort & router),
    }
    independent = not overlaps["phase20_calibration"] and not overlaps["phase20_confirmation"]
    return {
        "classification": "eligible_for_confirmatory_evaluation" if independent else "ineligible_for_confirmatory_evaluation",
        "cohort_match_count": len(cohort),
        "cohort_match_ids": sorted(cohort),
        "overlaps": overlaps,
        "independent_confirmation": independent,
        "metrics_calculated": False,
        "bootstrap_calculated": False,
        "router_modified": False,
        "markets_promoted": False,
    }


def _write(path: Path, payload: Any) -> None:
    """Escribe un JSON determinista mediante reemplazo atómico."""

    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def run() -> dict[str, Any]:
    """Publica la auditoría de elegibilidad sin ejecutar evaluación."""

    result = _audit()
    inputs = {path.name: _hash(path) for path in (COHORT, CALIBRATION, CONFIRMATION, ROUTER)}
    config = {"version": "phase_29_confirmatory_eligibility_audit_v1", "unit": "complete_match", "evaluation_allowed": False}
    manifest = {"phase": "29", "input_hashes": inputs, "output_classification": result["classification"]}
    report = _report(result)
    payloads = {"config.json": config, "input_manifest.json": manifest, "eligibility_audit.json": result}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name, payload in payloads.items():
        _write(OUTPUT / name, payload)
    (OUTPUT / "final_report.md").write_text(report + "\n", encoding="utf-8")
    hashes = {path.name: _hash(path) for path in sorted(OUTPUT.iterdir()) if path.name != "hashes.json"}
    _write(OUTPUT / "hashes.json", hashes)
    LOGGER.info("Fase 29 elegibilidad confirmatoria: %s", result["classification"])
    return result


def _report(result: dict[str, Any]) -> str:
    """Resume el bloqueo sin emitir una conclusión predictiva."""

    calibration = len(result["overlaps"]["phase20_calibration"])
    confirmation = len(result["overlaps"]["phase20_confirmation"])
    return "\n".join([
        "# Fase 29 — auditoría de elegibilidad confirmatoria", "",
        f"**Clasificación:** `{result['classification']}`", "",
        f"- partidos en la cohorte: `{result['cohort_match_count']}`",
        f"- solapamiento con calibración Fase 20: `{calibration}`",
        f"- solapamiento con confirmación Fase 20: `{confirmation}`",
        "- métricas, bootstrap y pérdidas calculados: `False`",
        "- router modificado: `False`",
        "- mercados promovidos: `False`", "",
        "La cohorte no puede usarse como confirmación independiente si comparte "
        "partidos con el bloque de calibración del modelo.",
    ])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()

# Version: 1.0.0
# Created: 2026-07-26
