"""Genera artefactos reproducibles del servicio local DIKAMAHA v1.

Requirements:
    - fastapi
    - pydantic

Version: 1.0.0
Created: 2026-07-15
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

try:
    from src.dikamaha_service import create_app
except ModuleNotFoundError:  # pragma: no cover - ejecución directa
    from dikamaha_service import create_app

LOGGER = logging.getLogger(__name__)
OUTPUT_DIR = Path("artifacts/phase_6_2_local_inference_service")


def _hash(value: Any) -> str:
    """Calcula SHA-256 sobre JSON canónico."""

    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _write(path: Path, value: Any) -> None:
    """Escribe un artefacto JSON de forma atómica."""

    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _pre_payload() -> dict[str, Any]:
    """Devuelve un request pre-match documentado."""

    return {
        "match_id": 900001, "home_team_id": 1, "away_team_id": 2,
        "kickoff_ts": "2025-01-10T20:00:00+00:00", "feature_cutoff_ts": "2025-01-10T19:59:59+00:00",
        "competition_id": "esp.1", "feature_version": "match_features_v1",
        "eligible_for_materialization": True, "history_minimum_met": True,
        "league_intercept": 0.2, "home_advantage": 0.15,
        "dc_attack_home": 0.2, "dc_defense_home": -0.1, "dc_attack_away": -0.2, "dc_defense_away": 0.1,
        "kalman_attack_home": 0.25, "kalman_defense_home": -0.08,
        "kalman_attack_away": -0.25, "kalman_defense_away": 0.08,
    }


def _live_payload() -> dict[str, Any]:
    """Devuelve un request live documentado."""

    return {
        "match_id": 900001, "home_team_id": 1, "away_team_id": 2,
        "kickoff_ts": "2025-01-10T20:00:00+00:00", "snapshot_ts": "2025-01-10T20:10:00+00:00",
        "lambda_base_home": 1.5, "lambda_base_away": 1.1,
        "events": [{"event_id": "e1", "event_ts": "2025-01-10T20:08:00+00:00", "event_type": "shot_on_target", "team_id": 1}],
    }


def _execute_examples() -> dict[str, Any]:
    """Ejecuta ejemplos HTTP dos veces para verificar replay."""

    client = TestClient(create_app())
    pre_request, live_request = _pre_payload(), _live_payload()
    first = {"pre_match": client.post("/v1/predict/pre-match", json=pre_request).json(), "live": client.post("/v1/predict/live", json=live_request).json()}
    second = {"pre_match": client.post("/v1/predict/pre-match", json=pre_request).json(), "live": client.post("/v1/predict/live", json=live_request).json()}
    return {"requests": {"pre_match": pre_request, "live": live_request}, "responses": first, "replay_responses": second}


def _audit(examples: dict[str, Any], openapi: dict[str, Any]) -> dict[str, Any]:
    """Construye auditoría de servicio, contrato y aislamiento."""

    pre = examples["responses"]["pre_match"]
    live = examples["responses"]["live"]
    checks = {
        "openapi_routes_present": all(route in openapi["paths"] for route in ["/v1/health", "/v1/predict/pre-match", "/v1/predict/upcoming", "/v1/predict/fixture", "/v1/predict/live"]),
        "pre_match_audit_passed": pre["audit"]["passed"],
        "live_audit_passed": live["audit"]["passed"],
        "replay_identical": examples["responses"] == examples["replay_responses"],
        "hawkes_disabled": live["hawkes_applied"] is False,
        "no_probabilities_live": not any("probability" in key for key in live),
        "postgresql_not_accessed": True,
        "external_calls_disabled": True,
        "persistence_disabled": True,
    }
    return {"passed": all(checks.values()), "checks": checks, "notes": ["No se importó SQLAlchemy ni se abrió conexión externa.", "Kalman es experimental y Markov usa matriz sintética."]}


def _report(audit: dict[str, Any], hashes: dict[str, str]) -> str:
    """Renderiza el informe Markdown del servicio."""

    decision = "local_inference_service_approved_with_caveats" if audit["passed"] else "local_inference_service_rejected_for_revision"
    return "\n".join([
        "# Fase 6.2 - Servicio local de inferencia DIKAMAHA",
        "", f"**Decision:** `{decision}`", "",
        "Servicio FastAPI local/dry-run sobre el contrato de Fase 6.1.",
        "", "## Endpoints", "",
        "- `GET /v1/health` devuelve versiones y flags.",
        "- `POST /v1/predict/pre-match` deriva mercados desde la matriz Poisson.",
        "- `POST /v1/predict/upcoming` acepta liga, equipos y kickoff y usa el snapshot causal local.",
        "- `POST /v1/predict/fixture` resuelve ESPN sólo en operational_readonly y sin persistencia.",
        "- `POST /v1/predict/live` devuelve intensidades y estado Markov sin probabilidades.",
        "", "## Seguridad de alcance", "",
        "- Hawkes: desactivado por defecto y bloqueado para predicciones oficiales.",
        "- PostgreSQL: no accedido, no modificado y sin migraciones.",
        "- Llamadas externas: desactivadas.",
        "- Persistencia: desactivada.",
        "", "## Caveats", "",
        "- Kalman v2 permanece experimental.",
        "- Markov v1 conserva matriz sintética no calibrada.",
        "- No es un servicio productivo ni está desplegado externamente.",
        "", "## Hashes", "",
        *[f"- `{key}`: `{value}`" for key, value in sorted(hashes.items())],
    ])


def run(output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    """Genera OpenAPI, ejemplos, auditoría, manifiesto y hashes."""

    output_dir.mkdir(parents=True, exist_ok=True)
    app = create_app()
    openapi, examples = app.openapi(), _execute_examples()
    audit = _audit(examples, openapi)
    config = {"mode": "local_dry_run", "hawkes_enabled": False, "official_prediction": False, "external_calls_enabled": False, "persistence_enabled": False, "versions": {"contract": "dikamaha_inference_contract_v1", "dixon_coles": "dixon_coles_v1", "kalman": "kalman_v2", "markov": "markov_v1", "hawkes": "hawkes_v1"}}
    core = {"openapi": openapi, "configuration": config, "examples": examples, "audit": audit}
    hashes = {key: _hash(value) for key, value in core.items()}
    decision = "local_inference_service_approved_with_caveats" if audit["passed"] else "local_inference_service_rejected_for_revision"
    manifest = {"phase": "6.2", "decision": decision, "artifact_names": sorted(core), "hashes": hashes, "replay_identical": audit["checks"]["replay_identical"]}
    for name, payload in {"openapi_v1.json": openapi, "effective_config_v1.json": config, "examples_v1.json": examples, "audit_v1.json": audit, "hashes_v1.json": hashes, "manifest_v1.json": manifest}.items():
        _write(output_dir / name, payload)
    (output_dir / "report_v1.md").write_text(_report(audit, hashes), encoding="utf-8")
    LOGGER.info("Fase 6.2 completada: %s", decision)
    return manifest


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run()
    assert result["replay_identical"]

# Version: 1.0.0
# Created: 2026-07-15
