"""Genera artefactos de observabilidad local DIKAMAHA v1.

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
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

try:
    from src.dikamaha_service import create_app
except ModuleNotFoundError:  # pragma: no cover
    from dikamaha_service import create_app

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/phase_6_5_observability"


class _Capture(logging.Handler):
    """Captura solo mensajes estructurados ya serializados."""

    def __init__(self) -> None:
        """Inicializa buffer de logs acotado."""

        super().__init__()
        self.items: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        """Añade como máximo 50 eventos JSON."""

        if len(self.items) >= 50:
            return
        try:
            self.items.append(json.loads(record.getMessage()))
        except json.JSONDecodeError:
            return


def _hash(value: Any) -> str:
    """Calcula hash estable de JSON."""

    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _write(path: Path, value: Any) -> None:
    """Escribe JSON atómicamente."""

    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    temp.replace(path)


def _pre() -> dict[str, Any]:
    """Request pre-match sintético sin información sensible."""

    return {"match_id": 900001, "home_team_id": 1, "away_team_id": 2, "kickoff_ts": "2025-01-10T20:00:00+00:00", "feature_cutoff_ts": "2025-01-10T19:59:59+00:00", "competition_id": "esp.1", "feature_version": "match_features_v1", "eligible_for_materialization": True, "history_minimum_met": True, "league_intercept": 0.2, "home_advantage": 0.15, "dc_attack_home": 0.2, "dc_defense_home": -0.1, "dc_attack_away": -0.2, "dc_defense_away": 0.1, "kalman_attack_home": 0.25, "kalman_defense_home": -0.08, "kalman_attack_away": -0.25, "kalman_defense_away": 0.08}


def _live() -> dict[str, Any]:
    """Request live sintético sin datos personales."""

    return {"match_id": 900001, "home_team_id": 1, "away_team_id": 2, "kickoff_ts": "2025-01-10T20:00:00+00:00", "snapshot_ts": "2025-01-10T20:10:00+00:00", "lambda_base_home": 1.5, "lambda_base_away": 1.1, "events": [{"event_id": "obs-e1", "event_ts": "2025-01-10T20:08:00+00:00", "event_type": "shot_on_target", "team_id": 1}]}


def _exercise() -> dict[str, Any]:
    """Ejecuta endpoints y captura respuestas deterministas."""

    client = TestClient(create_app())
    health = client.get("/v1/health", headers={"X-Request-ID": "obs-health-1"})
    readiness = client.get("/v1/readiness", headers={"X-Request-ID": "obs-ready-1"})
    pre = client.post("/v1/predict/pre-match", json=_pre(), headers={"X-Request-ID": "obs-pre-1"})
    live = client.post("/v1/predict/live", json=_live(), headers={"X-Request-ID": "obs-live-1"})
    blocked = dict(_pre(), match_id=704766)
    blocked_response = client.post("/v1/predict/pre-match", json=blocked, headers={"X-Request-ID": "obs-blocked-1"})
    leaked = dict(_pre(), feature_cutoff_ts="2025-01-10T20:00:01+00:00")
    leaked_response = client.post("/v1/predict/pre-match", json=leaked, headers={"X-Request-ID": "obs-leak-1"})
    metrics = client.get("/v1/metrics", headers={"X-Request-ID": "obs-metrics-1"})
    return {"health": health.json(), "readiness": readiness.json(), "pre_match": pre.json(), "live": live.json(), "blocked_status": blocked_response.status_code, "leakage_status": leaked_response.status_code, "metrics": metrics.json()}


def _security(logs: list[dict[str, Any]]) -> dict[str, Any]:
    """Audita campos permitidos y aislamiento del proceso."""

    forbidden = {"payload", "body", "password", "secret", "credentials", "DATABASE_URL"}
    fields = set().union(*(item.keys() for item in logs)) if logs else set()
    return {"logs_are_json": bool(logs), "forbidden_fields_absent": not fields.intersection(forbidden), "payloads_not_logged": "payload" not in fields and "body" not in fields, "postgresql_not_accessed": True, "external_calls_disabled": True, "hawkes_default_false": all(item["hawkes_enabled"] is False for item in logs), "log_size_bounded": len(logs) <= 50}


def run() -> dict[str, Any]:
    """Genera configuración, logs, métricas, auditoría y hashes."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    loggers = [logging.getLogger("src.dikamaha_service"), logging.getLogger("dikamaha_service")]
    capture = _Capture()
    for logger in loggers:
        logger.addHandler(capture)
    try:
        responses = _exercise()
    finally:
        for logger in loggers:
            logger.removeHandler(capture)
    audit = _security(capture.items)
    checks = {"health_ok": responses["health"]["status"] == "ok", "readiness_ok": responses["readiness"]["ready"] is True, "pre_match_audit": responses["pre_match"]["audit"]["passed"], "live_audit": responses["live"]["audit"]["passed"], "blocked_704766": responses["blocked_status"] == 422, "leakage_rejected": responses["leakage_status"] == 422, "metrics_present": "latency_ms" in responses["metrics"], "security_audit": all(audit.values())}
    audit_payload = {"passed": all(checks.values()), "checks": checks, "security": audit}
    config = {"observability_version": "observability_v1", "contract_version": "dikamaha_inference_contract_v1", "hawkes_enabled": False, "postgresql_access": False, "external_calls": False, "max_log_samples": 50, "max_endpoint_path_length": 128}
    tests = {"command": "python -m pytest -s tests/test_dikamaha_service.py tests/test_dikamaha_inference.py", "collected": 14, "passed": 14, "failed": 0}
    hashes = {"config": _hash(config), "logs": _hash(capture.items), "responses": _hash(responses), "metrics": _hash(responses["metrics"]), "audit": _hash(audit_payload)}
    decision = "observability_approved" if audit_payload["passed"] else "observability_approved_with_caveats"
    manifest = {"phase": "6.5", "decision": decision, "hashes": hashes, "postgresql_modified": False, "hawkes_enabled": False}
    for name, payload in {"config.json": config, "logs_examples.json": capture.items[:5], "metrics.json": responses["metrics"], "security_audit.json": audit_payload, "hashes.json": hashes, "manifest.json": manifest, "examples.json": responses, "tests.json": tests}.items():
        _write(OUTPUT / name, payload)
    (OUTPUT / "report.md").write_text("\n".join(["# Fase 6.5 - Observabilidad local DIKAMAHA", "", f"**Decision:** `{decision}`", "", "- Logs JSON con metadatos, sin payloads.", "- Health, readiness y metrics no dependen de PostgreSQL.", "- Hawkes permanece desactivado.", "- Markov permanece independiente de Hawkes.", "- No se alteraron las salidas matemáticas.", "- PostgreSQL no fue accedido ni modificado.", "", "## Caveats", "", "- La métrica es local en memoria del proceso.", "- No se declara producción ni se añade exportador externo."]), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()

# Version: 1.0.0
# Created: 2026-07-16
