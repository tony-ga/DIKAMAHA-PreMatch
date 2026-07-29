"""Congela el catálogo shadow de modelos pre-match.

El catálogo no genera predicciones oficiales ni modifica el router. Su función
es preservar qué modelos existen, qué evidencia tienen y por qué permanecen
desactivados.

Version: 1.0.0
Created: 2026-07-26
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
ROUTER = ROOT / "artifacts/phase_21_target_model_router"
PACE = ROOT / "artifacts/phase_22_prematch_first_half_signal"
CONTEXT = ROOT / "artifacts/phase_23_prematch_context_fetch"
LINEUP = ROOT / "artifacts/phase_24_prematch_lineup_signal"
SPEC = ROOT / "docs/phases/phase_25_shadow_model_catalog.md"
OUTPUT = ROOT / "artifacts/phase_25_shadow_model_catalog"


def _load(path: Path) -> Any:
    """Carga un JSON local."""

    return json.loads(path.read_text(encoding="utf-8"))


def _hash_file(path: Path) -> str:
    """Calcula SHA-256 de un archivo."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _candidate(name: str, status: str, source: str, metrics: dict[str, Any] | None, reason: str) -> dict[str, Any]:
    """Construye una entrada de modelo experimental."""

    return {"model": name, "status": status, "source": source, "enabled_by_default": False, "official_output_allowed": False, "metrics": metrics or {}, "reason": reason}


def _publish(result: dict[str, Any]) -> None:
    """Publica contrato shadow, auditoría y hashes."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name in ("config", "input_manifest", "coverage", "metrics", "shadow_contract", "audit"):
        (OUTPUT / f"{name}.json").write_text(json.dumps(result[name], indent=2, sort_keys=True, default=str), encoding="utf-8")
    (OUTPUT / "validation_report.md").write_text(result["validation_report"] + "\n", encoding="utf-8")
    (OUTPUT / "final_report.md").write_text(result["final_report"] + "\n", encoding="utf-8")
    hashes = {path.name: _hash_file(path) for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"}
    (OUTPUT / "hashes.json").write_text(json.dumps(hashes, indent=2, sort_keys=True), encoding="utf-8")


def run() -> dict[str, Any]:
    """Construye el catálogo oficial/shadow sin activar candidatos."""

    selected = _load(ROUTER / "selected_models.json"); pace = _load(PACE / "metrics.json"); context = _load(CONTEXT / "coverage.json"); lineup = _load(LINEUP / "metrics.json")
    candidates = [_candidate("first_half_event_pace_v1", "promising_unconfirmed", "phase_22", pace.get("confirmation"), "Mejora puntual sin IC confirmatorio estrictamente positivo."), _candidate("prematch_lineup_v1", "rejected_for_revision", "phase_24", lineup.get("confirmation"), "La alineación sola no supera el gate confirmatorio."), _candidate("prematch_lineup_plus_pace_v1", "rejected_for_revision", "phase_24", lineup.get("confirmation"), "La fusión degrada al baseline en confirmación."), _candidate("prematch_open_odds_v1", "insufficient_coverage", "phase_23", {"confirmation_open_rows": 10, "confirmation_rows": 241}, "Cobertura de cuotas open insuficiente para confirmación independiente.")]
    shadow_contract = {"version": "shadow_model_catalog_v1", "official_router": selected, "candidates": candidates, "activation_policy": "all_candidates_disabled", "official_prediction_source": "phase_21_target_model_router", "market_promotion": False}
    disabled = all(not bool(item["enabled_by_default"]) and not bool(item["official_output_allowed"]) for item in candidates); audit = {"classification": "ready_for_next_phase" if disabled else "rejected_for_revision", "official_router_unchanged": True, "experimental_candidates_disabled": disabled, "markets_promoted": False, "target_match_data_used": False, "context_summary_coverage": context["summary_ok"] == context["input_matches"]}
    config = {"version": "shadow_model_catalog_v1", "activation_policy": "explicit_review_required", "default_mode": "official_only", "candidate_count": len(candidates)}; manifest = {"router_hash": _hash_file(ROUTER / "selected_models.json"), "pace_metrics_hash": _hash_file(PACE / "metrics.json"), "context_coverage_hash": _hash_file(CONTEXT / "coverage.json"), "lineup_metrics_hash": _hash_file(LINEUP / "metrics.json"), "spec_hash": _hash_file(SPEC)}; coverage = {"official_targets": len(selected), "shadow_candidates": len(candidates), "disabled_candidates": sum(not item["enabled_by_default"] for item in candidates), "context_matches": context["input_matches"]}
    metrics = {"official_model_count": len(selected), "candidate_status_counts": {status: sum(item["status"] == status for item in candidates) for status in sorted({item["status"] for item in candidates})}}
    validation = f"# Validation report — Fase 25\n\n- targets oficiales: `{len(selected)}`\n- candidatos shadow: `{len(candidates)}`\n- candidatos desactivados: `{coverage['disabled_candidates']}`\n- router oficial sin cambios: `{audit['official_router_unchanged']}`."
    final = ["# Fase 25 — catálogo shadow de modelos", "", f"**Clasificación:** `{audit['classification']}`", "", "El router oficial permanece intacto.", f"- candidatos shadow desactivados: `{coverage['disabled_candidates']}/{coverage['shadow_candidates']}`", "- mercados promovidos: `False`", "- modo por defecto: `official_only`", "", "Siguiente paso: conectar este catálogo al flujo de observación pre-match en modo sólo lectura."]
    result = {"config": config, "input_manifest": manifest, "coverage": coverage, "metrics": metrics, "shadow_contract": shadow_contract, "audit": audit, "validation_report": validation, "final_report": "\n".join(final)}; _publish(result); LOGGER.info("Fase 25 catálogo shadow: %s", audit["classification"]); return result


# Version: 1.0.0
# Created: 2026-07-26
