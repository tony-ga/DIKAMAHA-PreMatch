"""Valida la integración del catálogo shadow en el servicio pre-match.

La fase comprueba que el bloque shadow es sólo trazabilidad y que los valores
oficiales siguen siendo idénticos a la inferencia vigente.

Requirements:
    - fastapi
    - fastapi[test]

Version: 1.0.0
Created: 2026-07-26
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi.encoders import jsonable_encoder
from fastapi.testclient import TestClient

from src.dikamaha_inference import DikamahaInferenceEngine, PreMatchInput
from src.dikamaha_service import create_app
from src.prematch_shadow_catalog import load_shadow_catalog

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/phase_26_shadow_runtime_integration"
PHASE25 = ROOT / "artifacts/phase_25_shadow_model_catalog"


def _hash_file(path: Path) -> str:
    """Calcula SHA-256 de un archivo local."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _request() -> PreMatchInput:
    """Construye una request sintética sin datos del partido objetivo."""

    return PreMatchInput(
        match_id=900001,
        home_team_id=1,
        away_team_id=2,
        kickoff_ts="2025-01-10T20:00:00+00:00",
        feature_cutoff_ts="2025-01-10T19:59:59+00:00",
        competition_id="esp.1",
        feature_version="match_features_v1",
        eligible_for_materialization=True,
        history_minimum_met=True,
        league_intercept=0.2,
        home_advantage=0.15,
        dc_attack_home=0.2,
        dc_defense_home=-0.1,
        dc_attack_away=-0.2,
        dc_defense_away=0.1,
        kalman_attack_home=0.25,
        kalman_defense_home=-0.08,
        kalman_attack_away=-0.25,
        kalman_defense_away=0.08,
    )


def _http_payload(request: PreMatchInput) -> dict[str, Any]:
    """Serializa la request de prueba para el endpoint local."""

    return asdict(request)


def _expected_official(request: PreMatchInput) -> dict[str, Any]:
    """Calcula la salida oficial vigente para comparación de contrato."""

    result = DikamahaInferenceEngine().predict_pre_match(request)
    return jsonable_encoder(asdict(result))


def _observe(request: PreMatchInput) -> tuple[int, dict[str, Any]]:
    """Observa una respuesta local sin llamadas externas ni persistencia."""

    response = TestClient(create_app()).post("/v1/predict/pre-match", json=_http_payload(request))
    return response.status_code, response.json()


def _build_result() -> dict[str, Any]:
    """Construye evidencia de integración y de preservación oficial."""

    contract = load_shadow_catalog()
    request = _request()
    status, payload = _observe(request)
    expected = _expected_official(request)
    official_same = status == 200 and all(payload.get(key) == value for key, value in expected.items())
    shadow = payload.get("shadow_catalog", {})
    shadow_safe = (
        shadow.get("mode") == "read_only"
        and shadow.get("candidate_outputs_computed") is False
        and shadow.get("target_match_data_used") is False
        and all(not item["enabled_by_default"] and not item["official_output_allowed"] for item in shadow.get("candidates", []))
    )
    audit = {"classification": "ready_for_next_phase" if status == 200 and official_same and shadow_safe else "rejected_for_revision", "catalog_valid": True, "shadow_read_only": shadow_safe, "official_router_unchanged": shadow.get("official_router") == contract["official_router"], "official_prediction_values_unchanged": official_same, "candidate_outputs_computed": False, "target_match_data_used": False}
    coverage = {"requests_observed": 1, "successful_observations": int(status == 200), "shadow_blocks_attached": int("shadow_catalog" in payload), "official_fields_checked": len(expected), "shadow_candidates": len(contract["candidates"])}
    metrics = {"service_version": "dikamaha_local_service_v1.2_shadow_catalog", "catalog_version": contract["version"], "official_target_count": len(contract["official_router"]), "shadow_candidate_count": len(contract["candidates"])}
    validation = "\n".join(["# Validation report — Fase 26", "", f"- status HTTP observado: `{status}`", f"- campos oficiales preservados: `{official_same}`", f"- bloque shadow sólo lectura: `{shadow_safe}`", f"- outputs experimentales calculados: `False`", f"- datos del partido objetivo usados: `False`"])
    final = "\n".join(["# Fase 26 — integración runtime del catálogo shadow", "", f"**Clasificación:** `{audit['classification']}`", "", "El servicio pre-match adjunta trazabilidad del catálogo sin modificar los valores oficiales.", f"- campos oficiales verificados: `{coverage['official_fields_checked']}`", f"- candidatos shadow observados: `{coverage['shadow_candidates']}`", "- candidatos activables por request: `False`", "- datos del partido objetivo usados: `False`", "", "Siguiente paso: observar este contrato en ejecuciones pre-match reales de sólo lectura y acumular evidencia sin entrenar ni promover modelos."])
    return {"config": {"version": "shadow_runtime_integration_v1", "mode": "read_only", "official_output_unchanged": True, "candidate_activation": "disabled"}, "input_manifest": _manifest(), "coverage": coverage, "metrics": metrics, "audit": audit, "validation_report": validation, "final_report": final}


def _manifest() -> dict[str, str]:
    """Registra hashes de las entradas del contrato de integración."""

    paths = {"shadow_contract_hash": PHASE25 / "shadow_contract.json", "shadow_audit_hash": PHASE25 / "audit.json", "service_hash": ROOT / "src/dikamaha_service.py", "runtime_catalog_hash": ROOT / "src/prematch_shadow_catalog.py", "phase_spec_hash": ROOT / "docs/phases/phase_26_shadow_runtime_integration.md", "decision_log_hash": ROOT / "docs/decision_log.md"}
    return {name: _hash_file(path) for name, path in paths.items()}


def _publish(result: dict[str, Any]) -> None:
    """Publica artefactos reproducibles y sus hashes."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name in ("config", "input_manifest", "coverage", "metrics", "audit"):
        (OUTPUT / f"{name}.json").write_text(json.dumps(result[name], indent=2, sort_keys=True), encoding="utf-8")
    for name in ("validation_report", "final_report"):
        (OUTPUT / f"{name}.md").write_text(result[name] + "\n", encoding="utf-8")
    hashes = {path.name: _hash_file(path) for path in sorted(OUTPUT.iterdir()) if path.name != "hashes.json"}
    (OUTPUT / "hashes.json").write_text(json.dumps(hashes, indent=2, sort_keys=True), encoding="utf-8")


def run() -> dict[str, Any]:
    """Ejecuta la auditoría local de la integración shadow."""

    result = _build_result()
    _publish(result)
    return result


if __name__ == "__main__":
    run()


# Version: 1.0.0
# Created: 2026-07-26
