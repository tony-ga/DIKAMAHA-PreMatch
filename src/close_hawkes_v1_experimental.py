"""Cierre experimental de `hawkes_v1`.

Consolida las fases 5.3, 5.4 y 5.5 para clasificar Hawkes como componente
experimental no productivo. No calibra parametros, no modifica Markov y no
escribe en PostgreSQL.

Version: 1.0.0
Created: 2026-07-16
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from evaluate_hawkes_historical import _ensure_dir, _stable_hash, _write_json
from hawkes_v1_integration import HawkesIntegrationConfig

LOGGER = logging.getLogger(__name__)

BASE_DIR = Path("/mnt/c/users/marco/desktop/dikahama_project/futbol_predictor")
OUTPUT_DIR = BASE_DIR / "artifacts" / "phase_5_6_hawkes_v1_closure"

PHASE_5_3 = BASE_DIR / "artifacts" / "phase_5_3_hawkes_v1_limited_historical" / "hawkes_v1_result.json"
PHASE_5_4 = BASE_DIR / "artifacts" / "phase_5_4_hawkes_v1_sensitivity" / "hawkes_v1_sensitivity_result.json"
PHASE_5_5 = BASE_DIR / "artifacts" / "phase_5_5_hawkes_v1_confirmatory" / "hawkes_v1_confirmatory_result.json"


def _load_json(path: Path) -> Any:
    """Carga un artefacto JSON."""
    return json.loads(path.read_text(encoding="utf-8"))


def _decision_payload() -> dict[str, Any]:
    """Construye la decision consolidada de cierre."""
    phase_53 = _load_json(PHASE_5_3)
    phase_54 = _load_json(PHASE_5_4)
    phase_55 = _load_json(PHASE_5_5)
    return {
        "decision_version": "hawkes_v1_closure_v1",
        "status": "hawkes_candidate_unconfirmed",
        "hawkes_enabled_default": False,
        "production_use_allowed": False,
        "official_predictions_allowed": False,
        "markov_remains_current_in_play_output": True,
        "frozen_candidate": {
            "config_id": "alpha_reduced",
            "source_phase": "phase_5_4",
            "experimental_only": True,
        },
        "consolidated_evidence": {
            "phase_5_3": {
                "decision": phase_53["decision"],
                "coverage": phase_53["coverage"],
                "metrics": phase_53["metrics"],
            },
            "phase_5_4": {
                "decision": phase_54["decision"],
                "candidate_configuration": phase_54["candidate_configuration"],
                "coverage": phase_54["coverage"],
            },
            "phase_5_5": {
                "decision": phase_55["decision"],
                "partition": phase_55["partition"],
                "metrics": phase_55["metrics"],
            },
        },
        "conclusion": {
            "lambda_comparison": "hawkes vs markov reviewed across phases 5.3-5.5",
            "oos_limitation": "confirmatory block covers 280 snapshots across 4 matches only",
            "predictive_evidence_sufficient": False,
            "overexcitation_risk_visible": True,
        },
        "reopen_conditions": [
            "greater_historical_sample",
            "independent_temporal_validation",
            "consistent_improvement_vs_markov",
            "postgresql_verifiable",
            "pytest_suite_executable",
        ],
        "calibration_prohibition": {
            "same_700_snapshots_for_recalibration": False,
            "description": "No calibrar Hawkes sobre los mismos 700 snapshots usados en evaluacion.",
        },
    }


def _risk_matrix(phase_55: dict[str, Any]) -> list[dict[str, Any]]:
    """Genera una matriz compacta de riesgos."""
    return [
        {
            "risk": "overexcitation_on_dense_matches",
            "severity": "high",
            "evidence": "uplift medio positivo y peor MAE/log score confirmatorio frente a Markov",
            "mitigation": "no activar por defecto; mantener candidato congelado",
        },
        {
            "risk": "insufficient_confirmatory_sample",
            "severity": "high",
            "evidence": f"{phase_55['partition']['confirmatory_match_ids']} only",
            "mitigation": "ampliar muestra historica independiente antes de reabrir",
        },
        {
            "risk": "database_verification_incomplete",
            "severity": "medium",
            "evidence": phase_55["manifest"]["database_before"],
            "mitigation": "instalar dependencia faltante y repetir SELECT before/after",
        },
        {
            "risk": "markov_upstream_synthetic_limit",
            "severity": "medium",
            "evidence": "Markov sigue experimental/no calibrado",
            "mitigation": "no declarar utilidad predictiva productiva de Hawkes",
        },
    ]


def _integration_contract() -> dict[str, Any]:
    """Define la interfaz futura sin activarla."""
    config = HawkesIntegrationConfig()
    return {
        "integration_version": config.config_version,
        "hawkes_enabled_default": config.hawkes_enabled,
        "input_contract": [
            "lambda_markov_home",
            "lambda_markov_away",
            "snapshot_ts",
            "valid_events",
            "markov_provenance",
        ],
        "optional_output_contract": [
            "lambda_hawkes_home",
            "lambda_hawkes_away",
            "hawkes_applied",
            "integration_version",
            "frozen_candidate",
        ],
        "forbidden_outputs": [
            "probabilities",
            "postgresql_writes",
        ],
    }


def _write_report(path: Path, payload: dict[str, Any], risks: list[dict[str, Any]]) -> None:
    """Escribe el informe consolidado de cierre."""
    lines = [
        "# Hawkes v1 experimental closure",
        "",
        f"- Status: `{payload['status']}`",
        f"- `hawkes_enabled`: `{str(payload['hawkes_enabled_default']).lower()}`",
        f"- Official predictions allowed: `{str(payload['official_predictions_allowed']).lower()}`",
        f"- Frozen candidate: `{payload['frozen_candidate']['config_id']}`",
        "",
        "## Consolidated decision",
        "",
        "- Markov permanece como salida in-play vigente.",
        "- Hawkes no muestra evidencia predictiva suficiente fuera de muestra.",
        "- El bloque OOS confirmatorio cubre solo 4 partidos y no confirma mejora frente a Markov.",
        "- El riesgo de sobreexcitación permanece visible en partidos densos en eventos.",
        "",
        "## Reopen conditions",
        "",
    ]
    lines.extend([f"- {item}" for item in payload["reopen_conditions"]])
    lines.extend(["", "## Risks", ""])
    lines.extend([f"- {item['risk']}: {item['mitigation']}" for item in risks])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    """Genera artefactos de cierre experimental."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    LOGGER.info("Generando cierre experimental de Hawkes v1.")
    _ensure_dir(OUTPUT_DIR)
    payload = _decision_payload()
    phase_55 = _load_json(PHASE_5_5)
    risks = _risk_matrix(phase_55)
    contract = _integration_contract()
    manifest = {
        "phase_5_3_source": str(PHASE_5_3),
        "phase_5_4_source": str(PHASE_5_4),
        "phase_5_5_source": str(PHASE_5_5),
        "postgresql_modified": False,
        "markov_modified": False,
        "hawkes_enabled_default": False,
    }
    hashes = {
        "decision_hash": _stable_hash(payload),
        "risks_hash": _stable_hash(risks),
        "contract_hash": _stable_hash(contract),
        "manifest_hash": _stable_hash(manifest),
    }
    _write_json(OUTPUT_DIR / "hawkes_v1_closure_decision.json", payload)
    _write_json(OUTPUT_DIR / "hawkes_v1_closure_frozen_config.json", payload["frozen_candidate"])
    _write_json(OUTPUT_DIR / "hawkes_v1_closure_risks.json", risks)
    _write_json(OUTPUT_DIR / "hawkes_v1_closure_integration_contract.json", contract)
    _write_json(OUTPUT_DIR / "hawkes_v1_closure_manifest.json", manifest)
    _write_json(OUTPUT_DIR / "hawkes_v1_closure_hashes.json", hashes)
    _write_report(OUTPUT_DIR / "hawkes_v1_closure_report.md", payload, risks)
    LOGGER.info("Cierre experimental completado.")


if __name__ == "__main__":
    main()

# Version: 1.0.0
# Created: 2026-07-16
