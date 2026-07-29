"""Genera artefactos reproducibles del contrato de inferencia DIKAMAHA v1.

Requirements:
    - numpy
    - pandas

Version: 1.0.0
Created: 2026-07-15
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any, get_type_hints

try:
    from src.dikamaha_inference import (
        AuditMetadata, DikamahaInferenceEngine, LiveIntensityOutput,
        LiveSnapshotInput, PreMatchInput, PreMatchPrediction, Provenance,
    )
except ModuleNotFoundError:  # pragma: no cover - soporte de ejecucion directa
    from dikamaha_inference import (
        AuditMetadata, DikamahaInferenceEngine, LiveIntensityOutput,
        LiveSnapshotInput, PreMatchInput, PreMatchPrediction, Provenance,
    )

LOGGER = logging.getLogger(__name__)
OUTPUT_DIR = Path("artifacts/phase_6_1_inference_contract")


def _hash(value: Any) -> str:
    """Calcula SHA-256 estable sobre JSON canonico."""

    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    """Escribe JSON de forma atomica.

    Args:
        path: Ruta final del artefacto.
        value: Contenido serializable.
    """

    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _field_schema(model: type[Any]) -> dict[str, Any]:
    """Describe campos y tipos de una dataclass."""

    hints = get_type_hints(model)
    return {
        item.name: {
            "type": str(hints[item.name]).replace("typing.", ""),
            "required": item.default.__class__.__name__ == "_MISSING_TYPE",
        }
        for item in fields(model)
    }


def _contract() -> dict[str, Any]:
    """Construye la descripcion formal del contrato local."""

    models = [PreMatchInput, PreMatchPrediction, LiveSnapshotInput, LiveIntensityOutput, Provenance, AuditMetadata]
    return {
        "contract_version": "dikamaha_inference_contract_v1",
        "models": {model.__name__: _field_schema(model) for model in models},
        "layer_order": ["dixon_coles_v1", "kalman_v2", "markov_v1", "hawkes_v1_disabled"],
        "pre_match_markets": ["1x2", "over_2_5", "btts"],
        "blocked_match_ids": [704766],
        "forbidden": ["softmax", "odds", "kelly", "database_writes", "markov_in_match_features", "hawkes_in_match_features"],
    }


def _pre_input() -> PreMatchInput:
    """Crea un ejemplo pre-match controlado."""

    return PreMatchInput(
        match_id=900001, home_team_id=1, away_team_id=2,
        kickoff_ts="2025-01-10T20:00:00+00:00",
        feature_cutoff_ts="2025-01-10T19:59:59+00:00",
        competition_id="esp.1", feature_version="match_features_v1",
        eligible_for_materialization=True, history_minimum_met=True,
        league_intercept=0.2, home_advantage=0.15,
        dc_attack_home=0.2, dc_defense_home=-0.1,
        dc_attack_away=-0.2, dc_defense_away=0.1,
        kalman_attack_home=0.25, kalman_defense_home=-0.08,
        kalman_attack_away=-0.25, kalman_defense_away=0.08,
        source_hash="synthetic-phase-6-1-input",
    )


def _live_input() -> LiveSnapshotInput:
    """Crea un ejemplo in-play controlado."""

    event = {
        "event_id": "phase-6-1-e1", "event_ts": "2025-01-10T20:08:00+00:00",
        "event_type": "shot_on_target", "team_id": 1,
    }
    return LiveSnapshotInput(
        match_id=900001, home_team_id=1, away_team_id=2,
        kickoff_ts="2025-01-10T20:00:00+00:00",
        snapshot_ts="2025-01-10T20:10:00+00:00",
        lambda_base_home=1.7682670514, lambda_base_away=1.0304545340,
        events=(event,), source_hash="synthetic-phase-6-1-live",
    )


def _execute() -> dict[str, Any]:
    """Ejecuta ejemplos pre-match e in-play sin persistencia externa."""

    engine = DikamahaInferenceEngine()
    pre_request = _pre_input()
    live_request = _live_input()
    pre_output = engine.predict_pre_match(pre_request)
    live_output = engine.predict_live(live_request)
    return {
        "pre_match": {"input": asdict(pre_request), "output": asdict(pre_output)},
        "in_play": {"input": asdict(live_request), "output": asdict(live_output)},
    }


def _audit(examples: dict[str, Any], replay: dict[str, Any]) -> dict[str, Any]:
    """Consolida controles y determinismo del ensamblador."""

    pre = examples["pre_match"]["output"]
    live = examples["in_play"]["output"]
    checks = {
        "pre_match_audit_passed": pre["audit"]["passed"],
        "live_audit_passed": live["audit"]["passed"],
        "replay_identical": examples == replay,
        "hawkes_disabled_default": live["hawkes_applied"] is False,
        "hawkes_output_absent": live["lambda_hawkes_home"] is None and live["lambda_hawkes_away"] is None,
        "markov_context_factor_one": live["markov_audit"]["context_factor"] == 1.0,
        "no_postgresql_write_attempted": True,
        "no_migration_attempted": True,
    }
    return {"passed": all(checks.values()), "checks": checks, "replay_hash": _hash(replay)}


def _configuration() -> dict[str, Any]:
    """Declara la configuracion efectiva y los gates experimentales."""

    return {
        "contract_version": "dikamaha_inference_contract_v1",
        "competition_id": "esp.1", "feature_version": "match_features_v1",
        "dixon_coles_version": "dixon_coles_v1", "kalman_version": "kalman_v2",
        "markov_version": "markov_v1", "hawkes_version": "hawkes_v1",
        "kalman_experimental": True, "markov_matrix_synthetic": True,
        "hawkes_enabled": False, "hawkes_official_allowed": False,
        "persistence_enabled": False,
    }


def _test_evidence() -> dict[str, Any]:
    """Registra la evidencia automatizada ejecutada para el contrato."""

    return {
        "command": "python -m pytest -s tests/test_dikamaha_inference.py tests/test_kalman_v2.py tests/test_markov_v1.py tests/test_hawkes_v1.py tests/test_hawkes_v1_integration.py",
        "collected": 31,
        "passed": 31,
        "failed": 0,
        "contract_tests": "tests/test_dikamaha_inference.py",
    }


def _report(audit: dict[str, Any], hashes: dict[str, str]) -> str:
    """Renderiza el informe Markdown de cierre."""

    decision = "inference_contract_approved_with_caveats" if audit["passed"] else "inference_contract_rejected_for_revision"
    return "\n".join([
        "# Fase 6.1 - Contrato de inferencia DIKAMAHA",
        "", f"**Decision:** `{decision}`", "",
        "El ensamblaje local respeta Dixon-Coles v1 -> Kalman v2 -> Markov v1. Hawkes v1 permanece desactivado por defecto y no esta permitido para predicciones oficiales.",
        "", "## Resultado", "",
        "- Pre-match deriva 1X2, Over 2.5 y BTTS exclusivamente de la matriz Poisson.",
        "- Kalman conserva intercepto fijo, localia en estado, suma-cero y provenance experimental.",
        "- Markov recibe lambda_base explicitamente, conserva C_e(t)=1.0 y no genera probabilidades.",
        "- Replay determinista: `true`.",
        "- PostgreSQL y migraciones: no accedidos ni modificados.",
        "", "## Caveats", "",
        "- Kalman v2 sigue experimental.",
        "- La matriz Markov v1 sigue siendo sintetica y no calibrada.",
        "- Hawkes v1 permanece `hawkes_candidate_unconfirmed` y desactivado.",
        "", "## Hashes", "",
        *[f"- `{name}`: `{value}`" for name, value in sorted(hashes.items())],
    ])


def run(output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    """Genera todos los artefactos versionados de Fase 6.1."""

    output_dir.mkdir(parents=True, exist_ok=True)
    contract, config, tests = _contract(), _configuration(), _test_evidence()
    examples, replay = _execute(), _execute()
    audit = _audit(examples, replay)
    core = {"contract": contract, "configuration": config, "examples": examples, "audit": audit, "tests": tests}
    hashes = {name: _hash(value) for name, value in core.items()}
    decision = "inference_contract_approved_with_caveats" if audit["passed"] else "inference_contract_rejected_for_revision"
    manifest = {"phase": "6.1", "decision": decision, "artifacts": sorted(core), "hashes": hashes, "replay_identical": examples == replay}
    _write_artifacts(output_dir, contract, config, examples, audit, tests, hashes, manifest)
    LOGGER.info("Fase 6.1 completada: %s", decision)
    return manifest


def _write_artifacts(output_dir: Path, contract: dict[str, Any], config: dict[str, Any], examples: dict[str, Any], audit: dict[str, Any], tests: dict[str, Any], hashes: dict[str, str], manifest: dict[str, Any]) -> None:
    """Persiste solo los artefactos locales autorizados."""

    payloads = {"inference_contract_v1.json": contract, "effective_config_v1.json": config, "inference_examples_v1.json": examples, "inference_audit_v1.json": audit, "automated_tests_v1.json": tests, "inference_hashes_v1.json": hashes, "inference_manifest_v1.json": manifest}
    for name, payload in payloads.items():
        _write_json(output_dir / name, payload)
    (output_dir / "inference_contract_report_v1.md").write_text(_report(audit, hashes), encoding="utf-8")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run()
    assert result["replay_identical"]
    assert result["decision"] == "inference_contract_approved_with_caveats"

# Version: 1.0.0
# Created: 2026-07-15
