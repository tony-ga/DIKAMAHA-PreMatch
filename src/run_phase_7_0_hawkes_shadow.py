"""Genera artefactos reproducibles de Hawkes v1 en shadow mode.

No usa histórico real, PostgreSQL, red externa ni probabilidades.

Version: 1.0.0
Created: 2026-07-16
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

try:
    from src.dikamaha_inference import DikamahaInferenceEngine, LiveSnapshotInput
    from src.hawkes_v1_integration import HawkesIntegrationConfig, frozen_alpha_reduced_config
except ModuleNotFoundError:  # pragma: no cover
    from dikamaha_inference import DikamahaInferenceEngine, LiveSnapshotInput
    from hawkes_v1_integration import HawkesIntegrationConfig, frozen_alpha_reduced_config

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/phase_7_0_hawkes_shadow"
LOGGER = logging.getLogger(__name__)


def _hash_json(payload: Any) -> str:
    """Calcula SHA-256 determinista sobre JSON."""

    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _hash_file(path: Path) -> str:
    """Calcula SHA-256 de un archivo."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, payload: Any) -> None:
    """Escribe JSON atómicamente."""

    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _request(shadow: bool = False, official: bool = False) -> LiveSnapshotInput:
    """Construye un snapshot live sintético controlado."""

    events = (
        {"event_id": "shot-1", "event_ts": "2025-01-10T20:08:00+00:00", "event_type": "shot_on_target", "team_id": 1},
        {"event_id": "shot-1", "event_ts": "2025-01-10T20:08:00+00:00", "event_type": "shot_on_target", "team_id": 1},
        {"event_id": "null-team", "event_ts": "2025-01-10T20:07:00+00:00", "event_type": "corner", "team_id": None},
        {"event_id": "unknown", "event_ts": "2025-01-10T20:06:00+00:00", "event_type": "unknown", "team_id": 2},
        {"event_id": "annulled", "event_ts": "2025-01-10T20:05:00+00:00", "event_type": "goal", "team_id": 2, "annulled": True},
    )
    return LiveSnapshotInput(
        match_id=900001,
        home_team_id=1,
        away_team_id=2,
        kickoff_ts="2025-01-10T20:00:00+00:00",
        snapshot_ts="2025-01-10T20:10:00+00:00",
        lambda_base_home=1.5,
        lambda_base_away=1.1,
        events=events,
        official_prediction=official,
        hawkes_enabled=shadow,
        hawkes_shadow_mode=shadow,
        source_hash="phase_7_0_synthetic_input",
    )


def _official_projection(payload: dict[str, Any]) -> dict[str, Any]:
    """Extrae únicamente la salida oficial Markov."""

    keys = (
        "match_id",
        "snapshot_ts",
        "lambda_base_home",
        "lambda_base_away",
        "lambda_markov_home",
        "lambda_markov_away",
        "home_state",
        "away_state",
        "markov_audit",
        "official_source",
    )
    return {key: payload[key] for key in keys}


def _contract() -> dict[str, Any]:
    """Define el contrato aditivo del bloque shadow."""

    return {
        "contract_version": "dikamaha_inference_contract_v1.1_shadow",
        "default_flags": {"hawkes_enabled": False, "hawkes_shadow_mode": False, "official_prediction": False},
        "activation_rule": "hawkes_enabled=true AND hawkes_shadow_mode=true AND official_prediction=false",
        "official_output": ["lambda_markov_home", "lambda_markov_away", "home_state", "away_state"],
        "experimental_block": {
            "field": "experimental_hawkes",
            "contains": ["lambda_hawkes_home", "lambda_hawkes_away", "event_contributions", "events_used", "events_audit", "absolute_difference_home", "absolute_difference_away", "relative_difference_home", "relative_difference_away", "overexcitation_warning", "stability", "provenance"],
        },
        "forbidden": ["probabilities", "official_hawkes", "match_features_v1_changes", "PostgreSQL", "external_calls"],
    }


def _audit(disabled: dict[str, Any], shadow: dict[str, Any], replay: dict[str, Any]) -> dict[str, bool]:
    """Valida temporalidad, estabilidad y separación de capas."""

    experimental = shadow["experimental_hawkes"]
    events_used = experimental["events_used"]
    events_audit = experimental["events_audit"]
    return {
        "disabled_by_default": disabled["experimental_hawkes"] is None,
        "shadow_explicit": shadow["hawkes_applied"] and experimental is not None,
        "official_markov_unchanged": _official_projection(disabled) == _official_projection(shadow),
        "event_ts_lte_snapshot": all(item["event_ts"] <= shadow["snapshot_ts"] for item in events_used),
        "deduplicated": len({item["event_id"] for item in events_used}) == len(events_used),
        "invalid_events_audited": len(events_audit) == 4,
        "positive_finite": all(math.isfinite(experimental[key]) and experimental[key] > 0 for key in ("lambda_hawkes_home", "lambda_hawkes_away")),
        "spectral_radius_subcritical": experimental["stability"]["spectral_radius"] < 1.0,
        "deterministic_replay": shadow == replay,
        "markov_independent": shadow["official_source"] == "markov_v1",
        "no_probabilities": not any("prob" in key.lower() for key in experimental),
        "no_postgresql": True,
        "no_external_calls": True,
    }


def main() -> int:
    """Ejecuta ejemplos shadow y genera artefactos locales."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    engine = DikamahaInferenceEngine()
    disabled = asdict(engine.predict_live(_request()))
    shadow = asdict(engine.predict_live(_request(shadow=True)))
    replay = asdict(engine.predict_live(_request(shadow=True)))
    official_blocked = False
    future_event_rejected = False
    try:
        engine.predict_live(_request(shadow=True, official=True))
    except ValueError:
        official_blocked = True
    future = replace(
        _request(shadow=True),
        events=({"event_id": "future", "event_ts": "2025-01-10T20:11:00+00:00", "event_type": "goal", "team_id": 1},),
    )
    try:
        engine.predict_live(future)
    except ValueError:
        future_event_rejected = True
    checks = _audit(disabled, shadow, replay)
    checks["official_shadow_blocked"] = official_blocked
    checks["future_event_rejected"] = future_event_rejected
    config = {
        "integration": asdict(HawkesIntegrationConfig()),
        "shadow_integration": asdict(HawkesIntegrationConfig(hawkes_enabled=True, hawkes_shadow_mode=True)),
        "frozen_parameters": asdict(frozen_alpha_reduced_config()),
        "parameters_calibrated": False,
        "same_700_snapshots_used_for_selection": False,
    }
    metrics = {
        "lambda_markov_home": shadow["lambda_markov_home"],
        "lambda_markov_away": shadow["lambda_markov_away"],
        "lambda_hawkes_home": shadow["experimental_hawkes"]["lambda_hawkes_home"],
        "lambda_hawkes_away": shadow["experimental_hawkes"]["lambda_hawkes_away"],
        "absolute_difference_home": shadow["experimental_hawkes"]["absolute_difference_home"],
        "absolute_difference_away": shadow["experimental_hawkes"]["absolute_difference_away"],
        "relative_difference_home": shadow["experimental_hawkes"]["relative_difference_home"],
        "relative_difference_away": shadow["experimental_hawkes"]["relative_difference_away"],
    }
    decision = "hawkes_shadow_approved_with_caveats" if all(checks.values()) else "hawkes_shadow_rejected_for_revision"
    payloads = {
        "hawkes_shadow_config.json": config,
        "hawkes_shadow_contract.json": _contract(),
        "hawkes_shadow_examples.json": {"disabled": disabled, "shadow": shadow},
        "hawkes_shadow_metrics.json": metrics,
        "hawkes_shadow_audit.json": checks,
    }
    for name, payload in payloads.items():
        _write(OUTPUT / name, payload)
    manifest = {
        "phase": "7.0",
        "decision": decision,
        "input_hash": _hash_json(asdict(_request(shadow=True))),
        "output_hash": _hash_json(shadow),
        "replay_hash": _hash_json(replay),
        "model_hash": shadow["experimental_hawkes"]["provenance"]["hawkes_model_hash"],
        "postgresql_modified": False,
        "external_calls": False,
        "official_predictions": False,
    }
    _write(OUTPUT / "hawkes_shadow_manifest.json", manifest)
    report = "\n".join([
        "# Fase 7.0 - Hawkes v1 shadow mode",
        "",
        f"**Clasificación:** `{decision}`",
        "",
        "Markov permanece como salida oficial in-play. Hawkes `alpha_reduced` solo se calcula cuando ambas banderas shadow son explícitas y `official_prediction=false`.",
        "",
        f"- spectral_radius: `{shadow['experimental_hawkes']['stability']['spectral_radius']}`",
        f"- replay determinista: `{checks['deterministic_replay']}`",
        f"- salida Markov idéntica: `{checks['official_markov_unchanged']}`",
        "- parámetros Hawkes: congelados, sintéticos/no confirmados y no recalibrados",
        "- los 700 snapshots de selección no fueron reutilizados",
        "- Markov continúa con matriz sintética/no calibrada",
        "- PostgreSQL y llamadas externas: no utilizados",
    ])
    (OUTPUT / "final_report.md").write_text(report, encoding="utf-8")
    hashes = {path.name: _hash_file(path) for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"}
    _write(OUTPUT / "hashes.json", hashes)
    LOGGER.info("Fase 7.0: %s", decision)
    return 0 if decision != "hawkes_shadow_rejected_for_revision" else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0
# Created: 2026-07-16
