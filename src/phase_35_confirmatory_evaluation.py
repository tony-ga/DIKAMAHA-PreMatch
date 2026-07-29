"""Evalúa fuera de muestra las predicciones prospectivas ya congeladas.

Los scores y eventos de staging sólo se leen después de que Fase 34 publica
predicciones. Se usan como targets de scoring y nunca como features.

Requirements:
    - numpy
    - SQLAlchemy==2.0.41
    - psycopg2-binary==2.9.10
    - python-dotenv

Version: 1.0.0
Created: 2026-07-26
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from pathlib import Path
from typing import Any

import numpy as np
from dotenv import load_dotenv

from src.event_windows_v1 import EventWindowsConfig, build_windows
from src.phase_09_historical_target_revision import _normalize_events, _normalize_matches, derive_targets
from src.phase_10_temporal_target_evaluation import DIAGNOSTIC_TARGETS, TARGETS
from src.postgres_readonly_staging import ReadonlyDatabase, counts_identical

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
PREDICTIONS = ROOT / "artifacts/phase_34_prematch_prediction_package/predictions.json"
GATE = ROOT / "artifacts/phase_31_prospective_cohort_gate/gate_result.json"
OUTPUT = ROOT / "artifacts/phase_35_confirmatory_evaluation"
ALL_TARGETS = TARGETS + DIAGNOSTIC_TARGETS


def _load(path: Path) -> Any:
    """Carga un JSON local."""

    return json.loads(path.read_text(encoding="utf-8"))


def _hash(path: Path) -> str:
    """Calcula SHA-256 de un artefacto."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, payload: Any) -> None:
    """Escribe un JSON con reemplazo atómico."""

    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temporary.replace(path)


def _database_url() -> str:
    """Obtiene DATABASE_URL sin comillas externas."""

    load_dotenv(ROOT / ".env")
    value = os.getenv("DATABASE_URL", "").strip().strip("\"'")
    if not value: raise ValueError("missing_database_url")
    return value


def _read_staging(candidate_ids: set[int]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Lee partidos y eventos de staging mediante SELECT-only."""

    database = ReadonlyDatabase(_database_url())
    with database.session() as session:
        before = {"matches": int(session.scalar("SELECT COUNT(*) FROM prospective_staging_v2.matches WHERE provider='espn'")), "events": int(session.scalar("SELECT COUNT(*) FROM prospective_staging_v2.events WHERE provider='espn'"))}
        matches = session.rows("SELECT provider_match_id::bigint AS match_id, kickoff_ts, home_provider_team_id, away_provider_team_id, home_score, away_score FROM prospective_staging_v2.matches WHERE provider='espn'")
        events = session.rows("SELECT provider_match_id::bigint AS match_id, event_index, minute, second, team_provider_id, event_type, annulled FROM prospective_staging_v2.events WHERE provider='espn'")
        after = {"matches": int(session.scalar("SELECT COUNT(*) FROM prospective_staging_v2.matches WHERE provider='espn'")), "events": int(session.scalar("SELECT COUNT(*) FROM prospective_staging_v2.events WHERE provider='espn'"))}
    selected_matches = [row for row in matches if int(row["match_id"]) in candidate_ids]; selected_events = [row for row in events if int(row["match_id"]) in candidate_ids]
    audit = {"before": before, "after": after, "counts_identical": counts_identical(before, after), "select_only": all(statement.startswith("SELECT ") for statement in database.statements), "write_statements": 0, "connection_closed": database.closed, "statements": database.statements}
    return selected_matches, selected_events, audit


def _targets(matches: list[dict[str, Any]], events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Construye targets temporales sólo después del paquete pre-match."""

    normalized_matches, normalized_events = _normalize_matches(matches), _normalize_events(events)
    windows, temporal = build_windows(normalized_matches, normalized_events, EventWindowsConfig())
    return derive_targets(windows, "independent_confirmatory"), {"window_count": len(windows), "event_count": len(normalized_events), "temporal": temporal}


def _loss(probability: float, actual: bool) -> float:
    """Calcula log-loss binario con clipping."""

    value = min(max(float(probability), 1e-12), 1.0 - 1e-12)
    return -math.log(value if actual else 1.0 - value)


def _bootstrap(values: list[float], samples: int = 5000, seed: int = 20260726) -> dict[str, Any]:
    """Calcula IC bootstrap agrupado por partido."""

    if not values: return {"match_count": 0, "mean_improvement": None, "ci_95": [None, None], "improvement_confirmed": False}
    rng = np.random.default_rng(seed); indices = rng.integers(0, len(values), size=(samples, len(values))); means = np.asarray(values)[indices].mean(axis=1); ci = np.quantile(means, [0.025, 0.975]).tolist()
    return {"match_count": len(values), "mean_improvement": float(np.mean(values)), "ci_95": [float(ci[0]), float(ci[1])], "improvement_confirmed": bool(ci[0] > 0.0)}


def _opportunities(targets: list[dict[str, Any]], name: str) -> int:
    """Cuenta oportunidades condicionales de cada target."""

    key = {"home_recovery_draw_or_win": "home_trailing_at_half", "away_recovery_draw_or_win": "away_trailing_at_half", "home_reaches_level_after_half": "home_trailing_at_half", "away_reaches_level_after_half": "away_trailing_at_half", "home_comeback_win": "home_trailing_at_half", "away_comeback_win": "away_trailing_at_half"}.get(name)
    return len(targets) if key is None else sum(bool(row[key]) for row in targets)


def _score(predictions: list[dict[str, Any]], targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Alinea probabilidades pre-match con outcomes post-match."""

    index = {int(row["match_id"]): row for row in targets}; scored = []
    for prediction in predictions:
        target = index[int(prediction["match_id"])]
        row = {"match_id": int(prediction["match_id"]), "cutoff_ts": prediction["cutoff_ts"]}
        for name in ALL_TARGETS:
            actual = bool(target[name]); probability = float(prediction[f"routed_probability_{name}"]); baseline = float(prediction[f"baseline_{name}"])
            row.update({f"target_{name}": actual, f"routed_probability_{name}": probability, f"baseline_{name}": baseline, f"loss_{name}": _loss(probability, actual), f"baseline_loss_{name}": _loss(baseline, actual)})
        scored.append(row)
    return scored


def _metrics(scored: list[dict[str, Any]], targets: list[dict[str, Any]]) -> dict[str, Any]:
    """Resume desempeño, soporte y bootstrap por target."""

    output = {}
    for index, name in enumerate(ALL_TARGETS):
        values = [float(row[f"baseline_loss_{name}"]) - float(row[f"loss_{name}"]) for row in scored]; positives = sum(bool(row[f"target_{name}"]) for row in scored); opportunities = _opportunities(targets, name)
        output[name] = {"match_count": len(scored), "positive_events": positives, "opportunities": opportunities, "support_sufficient": len(scored) >= 30 and positives >= 20 and opportunities >= 30, "routed_log_loss": sum(float(row[f"loss_{name}"]) for row in scored) / len(scored) if scored else None, "baseline_log_loss": sum(float(row[f"baseline_loss_{name}"]) for row in scored) / len(scored) if scored else None, "improvement_vs_baseline": sum(values) / len(values) if values else None, "bootstrap": _bootstrap(values, seed=20260726 + index)}
    return output


def _publish(result: dict[str, Any]) -> None:
    """Publica scoring, métricas, auditoría y hashes."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name in ("config", "input_manifest", "coverage", "scored_predictions", "metrics", "audit", "evaluation_result"): _write(OUTPUT / f"{name}.json", result[name])
    report = ["# Fase 35 — evaluación confirmatoria independiente", "", f"**Clasificación:** `{result['classification']}`", "", f"- predicciones recibidas: `{result['coverage']['predictions']}`", f"- targets recuperados: `{result['coverage']['targets']}`", f"- scoring ejecutado: `{result['coverage']['scoring_executed']}`", "- targets usados como features: `False`", "- router modificado: `False`", "- mercados promovidos: `False`"]
    (OUTPUT / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    _write(OUTPUT / "hashes.json", {path.name: _hash(path) for path in sorted(OUTPUT.iterdir()) if path.name != "hashes.json"})


def run() -> dict[str, Any]:
    """Ejecuta scoring sólo cuando existen predicciones prospectivas."""

    predictions = _load(PREDICTIONS); config = {"version": "phase_35_confirmatory_evaluation_v1", "minimum_matches": 30, "minimum_positive_events": 20, "minimum_opportunities": 30, "bootstrap_samples": 5000}
    if not predictions:
        result = {"classification": "waiting_for_prematch_predictions", "config": config, "input_manifest": {"predictions_hash": _hash(PREDICTIONS), "gate_hash": _hash(GATE)}, "coverage": {"predictions": 0, "targets": 0, "scoring_executed": False}, "scored_predictions": [], "metrics": {}, "audit": {"predictions_generated_before_targets": True, "target_outcomes_read": False, "target_outcomes_used_as_features": False, "losses_calculated": False, "router_modified": False, "markets_promoted": False}, "evaluation_result": {"classification": "waiting_for_prematch_predictions"}}
        _publish(result); return result
    candidate_ids = {int(row["match_id"]) for row in predictions}; matches, events, database = _read_staging(candidate_ids); targets, target_audit = _targets(matches, events); scored = _score(predictions, targets); metrics = _metrics(scored, targets); complete = len(scored) == len(predictions) == len(targets) and database["select_only"] and database["counts_identical"]; classification = "confirmatory_evaluation_complete" if complete and len(scored) >= 30 else "confirmatory_evaluation_insufficient_support" if complete else "confirmatory_evaluation_rejected_for_revision"; audit = {"predictions_generated_before_targets": True, "target_outcomes_read": True, "target_outcomes_used_as_features": False, "losses_calculated": True, "database": database, "target_audit": target_audit, "router_modified": False, "markets_promoted": False}
    result = {"classification": classification, "config": config, "input_manifest": {"predictions_hash": _hash(PREDICTIONS), "gate_hash": _hash(GATE)}, "coverage": {"predictions": len(predictions), "targets": len(targets), "scored_predictions": len(scored), "scoring_executed": True}, "scored_predictions": scored, "metrics": metrics, "audit": audit, "evaluation_result": {"classification": classification, "promotion_allowed": False}}
    _publish(result); LOGGER.info("Fase 35 evaluación confirmatoria: %s", classification); return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()

# Version: 1.0.0
# Created: 2026-07-26
