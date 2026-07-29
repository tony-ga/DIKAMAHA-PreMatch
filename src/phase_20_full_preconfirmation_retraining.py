"""Evaluación final con todo el histórico disponible antes de diciembre 2025."""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

from src.phase_14_dynamic_markov_recalibration import Phase14Config, _block, _dynamic_prior, _hash_file, _instant, _match_rows, _team_mapping
from src.phase_17_extended_markov_retraining import _canonical_targets, _ids, _normalize_team_ids
from src.state_labeling_v1 import StateLabelingConfig, label_rows
from src.phase_10_temporal_target_evaluation import TARGETS

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
PHASE19_WINDOWS = ROOT / "artifacts/phase_19_current_season_windows/event_windows.json"
PHASE19_TARGETS = ROOT / "artifacts/phase_19_current_season_windows/target_labels.json"
SPEC = ROOT / "docs/specs/temporal_targets_v2.md"
OUTPUT = ROOT / "artifacts/phase_20_full_preconfirmation_retraining"


def _load(path: Path) -> Any:
    """Carga JSON local."""

    return json.loads(path.read_text(encoding="utf-8"))


def _window_signature(row: dict[str, Any], mapping: dict[int, int]) -> tuple[str, int, int]:
    """Identifica un partido por kickoff y equipos normalizados."""

    home = int(row["team_id"]) if bool(row["is_home"]) else int(row["opponent_team_id"])
    away = int(row["opponent_team_id"]) if bool(row["is_home"]) else int(row["team_id"])
    return (str(_instant(str(row["match_date"]))), mapping.get(home, home), mapping.get(away, away))


def _remove_canonical_duplicates(canonical_windows: list[dict[str, Any]], canonical_labels: list[dict[str, Any]], extension_windows: list[dict[str, Any]], canonical_targets: list[dict[str, Any]], mapping: dict[int, int]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Excluye copias canónicas de partidos presentes en la extensión posterior."""

    extension_signatures = {_window_signature(row, mapping) for row in extension_windows if int(row["window_index"]) == 0}
    duplicate_ids = {int(row["match_id"]) for row in canonical_windows if int(row["window_index"]) == 0 and _window_signature(row, mapping) in extension_signatures}
    windows = [row for row in canonical_windows if int(row["match_id"]) not in duplicate_ids]
    labels = [row for row in canonical_labels if int(row["match_id"]) not in duplicate_ids]
    targets = [row for row in canonical_targets if int(row["match_id"]) not in duplicate_ids]
    return windows, labels, targets, {"duplicate_match_ids": sorted(duplicate_ids), "excluded_match_count": len(duplicate_ids), "policy": "prefer_later_normalized_extension"}


def _publish(result: dict[str, Any]) -> None:
    """Publica artefactos, reportes y hashes."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name in ("config", "input_manifest", "coverage", "calibration", "confirmation", "audit"):
        (OUTPUT / f"{name}.json").write_text(json.dumps(result[name], indent=2, sort_keys=True, default=str), encoding="utf-8")
    (OUTPUT / "validation_report.md").write_text(result["validation_report"] + "\n", encoding="utf-8")
    (OUTPUT / "final_report.md").write_text(result["final_report"] + "\n", encoding="utf-8")
    hashes = {path.name: _hash_file(path) for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"}
    (OUTPUT / "hashes.json").write_text(json.dumps(hashes, indent=2, sort_keys=True), encoding="utf-8")


def run(config: Phase14Config | None = None) -> dict[str, Any]:
    """Entrena con 900 partidos previos y confirma en 241 partidos posteriores."""

    active = config or Phase14Config(version="full_preconfirmation_retraining_v1")
    mapping, database = _team_mapping(); state_config = StateLabelingConfig()
    cw, cl = _load(CANONICAL_WINDOWS), _load(CANONICAL_LABELS)
    w16, t16 = _load(PHASE16_WINDOWS), _load(PHASE16_TARGETS)
    w19, t19 = _load(PHASE19_WINDOWS), _load(PHASE19_TARGETS)
    w09, t09 = _normalize_team_ids(_load(PHASE09_WINDOWS), mapping), _load(PHASE09_TARGETS)
    w12, t12 = _normalize_team_ids(_load(PHASE12_WINDOWS), mapping), _load(PHASE12_TARGETS)
    l16, l19, l09, l12 = label_rows(w16, state_config), label_rows(w19, state_config), label_rows(w09, state_config), label_rows(w12, state_config)
    canonical_targets = _canonical_targets(t09); old_extension = [row for row in t09 if row.get("cohort") != "canonical_v1"]
    cw, cl, canonical_targets, deduplication = _remove_canonical_duplicates(cw, cl, w09, canonical_targets, mapping)
    ids16, ids19, ids_can, ids09, ids12 = _ids(t16), _ids(t19), _ids(canonical_targets), _ids(old_extension), _ids(t12)
    base_train = ids16 | ids_can | ids19; full_train = base_train | ids09
    base_windows, base_labels = w16 + cw + w19, l16 + cl + l19
    full_windows, full_labels = base_windows + w09, base_labels + l09
    base_history = _match_rows(w16, mapping) + _match_rows(cw, mapping) + _match_rows(w19, mapping)
    full_history = base_history + _match_rows(w09, mapping)
    cal_matches, conf_matches = _match_rows(w09, mapping), _match_rows(w12, mapping)
    cal_priors = {int(row["match_id"]): _dynamic_prior(row, base_history, active) for row in cal_matches}; conf_priors = {int(row["match_id"]): _dynamic_prior(row, full_history, active) for row in conf_matches}
    cal_targets = t16 + canonical_targets + t19 + old_extension; conf_targets = t16 + t09 + t19 + t12
    calibration = _block(base_windows, base_labels, cal_targets, cal_priors, base_train, ids09, active)
    confirmation = _block(full_windows + w12, full_labels + l12, conf_targets, conf_priors, full_train, ids12, active)
    supported = [name for name in TARGETS if confirmation["metrics"][name]["support"]["support_sufficient"]]; confirmed = [name for name in supported if confirmation["bootstrap_results"][name]["improvement_confirmed"]]
    classification = "promising_unconfirmed" if confirmed else "rejected_for_revision"
    audit = {"classification": classification, "database": database, "base_train_matches": len(base_train), "full_train_matches": len(full_train), "confirmation_ids_overlap_train": sorted(full_train & ids12), "calibration_prediction_coverage": len(calibration["predictions"]) == len(ids09), "confirmation_prediction_coverage": len(confirmation["predictions"]) == len(ids12), "team_identity_normalization": "provider_to_internal", "target_outcomes_used_as_features": False, "prior_semantics": "rolling_venue_aware_pre_kickoff", "targets_with_sufficient_support": supported, "targets_with_confirmed_improvement": confirmed, "markets_promoted": False, "deduplication": deduplication}
    manifest = {"canonical_windows_hash": _hash_file(CANONICAL_WINDOWS), "canonical_labels_hash": _hash_file(CANONICAL_LABELS), "phase09_windows_hash": _hash_file(PHASE09_WINDOWS), "phase09_targets_hash": _hash_file(PHASE09_TARGETS), "phase12_windows_hash": _hash_file(PHASE12_WINDOWS), "phase12_targets_hash": _hash_file(PHASE12_TARGETS), "phase16_windows_hash": _hash_file(PHASE16_WINDOWS), "phase16_targets_hash": _hash_file(PHASE16_TARGETS), "phase19_windows_hash": _hash_file(PHASE19_WINDOWS), "phase19_targets_hash": _hash_file(PHASE19_TARGETS), "target_spec_hash": _hash_file(SPEC)}
    coverage = {"phase16_backfill_matches": len(ids16), "canonical_matches": len(ids_can), "phase19_current_season_matches": len(ids19), "phase09_calibration_matches": len(ids09), "phase12_confirmation_matches": len(ids12), "base_train_matches": len(base_train), "full_train_matches": len(full_train), "canonical_duplicate_matches_excluded": deduplication["excluded_match_count"]}
    validation = f"# Validation report — Fase 20\n\n- train base: `{len(base_train)}` partidos\n- calibración: `{len(ids09)}` partidos\n- train final: `{len(full_train)}` partidos\n- confirmación: `{len(ids12)}` partidos\n- duplicados canónicos excluidos: `{deduplication['excluded_match_count']}`\n- targets con soporte: `{supported}`\n- mejoras confirmadas: `{confirmed}`."
    lines = ["# Fase 20 — evaluación final de reentrenamiento", "", f"**Clasificación:** `{classification}`", "", f"- train final: `{len(full_train)}` partidos", f"- confirmación: `{len(ids12)}` partidos", f"- targets con soporte suficiente: `{supported}`"]
    for name in TARGETS:
        item = confirmation["metrics"][name]; ci = confirmation["bootstrap_results"][name]["ci_95"]; lines.append(f"- `{name}`: Markov `{item['model_log_loss']:.6f}`, baseline `{item['baseline_log_loss']:.6f}`, IC mejora `{ci}`")
    lines.extend(["", f"Duplicados canónicos excluidos: `{deduplication['duplicate_match_ids']}`.", "Mercados promovidos: `False`.", "Siguiente paso: regenerar el selector por target y comparar señales sólo contra esta referencia limpia."])
    result = {"config": asdict(active), "input_manifest": manifest, "coverage": coverage, "calibration": calibration, "confirmation": confirmation, "audit": audit, "validation_report": validation, "final_report": "\n".join(lines)}
    _publish(result); LOGGER.info("Fase 20 reentrenamiento final: %s", classification); return result


# Version: 1.0.0
# Created: 2026-07-26
