"""Reentrenamiento Markov con una temporada histórica adicional.

La Fase 17 incorpora la temporada 2023-24 al train, conserva los 44 partidos
de octubre-noviembre de 2025 como calibración temporal y confirma en diciembre
2025-mayo 2026. Todo el flujo es pre-kickoff y por partido completo.

Requirements:
    - numpy
    - SQLAlchemy==2.0.41
    - python-dotenv

Version: 1.0.0
Created: 2026-07-26
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

from src.phase_10_temporal_target_evaluation import TARGETS
from src.phase_14_dynamic_markov_recalibration import (
    Phase14Config, _block, _dynamic_prior, _hash_file, _match_rows,
    _team_mapping,
)
from src.state_labeling_v1 import StateLabelingConfig, label_rows

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
CANONICAL_WINDOWS = ROOT / "artifacts/phase_01_event_windows_v1/event_windows.json"
CANONICAL_LABELS = ROOT / "artifacts/phase_02_state_labeling_v1/state_labels.json"
PHASE09_WINDOWS = ROOT / "artifacts/phase_09_historical_target_revision/candidate_event_windows.json"
PHASE09_TARGETS = ROOT / "artifacts/phase_09_historical_target_revision/target_labels.json"
PHASE12_WINDOWS = ROOT / "artifacts/phase_12_extension_windows_targets/event_windows.json"
PHASE12_TARGETS = ROOT / "artifacts/phase_12_extension_windows_targets/target_labels.json"
PHASE16_WINDOWS = ROOT / "artifacts/phase_16_backfill_windows/event_windows.json"
PHASE16_TARGETS = ROOT / "artifacts/phase_16_backfill_windows/target_labels.json"
SPEC = ROOT / "docs/specs/temporal_targets_v2.md"
OUTPUT = ROOT / "artifacts/phase_17_extended_markov_retraining"


def _load(path: Path) -> Any:
    """Carga JSON local."""

    return json.loads(path.read_text(encoding="utf-8"))


def _ids(rows: list[dict[str, Any]]) -> set[int]:
    """Extrae IDs únicos de una cohorte de targets."""

    return {int(row["match_id"]) for row in rows}


def _normalize_team_ids(rows: list[dict[str, Any]], mapping: dict[int, int]) -> list[dict[str, Any]]:
    """Normaliza IDs ESPN de extensiones al catálogo interno del modelo."""

    return [{**row, "team_id": mapping.get(int(row["team_id"]), int(row["team_id"])), "opponent_team_id": mapping.get(int(row["opponent_team_id"]), int(row["opponent_team_id"]))} for row in rows]


def _canonical_targets(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Selecciona labels canónicos del artefacto Fase 09."""

    return [row for row in rows if row.get("cohort") == "canonical_v1"]


def _hash_value(value: Any) -> str:
    """Calcula un hash estable de una estructura JSON."""

    raw = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _publish(result: dict[str, Any]) -> None:
    """Publica artefactos contractuales y hashes reproducibles."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name in ("config", "input_manifest", "coverage", "calibration", "confirmation", "audit"):
        (OUTPUT / f"{name}.json").write_text(json.dumps(result[name], indent=2, sort_keys=True, default=str), encoding="utf-8")
    (OUTPUT / "validation_report.md").write_text(result["validation_report"] + "\n", encoding="utf-8")
    (OUTPUT / "final_report.md").write_text(result["final_report"] + "\n", encoding="utf-8")
    hashes = {path.name: _hash_file(path) for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"}
    (OUTPUT / "hashes.json").write_text(json.dumps(hashes, indent=2, sort_keys=True), encoding="utf-8")


def run(config: Phase14Config | None = None) -> dict[str, Any]:
    """Reentrena Markov y evalúa la confirmación ampliada."""

    active = config or Phase14Config()
    mapping, database = _team_mapping()
    canonical_windows, canonical_labels = _load(CANONICAL_WINDOWS), _load(CANONICAL_LABELS)
    previous_windows, previous_targets = _load(PHASE09_WINDOWS), _load(PHASE09_TARGETS)
    backfill_windows, backfill_targets = _load(PHASE16_WINDOWS), _load(PHASE16_TARGETS)
    confirmation_windows, confirmation_targets = _load(PHASE12_WINDOWS), _load(PHASE12_TARGETS)
    previous_windows = _normalize_team_ids(previous_windows, mapping)
    backfill_windows = _normalize_team_ids(backfill_windows, mapping)
    confirmation_windows = _normalize_team_ids(confirmation_windows, mapping)
    state_config = StateLabelingConfig()
    backfill_labels, previous_labels = label_rows(backfill_windows, state_config), label_rows(previous_windows, state_config)
    canonical_targets = _canonical_targets(previous_targets)
    backfill_ids, canonical_ids = _ids(backfill_targets), _ids(canonical_targets)
    previous_ids, confirmation_ids = _ids([row for row in previous_targets if row.get("cohort") != "canonical_v1"]), _ids(confirmation_targets)
    calibration_train_ids = backfill_ids | canonical_ids
    confirmation_train_ids = calibration_train_ids | previous_ids
    calibration_windows = backfill_windows + canonical_windows
    calibration_labels = backfill_labels + canonical_labels
    confirmation_train_windows = calibration_windows + previous_windows
    confirmation_train_labels = calibration_labels + previous_labels
    calibration_history = _match_rows(backfill_windows, mapping) + _match_rows(canonical_windows, mapping)
    confirmation_history = calibration_history + _match_rows(previous_windows, mapping)
    calibration_matches = _match_rows(previous_windows, mapping)
    confirmation_matches = _match_rows(confirmation_windows, mapping)
    calibration_priors = {int(row["match_id"]): _dynamic_prior(row, calibration_history, active) for row in calibration_matches}
    confirmation_priors = {int(row["match_id"]): _dynamic_prior(row, confirmation_history, active) for row in confirmation_matches}
    calibration_targets = backfill_targets + canonical_targets + [row for row in previous_targets if row.get("cohort") != "canonical_v1"]
    confirmation_targets_all = backfill_targets + previous_targets + confirmation_targets
    calibration = _block(calibration_windows, calibration_labels, calibration_targets, calibration_priors, calibration_train_ids, previous_ids, active)
    full_windows = confirmation_train_windows + confirmation_windows
    full_labels = confirmation_train_labels + label_rows(confirmation_windows, state_config)
    confirmation = _block(full_windows, full_labels, confirmation_targets_all, confirmation_priors, confirmation_train_ids, confirmation_ids, active)
    supported = [name for name in TARGETS if confirmation["metrics"][name]["support"]["support_sufficient"]]
    confirmed = [name for name in supported if confirmation["bootstrap_results"][name]["improvement_confirmed"]]
    classification = "promising_unconfirmed" if confirmed else "rejected_for_revision"
    audit = {"classification": classification, "database": database, "fit_scope_calibration": "phase16_backfill_plus_canonical", "fit_scope_confirmation": "phase16_backfill_plus_canonical_plus_phase09", "confirmation_ids_overlap_train": sorted(confirmation_train_ids & confirmation_ids), "calibration_prediction_coverage": len(calibration["predictions"]) == len(previous_ids), "confirmation_prediction_coverage": len(confirmation["predictions"]) == len(confirmation_ids), "target_outcomes_used_as_features": False, "prior_semantics": "rolling_venue_aware_pre_kickoff", "team_identity_normalization": "provider_to_internal_with_explicit_unknown_fallback", "targets_with_sufficient_support": supported, "targets_with_confirmed_improvement": confirmed, "markets_promoted": False}
    manifest = {"canonical_windows_hash": _hash_file(CANONICAL_WINDOWS), "canonical_labels_hash": _hash_file(CANONICAL_LABELS), "phase09_windows_hash": _hash_file(PHASE09_WINDOWS), "phase09_targets_hash": _hash_file(PHASE09_TARGETS), "phase12_windows_hash": _hash_file(PHASE12_WINDOWS), "phase12_targets_hash": _hash_file(PHASE12_TARGETS), "phase16_windows_hash": _hash_file(PHASE16_WINDOWS), "phase16_targets_hash": _hash_file(PHASE16_TARGETS), "target_spec_hash": _hash_file(SPEC), "configuration_hash": _hash_value(asdict(active))}
    coverage = {"backfill_matches": len(backfill_ids), "canonical_matches": len(canonical_ids), "phase09_calibration_matches": len(previous_ids), "phase12_confirmation_matches": len(confirmation_ids), "calibration_train_matches": len(calibration_train_ids), "confirmation_train_matches": len(confirmation_train_ids), "confirmation_windows": len(confirmation_windows), "backfill_labels_generated": len(backfill_labels)}
    validation = f"# Validation report — Fase 17\n\n- train calibración: `{len(calibration_train_ids)}` partidos\n- calibración: `{len(previous_ids)}` partidos\n- train confirmación: `{len(confirmation_train_ids)}` partidos\n- confirmación: `{len(confirmation_ids)}` partidos\n- targets con soporte: `{supported}`\n- mejoras confirmadas: `{confirmed}`."
    lines = ["# Fase 17 — reentrenamiento Markov con backfill", "", f"**Clasificación:** `{classification}`", "", f"- train confirmación: `{len(confirmation_train_ids)}` partidos", f"- confirmación: `{len(confirmation_ids)}` partidos", f"- targets con soporte suficiente: `{supported}`"]
    for name in TARGETS:
        item = confirmation["metrics"][name]; ci = confirmation["bootstrap_results"][name]["ci_95"]; lines.append(f"- `{name}`: Markov `{item['model_log_loss']:.6f}`, baseline `{item['baseline_log_loss']:.6f}`, IC mejora `{ci}`")
    lines.extend(["", "Mercados promovidos: `False`.", "Siguiente paso: conservar sólo la configuración que supere la confirmación completa; si falla, revisar la semántica de estados."])
    result = {"config": asdict(active), "input_manifest": manifest, "coverage": coverage, "calibration": calibration, "confirmation": confirmation, "audit": audit, "validation_report": validation, "final_report": "\n".join(lines)}
    _publish(result)
    LOGGER.info("Fase 17 reentrenamiento Markov: %s", classification)
    return result


# Version: 1.0.0
# Created: 2026-07-26
