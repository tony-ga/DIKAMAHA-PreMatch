"""Materializa features y contexto pre-match para candidatos independientes.

La fase consume exclusivamente candidatos aprobados por Fase 31. Las features
se construyen con partidos históricos anteriores al kickoff y el contexto se
limita a campos sanitizados por Fase 23.

Requirements:
    - requests
    - SQLAlchemy==2.0.41
    - psycopg2-binary==2.9.10
    - python-dotenv
    - tenacity

Version: 1.0.0
Created: 2026-07-26
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.phase_14_dynamic_markov_recalibration import _team_mapping
from src.phase_22_prematch_first_half_signal import (
    _datasets,
    build_feature_rows,
)
from src.phase_23_prematch_context_fetch import _fetch_row

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "artifacts/phase_31_prospective_cohort_gate/gate_result.json"
OUTPUT = ROOT / "artifacts/phase_33_prematch_input_materialization"


def _load(path: Path) -> Any:
    """Carga un artefacto JSON local."""

    return json.loads(path.read_text(encoding="utf-8"))


def _hash(path: Path) -> str:
    """Calcula SHA-256 de un archivo."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, payload: Any) -> None:
    """Publica JSON determinista mediante reemplazo atómico."""

    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temporary.replace(path)


def _instant(value: Any) -> datetime:
    """Normaliza un timestamp a UTC."""

    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _candidate_match(row: dict[str, Any], mapping: dict[int, int]) -> dict[str, Any]:
    """Convierte un candidato del gate al contrato mínimo de Fase 22."""

    home = int(row["home_team_id"]); away = int(row["away_team_id"])
    return {"match_id": int(row["match_id"]), "match_date": str(row["kickoff_ts"]), "home_team_id": mapping.get(home, home), "away_team_id": mapping.get(away, away)}


def build_candidate_feature_rows(
    history: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    mapping: dict[int, int] | None = None,
) -> list[dict[str, Any]]:
    """Construye cada fila aislando candidatos para impedir contaminación cruzada."""

    active_mapping = mapping or {}
    rows = []
    for candidate in candidates:
        target = _candidate_match(candidate, active_mapping)
        generated = build_feature_rows(history + [target])
        rows.append(next(row for row in generated if int(row["match_id"]) == int(target["match_id"])))
    return rows


def _feature_audit(rows: list[dict[str, Any]], history: list[dict[str, Any]], candidate_ids: set[int]) -> dict[str, Any]:
    """Audita ausencia de datos del objetivo y precedencia estricta."""

    dates = {int(row["match_id"]): _instant(row["match_date"]) for row in history}
    violations = []
    for row in rows:
        cutoff = _instant(row["cutoff_ts"])
        prior_ids = row["home_prior_match_ids"] + row["away_prior_match_ids"]
        for prior_id in prior_ids:
            if prior_id in candidate_ids or prior_id not in dates or dates[prior_id] >= cutoff:
                violations.append({"match_id": int(row["match_id"]), "prior_match_id": int(prior_id)})
    return {"target_match_data_used": False, "target_ids_in_history": False, "temporal_violations": violations, "temporal_causality_pass": not violations, "feature_flags_pass": all(row.get("target_match_data_used") is False for row in rows)}


def _context_usable(row: dict[str, Any], cutoff: str) -> tuple[bool, list[str]]:
    """Valida identidad, kickoff y exclusión de estadísticas post-match."""

    reasons = []
    if row.get("status") != "ok": reasons.append("context_fetch_failed")
    if row.get("identity_pass") is not True: reasons.append("context_identity_failed")
    if not row.get("summary_kickoff_ts") or _instant(row["summary_kickoff_ts"]) != _instant(cutoff): reasons.append("context_kickoff_mismatch")
    if row.get("target_match_statistics_used") is not False: reasons.append("target_statistics_flag")
    return not reasons, reasons


def _materialize_context(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Recupera contexto de ESPN usando IDs provider del staging."""

    return [_fetch_row({"match_id": int(row["match_id"]), "cutoff_ts": str(row["kickoff_ts"])}, {}) for row in candidates]


def _result_without_candidates(gate: dict[str, Any]) -> dict[str, Any]:
    """Genera salida estable cuando el gate aún no aporta candidatos."""

    return {"classification": "waiting_for_independent_cohort", "gate_candidate_count": 0, "history_match_count": 0, "feature_count": 0, "context_count": 0, "prepared_count": 0, "rejected_count": 0, "prepared_candidates": [], "rejected_candidates": [], "predictions_generated": False, "targets_used": False, "router_modified": False, "markets_promoted": False, "gate_classification": gate.get("classification")}


def _publish(result: dict[str, Any], gate: dict[str, Any]) -> None:
    """Publica artefactos de features, contexto, cobertura y auditoría."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    payloads = {"config.json": {"version": "phase_33_prematch_input_materialization_v1", "target_data_forbidden": True, "router_execution": False}, "input_manifest.json": {"gate_hash": _hash(GATE)}, "feature_rows.json": result.get("feature_rows", []), "context_rows.json": result.get("context_rows", []), "coverage.json": result["coverage"], "audit.json": result["audit"], "materialization_result.json": result}
    for name, payload in payloads.items(): _write(OUTPUT / name, payload)
    report = ["# Fase 33 — materialización de insumos pre-match", "", f"**Clasificación:** `{result['classification']}`", "", f"- candidatos del gate: `{result['gate_candidate_count']}`", f"- features materializadas: `{result['feature_count']}`", f"- contextos materializados: `{result['context_count']}`", f"- candidatos preparados: `{result['prepared_count']}`", f"- candidatos rechazados: `{result['rejected_count']}`", "- predicciones generadas: `False`", "- targets usados: `False`", "- router modificado: `False`"]
    (OUTPUT / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    _write(OUTPUT / "hashes.json", {path.name: _hash(path) for path in sorted(OUTPUT.iterdir()) if path.name != "hashes.json"})


def run() -> dict[str, Any]:
    """Materializa inputs prospectivos sin evaluar ni modificar el sistema oficial."""

    gate = _load(GATE); candidates = gate.get("candidate_matches", [])
    if gate.get("classification") != "cohort_ready_for_confirmatory_evaluation" or not candidates:
        result = _result_without_candidates(gate)
        result["gate_candidate_count"] = len(candidates)
        result["coverage"] = {"gate_candidates": len(candidates), "features": 0, "contexts": 0, "prepared": 0, "rejected": 0}
        result["audit"] = {"classification": result["classification"], "history_read": False, "target_data_used_as_feature": False, "context_statistics_used": False, "temporal_causality_pass": True, "router_modified": False}
        _publish(result, gate); return result
    mapping, database = _team_mapping(); history, _, deduplication = _datasets(mapping)
    features = build_candidate_feature_rows(history, candidates, mapping); contexts = _materialize_context(candidates)
    feature_index = {int(row["match_id"]): row for row in features}; context_index = {int(row["match_id"]): row for row in contexts}; candidate_ids = {int(row["match_id"]) for row in candidates}
    prepared, rejected = [], []
    for candidate in candidates:
        match_id = int(candidate["match_id"]); feature, context = feature_index.get(match_id), context_index.get(match_id); ok, reasons = _context_usable(context or {}, str(candidate["kickoff_ts"]))
        if feature is None: reasons.append("missing_features")
        if feature and feature.get("target_match_data_used") is not False: reasons.append("feature_target_data_flag")
        item = {"match_id": match_id, "cutoff_ts": candidate["kickoff_ts"], "reasons": reasons}
        (prepared if not reasons and ok else rejected).append(item)
    feature_audit = _feature_audit(features, history, candidate_ids); classification = "prematch_inputs_ready" if prepared and not rejected and feature_audit["temporal_causality_pass"] else "prematch_inputs_rejected_for_revision" if rejected or not feature_audit["temporal_causality_pass"] else "waiting_for_independent_cohort"
    result = {"classification": classification, "gate_classification": gate.get("classification"), "gate_candidate_count": len(candidates), "history_match_count": len(history), "feature_count": len(features), "context_count": len(contexts), "prepared_count": len(prepared), "rejected_count": len(rejected), "prepared_candidates": prepared, "rejected_candidates": rejected, "feature_rows": features, "context_rows": contexts, "coverage": {"gate_candidates": len(candidates), "history_matches": len(history), "features": len(features), "contexts": len(contexts), "prepared": len(prepared), "rejected": len(rejected)}, "audit": {"classification": classification, "database": database, "deduplication": deduplication, **feature_audit, "context_statistics_used": False, "router_modified": False}, "predictions_generated": False, "targets_used": False, "router_modified": False, "markets_promoted": False}
    _publish(result, gate); LOGGER.info("Fase 33 materialización: %s", classification); return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()

# Version: 1.0.0
# Created: 2026-07-26
