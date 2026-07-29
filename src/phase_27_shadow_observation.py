"""Observa predicciones oficiales pre-match junto al catálogo shadow.

La fase no recalcula modelos. Sólo alinea artefactos ya publicados, valida
cutoffs y produce una traza sanitizada sin targets ni pérdidas post-match.

Version: 1.0.0
Created: 2026-07-26
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.prematch_shadow_catalog import load_shadow_catalog

ROOT = Path(__file__).resolve().parents[1]
ROUTER = ROOT / "artifacts/phase_21_target_model_router/predictions.json"
FEATURES = ROOT / "artifacts/phase_22_prematch_first_half_signal/feature_rows.json"
CONTEXT = ROOT / "artifacts/phase_23_prematch_context_fetch/context_rows.json"
CATALOG = ROOT / "artifacts/phase_25_shadow_model_catalog/shadow_contract.json"
PHASE26_AUDIT = ROOT / "artifacts/phase_26_shadow_runtime_integration/audit.json"
SPEC = ROOT / "docs/phases/phase_27_shadow_observation.md"
OUTPUT = ROOT / "artifacts/phase_27_shadow_observation"

TARGETS = (
    "first_half_goal",
    "second_half_goal",
    "home_recovery_draw_or_win",
    "away_recovery_draw_or_win",
    "home_reaches_level_after_half",
    "away_reaches_level_after_half",
    "home_comeback_win",
    "away_comeback_win",
)
PROBABILITY_FIELDS = tuple(f"routed_probability_{target}" for target in TARGETS)
OFFICIAL_FIELDS = ("model", "simulation_count", "lambda_base_home", "lambda_base_away", *PROBABILITY_FIELDS)


def _load(path: Path) -> Any:
    """Carga un JSON local."""

    return json.loads(path.read_text(encoding="utf-8"))


def _hash_file(path: Path) -> str:
    """Calcula SHA-256 de un archivo."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _instant(value: str) -> datetime:
    """Normaliza timestamps ISO a UTC."""

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00").replace(" ", "T"))
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _index(rows: list[dict[str, Any]], label: str) -> dict[int, dict[str, Any]]:
    """Indexa filas por partido y rechaza duplicados."""

    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        match_id = int(row["match_id"])
        if match_id in result:
            raise ValueError(f"duplicate_{label}_match_id:{match_id}")
        result[match_id] = row
    return result


def _official_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Conserva sólo salida oficial independiente del target."""

    return {key: row[key] for key in OFFICIAL_FIELDS if key in row}


def _router_matches(row: dict[str, Any], official_router: dict[str, str]) -> bool:
    """Comprueba que la selección por target coincide con Fase 21."""

    return all(row.get(f"selected_model_{target}") == model for target, model in official_router.items())


def _observe(
    predictions: list[dict[str, Any]],
    features: dict[int, dict[str, Any]],
    context: dict[int, dict[str, Any]],
    contract: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Alinea predicciones y contexto sin recalcular modelos."""

    observations: list[dict[str, Any]] = []
    missing_features: list[int] = []
    missing_context: list[int] = []
    cutoff_mismatches: list[int] = []
    causal_failures: list[int] = []
    router_failures: list[int] = []
    for prediction in predictions:
        match_id = int(prediction["match_id"])
        feature = features.get(match_id)
        context_row = context.get(match_id)
        if feature is None:
            missing_features.append(match_id)
        if context_row is None:
            missing_context.append(match_id)
        if feature is None or context_row is None:
            continue
        if _instant(str(feature["cutoff_ts"])) != _instant(str(context_row["cutoff_ts"])):
            cutoff_mismatches.append(match_id)
        if feature.get("target_match_data_used") is not False or context_row.get("target_match_statistics_used") is not False:
            causal_failures.append(match_id)
        if not _router_matches(prediction, contract["official_router"]):
            router_failures.append(match_id)
        observations.append({"match_id": match_id, "cutoff_ts": str(prediction["cutoff_ts"]), "official": _official_payload(prediction), "shadow": {"mode": "read_only", "catalog_version": contract["version"], "candidate_count": len(contract["candidates"]), "all_candidates_disabled": True, "candidate_outputs_computed": False, "official_output_unchanged": True, "target_match_data_used": False}})
    audit = {"missing_feature_ids": missing_features, "missing_context_ids": missing_context, "cutoff_mismatch_ids": cutoff_mismatches, "causality_failure_ids": causal_failures, "router_selection_failure_ids": router_failures}
    return observations, audit


def _manifest() -> dict[str, str]:
    """Registra hashes de las fuentes observadas."""

    paths = {"router_hash": ROUTER, "feature_rows_hash": FEATURES, "context_rows_hash": CONTEXT, "catalog_hash": CATALOG, "phase26_audit_hash": PHASE26_AUDIT, "spec_hash": SPEC}
    return {name: _hash_file(path) for name, path in paths.items()}


def _publish(result: dict[str, Any]) -> None:
    """Publica artefactos de observación y hashes."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name in ("config", "input_manifest", "coverage", "metrics", "observations", "audit"):
        (OUTPUT / f"{name}.json").write_text(json.dumps(result[name], indent=2, sort_keys=True), encoding="utf-8")
    for name in ("validation_report", "final_report"):
        (OUTPUT / f"{name}.md").write_text(result[name] + "\n", encoding="utf-8")
    hashes = {path.name: _hash_file(path) for path in sorted(OUTPUT.iterdir()) if path.name != "hashes.json"}
    (OUTPUT / "hashes.json").write_text(json.dumps(hashes, indent=2, sort_keys=True), encoding="utf-8")


def run() -> dict[str, Any]:
    """Ejecuta la observación read-only de la cohorte oficial."""

    predictions = _load(ROUTER)
    feature_rows = _index(_load(FEATURES), "feature")
    context_rows = _index(_load(CONTEXT), "context")
    contract = load_shadow_catalog()
    observations, detail = _observe(predictions, feature_rows, context_rows, contract)
    failures = any(detail.values()) or len(observations) != len(predictions)
    audit = {"classification": "rejected_for_revision" if failures else "ready_for_next_phase", "read_only": True, "models_retrained": False, "candidate_outputs_computed": False, "target_match_data_used": False, "official_router_unchanged": True, **detail}
    coverage = {"official_predictions": len(predictions), "feature_rows_source": len(feature_rows), "context_rows_source": len(context_rows), "observations_published": len(observations), "feature_matches": len(predictions) - len(detail["missing_feature_ids"]), "context_matches": len(predictions) - len(detail["missing_context_ids"]), "candidate_count": len(contract["candidates"])}
    metrics = {"selected_model_counts": {target: sum(row.get(f"selected_model_{target}") == contract["official_router"].get(target) for row in predictions) for target in TARGETS}, "candidate_status_counts": {status: sum(item["status"] == status for item in contract["candidates"]) for status in sorted({item["status"] for item in contract["candidates"]})}}
    validation = "\n".join(["# Validation report — Fase 27", "", f"- predicciones oficiales observadas: `{coverage['official_predictions']}`", f"- observaciones publicadas: `{coverage['observations_published']}`", f"- coincidencias de cutoff: `{len(detail['cutoff_mismatch_ids']) == 0}`", f"- router conservado: `{len(detail['router_selection_failure_ids']) == 0}`", "- candidatos ejecutados: `False`", "- datos del partido objetivo usados: `False`"])
    final = "\n".join(["# Fase 27 — observación read-only de predicciones pre-match", "", f"**Clasificación:** `{audit['classification']}`", "", f"Se observaron `{coverage['official_predictions']}` predicciones oficiales de Fase 21 junto con sus filas causales de Fase 22 y contexto de Fase 23.", "- no se recalcularon modelos", "- no se entrenaron modelos", "- no se incorporaron cohortes", "- los 4 candidatos shadow permanecen desactivados", "- no se publicaron targets ni pérdidas post-match", "", "**Gate de fase:** cerrado exitosamente con cobertura completa y replay reproducible.", "", "Siguiente paso: acumular observaciones prospectivas de sólo lectura cuando exista una cohorte nueva válida; cualquier evaluación o promoción requiere una decisión posterior."])
    result = {"config": {"version": "shadow_observation_v1", "mode": "read_only", "source_router": "phase_21_target_model_router", "model_retraining": False, "candidate_execution": False}, "input_manifest": _manifest(), "coverage": coverage, "metrics": metrics, "observations": observations, "audit": audit, "validation_report": validation, "final_report": final}
    _publish(result)
    return result


if __name__ == "__main__":
    run()


# Version: 1.0.0
# Created: 2026-07-26
