"""Auditoría de targets temporales y remontadas pre-match.

Version: 1.0.0
Created: 2026-07-26
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
WINDOWS = ROOT / "artifacts/phase_01_event_windows_v1/event_windows.json"
FEATURES = ROOT / "artifacts/phase_2_5_match_features_v1_baseline/match_features_v1_candidate.json"
FOLDS = ROOT / "artifacts/phase_3_8_common_protocol/common_temporal_folds_v1.json"
OUTPUT = ROOT / "artifacts/phase_08_temporal_target_audit"
TARGETS = ("first_half_goal", "second_half_goal", "home_comeback", "away_comeback")


@dataclass(frozen=True, slots=True)
class TargetAuditConfig:
    """Versión y umbrales descriptivos de auditoría, no de promoción."""

    version: str = "temporal_target_audit_v1"
    confirmation_fold_id: int = 3


def _load(path: Path) -> Any:
    """Carga JSON local de una fuente aprobada."""
    return json.loads(path.read_text(encoding="utf-8"))


def _targets() -> tuple[dict[int, dict[str, bool]], dict[int, dict[str, int]]]:
    """Construye targets y oportunidades de remontada por partido."""
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in _load(WINDOWS): grouped[int(row["match_id"])].append(row)
    output, opportunities = {}, {}
    for match_id, rows in grouped.items():
        home = [row for row in rows if bool(row["is_home"])]
        away = [row for row in rows if not bool(row["is_home"])]
        home_by = {int(row["window_index"]): int(row["goals"]) for row in home}
        away_by = {int(row["window_index"]): int(row["goals"]) for row in away}
        home_half = sum(home_by.get(index, 0) for index in range(3))
        away_half = sum(away_by.get(index, 0) for index in range(3))
        home_final, away_final = sum(home_by.values()), sum(away_by.values())
        output[match_id] = {"first_half_goal": home_half + away_half > 0, "second_half_goal": home_final + away_final - home_half - away_half > 0, "home_comeback": home_half < away_half and home_final > away_final, "away_comeback": away_half < home_half and away_final > home_final}
        opportunities[match_id] = {"home_trailing_at_half": int(home_half < away_half), "away_trailing_at_half": int(away_half < home_half)}
    return output, opportunities


def _structural_audit(targets: dict[int, dict[str, bool]], opportunities: dict[int, dict[str, int]]) -> dict[str, Any]:
    """Resume prevalencia global y denominadores reales de remontadas."""
    counts = {target: sum(row[target] for row in targets.values()) for target in TARGETS}
    opportunity_counts = {side: sum(row[side] for row in opportunities.values()) for side in ("home_trailing_at_half", "away_trailing_at_half")}
    conversion = {"home_comeback_given_trailing": counts["home_comeback"] / opportunity_counts["home_trailing_at_half"] if opportunity_counts["home_trailing_at_half"] else 0.0, "away_comeback_given_trailing": counts["away_comeback"] / opportunity_counts["away_trailing_at_half"] if opportunity_counts["away_trailing_at_half"] else 0.0}
    total = len(targets)
    return {"match_count": total, "counts": counts, "rates": {target: counts[target] / total for target in TARGETS}, "opportunity_counts": opportunity_counts, "conversion_rates": conversion}


def _fold_audit(targets: dict[int, dict[str, bool]]) -> dict[str, Any]:
    """Mide prevalencia por fold sin mezclar observaciones entre bloques."""
    folds = _load(FOLDS)["folds"]
    output = {}
    for fold in folds:
        ids = [int(value) for value in fold["validation_ids"]]
        output[str(fold["fold_id"])] = {"match_count": len(ids), "counts": {target: sum(targets[match_id][target] for match_id in ids) for target in TARGETS}}
    return output


def _consistency(targets: dict[int, dict[str, bool]]) -> dict[str, Any]:
    """Comprueba seis ventanas por equipo y consistencia con scores canónicos."""
    windows = _load(WINDOWS); features = {int(row["match_id"]): row for row in _load(FEATURES)["rows"]}
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in windows: grouped[int(row["match_id"])].append(row)
    malformed, score_mismatch = [], []
    for match_id, rows in grouped.items():
        if len(rows) != 12 or {int(row["window_index"]) for row in rows} != set(range(6)): malformed.append(match_id)
        home, away = sum(int(row["goals"]) for row in rows if bool(row["is_home"])), sum(int(row["goals"]) for row in rows if not bool(row["is_home"]))
        feature = features.get(match_id, {})
        if home != int(feature.get("home_goals", -1)) or away != int(feature.get("away_goals", -1)): score_mismatch.append(match_id)
    return {"malformed_matches": malformed, "score_mismatch_matches": score_mismatch, "target_match_identity_count": len(targets)}


def run(config: TargetAuditConfig | None = None) -> dict[str, Any]:
    """Ejecuta auditoría de targets sin generar nuevas predicciones."""
    active = config or TargetAuditConfig(); targets, opportunities = _targets()
    result = {"config": asdict(active), "structural": _structural_audit(targets, opportunities), "by_fold": _fold_audit(targets), "consistency": _consistency(targets), "classification": "ready_for_revision"}
    _publish(result)
    LOGGER.info("Auditoría targets temporales: %s", result["classification"])
    return result


def _publish(result: dict[str, Any]) -> None:
    """Publica auditoría, definición, manifest y hashes reproducibles."""
    OUTPUT.mkdir(parents=True, exist_ok=True)
    payloads = {"config.json": result["config"], "coverage.json": result["structural"], "metrics.json": result["by_fold"], "audit.json": result["consistency"], "input_manifest.json": {"windows_hash": hashlib.sha256(WINDOWS.read_bytes()).hexdigest(), "features_hash": hashlib.sha256(FEATURES.read_bytes()).hexdigest(), "folds_hash": hashlib.sha256(FOLDS.read_bytes()).hexdigest()}}
    for name, value in payloads.items(): (OUTPUT / name).write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    report = ["# Auditoría de targets temporales v1", "", "**Clasificación:** `ready_for_revision`", "", "- primer y segundo tiempo se derivan de ventanas 0–2 y 3–5.", "- remontada exige desventaja al descanso y victoria final del equipo.", f"- tasas globales: `{result['structural']['rates']}`", f"- oportunidades: `{result['structural']['opportunity_counts']}`", "- esta auditoría no habilita mercados."]
    (OUTPUT / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"}
    (OUTPUT / "hashes.json").write_text(json.dumps(hashes, indent=2, sort_keys=True), encoding="utf-8")


# Version: 1.0.0
# Created: 2026-07-26
