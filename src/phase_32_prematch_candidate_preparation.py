"""Prepara candidatos independientes para el router pre-match.

No calcula probabilidades: sólo alinea features, contexto y cutoff causal.

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
GATE = ROOT / "artifacts/phase_31_prospective_cohort_gate/gate_result.json"
FEATURES = ROOT / "artifacts/phase_22_prematch_first_half_signal/feature_rows.json"
CONTEXT = ROOT / "artifacts/phase_23_prematch_context_fetch/context_rows.json"
PROSPECTIVE_FEATURES = ROOT / "artifacts/phase_33_prematch_input_materialization/feature_rows.json"
PROSPECTIVE_CONTEXT = ROOT / "artifacts/phase_33_prematch_input_materialization/context_rows.json"
OUTPUT = ROOT / "artifacts/phase_32_prematch_candidate_preparation"
LOGGER = logging.getLogger(__name__)


def _load(path: Path) -> Any:
    """Carga un JSON local."""

    return json.loads(path.read_text(encoding="utf-8"))


def _index(path: Path) -> dict[int, dict[str, Any]]:
    """Indexa filas pre-match y rechaza IDs duplicados."""

    rows = _load(path)
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        match_id = int(row["match_id"])
        if match_id in result:
            raise ValueError(f"duplicate_match_id:{match_id}")
        result[match_id] = row
    return result


def _source_index(primary: Path, fallback: Path) -> dict[int, dict[str, Any]]:
    """Prefiere materialización prospectiva y conserva el fallback histórico."""

    source = primary if primary.exists() else fallback
    return _index(source)


def _prepare(
    candidates: list[dict[str, Any]],
    features: dict[int, dict[str, Any]],
    context: dict[int, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Separa filas alineadas de candidatos bloqueados."""

    prepared, rejected = [], []
    for candidate in candidates:
        match_id = int(candidate["match_id"])
        feature, context_row = features.get(match_id), context.get(match_id)
        reasons = []
        if feature is None:
            reasons.append("missing_features")
        if context_row is None:
            reasons.append("missing_context")
        if feature and context_row and feature["cutoff_ts"] != context_row["cutoff_ts"]:
            reasons.append("cutoff_mismatch")
        if feature and feature.get("target_match_data_used") is not False:
            reasons.append("feature_target_data_flag")
        if context_row and context_row.get("target_match_statistics_used") is not False:
            reasons.append("context_target_data_flag")
        item = {"match_id": match_id, "cutoff_ts": feature.get("cutoff_ts") if feature else None, "reasons": reasons}
        (rejected if reasons else prepared).append(item)
    return prepared, rejected


def _hash(path: Path) -> str:
    """Calcula el hash SHA-256 de una fuente."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, payload: Any) -> None:
    """Escribe JSON determinista y atómico."""

    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temporary.replace(path)


def run() -> dict[str, Any]:
    """Prepara la cohorte aprobada sin ejecutar el router."""

    gate = _load(GATE)
    if gate.get("classification") != "cohort_ready_for_confirmatory_evaluation":
        result = {"classification": "waiting_for_independent_cohort", "gate_candidate_count": len(gate.get("candidate_matches", [])), "prepared_count": 0, "rejected_count": 0, "prepared_candidates": [], "rejected_candidates": [], "predictions_generated": False, "targets_used": False, "router_modified": False, "markets_promoted": False}
        OUTPUT.mkdir(parents=True, exist_ok=True)
        _write(OUTPUT / "config.json", {"version": "phase_32_prematch_candidate_preparation_v2", "router_execution": False, "target_data_forbidden": True})
        _write(OUTPUT / "preparation_result.json", result)
        (OUTPUT / "final_report.md").write_text("# Fase 32 — preparación pre-match\n\n**Clasificación:** `waiting_for_independent_cohort`\n\n- candidatos del gate: `" + str(len(gate.get("candidate_matches", []))) + "`\n- inputs preparados: `0`\n- inputs rechazados: `0`\n- predicciones generadas: `False`\n- targets usados: `False`\n- router modificado: `False`\n", encoding="utf-8")
        return result
    features = _source_index(PROSPECTIVE_FEATURES, FEATURES)
    context = _source_index(PROSPECTIVE_CONTEXT, CONTEXT)
    prepared, rejected = _prepare(gate["candidate_matches"], features, context)
    result = {"classification": "prematch_inputs_ready" if prepared and not rejected else "waiting_for_independent_cohort" if not prepared and not rejected else "prematch_inputs_rejected_for_revision", "gate_candidate_count": len(gate["candidate_matches"]), "prepared_count": len(prepared), "rejected_count": len(rejected), "prepared_candidates": prepared, "rejected_candidates": rejected, "predictions_generated": False, "targets_used": False, "router_modified": False, "markets_promoted": False}
    config = {"version": "phase_32_prematch_candidate_preparation_v1", "unit": "complete_match", "router_execution": False, "target_data_forbidden": True}
    sources = [GATE, FEATURES, CONTEXT]
    if PROSPECTIVE_FEATURES.exists(): sources.append(PROSPECTIVE_FEATURES)
    if PROSPECTIVE_CONTEXT.exists(): sources.append(PROSPECTIVE_CONTEXT)
    manifest = {"phase": "32", "source_hashes": {str(path.relative_to(ROOT)): _hash(path) for path in sources}}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name, payload in {"config.json": config, "input_manifest.json": manifest, "preparation_result.json": result}.items():
        _write(OUTPUT / name, payload)
    report = f"# Fase 32 — preparación pre-match\n\n**Clasificación:** `{result['classification']}`\n\n- candidatos del gate: `{len(gate['candidate_matches'])}`\n- inputs preparados: `{len(prepared)}`\n- inputs rechazados: `{len(rejected)}`\n- predicciones generadas: `False`\n- targets usados: `False`\n- router modificado: `False`\n"
    (OUTPUT / "final_report.md").write_text(report, encoding="utf-8")
    _write(OUTPUT / "hashes.json", {path.name: _hash(path) for path in sorted(OUTPUT.iterdir()) if path.name != "hashes.json"})
    LOGGER.info("Fase 32 preparación: %s", result["classification"])
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()

# Version: 1.0.0
# Created: 2026-07-26
