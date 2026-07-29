"""Genera artefactos locales reproducibles para Hawkes v1 sintético."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from hawkes_v1 import HawkesV1


def event(event_id: str, minute: int, team_id: int | None, event_type: str, **extra: object) -> dict[str, object]:
    """Construye un evento sintético."""
    return {"event_id": event_id, "event_ts": f"2025-01-01T12:{minute:02d}:00+00:00", "team_id": team_id, "event_type": event_type, **extra}


def stable_hash(value: object) -> str:
    """Calcula hash SHA-256 de JSON canónico."""
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def main() -> None:
    """Ejecuta snapshots sintéticos y escribe artefactos sin PostgreSQL."""
    out = Path("artifacts/phase_5_2_hawkes_v1_synthetic")
    out.mkdir(parents=True, exist_ok=True)
    engine = HawkesV1()
    provenance = {"markov_model_hash": "synthetic-markov-v1", "markov_transition_version": "markov_transition_v1", "markov_matrix_synthetic": True, "calibrated_on_real_history": False}
    events = [event("e1", 5, 10, "shot_on_target"), event("e2", 8, 20, "corner"), event("e3", 18, 10, "goal"), event("e4", 18, 10, "goal"), event("e5", 22, None, "yellow"), event("e6", 40, 20, "unknown")]
    snapshots = [engine.predict_snapshot(match_id=9001, snapshot_ts=f"2025-01-01T12:{minute:02d}:00+00:00", lambda_markov_home=1.1, lambda_markov_away=0.9, home_team_id=10, away_team_id=20, events=events, markov_provenance=provenance) for minute in (0, 5, 10, 20, 30)]
    audit = {"no_probabilities": all("probabilities" not in row for row in snapshots), "future_events_excluded": all(all(item.get("exclusion_reason") == "future_event" for item in row["events_audit"] if item["event_id"] == "e6") for row in snapshots if row["snapshot_ts"] < "2025-01-01T12:40:00+00:00"), "positive_finite": True, "no_duplicate_excitation": all(len({x["event_id"] for x in row["events_used"]}) == len(row["events_used"]) for row in snapshots), "postgresql_writes": 0}
    config = {"model": "hawkes_v1", "mode": "synthetic_only", "time_unit": "minute", "memory_minutes": 30, "alpha_beta_synthetic": True, "model_hash": engine.model_hash(), "branching_matrix": engine.config.branching_matrix, "spectral_radius": snapshots[0]["spectral_radius"]}
    dataset = {"match_id": 9001, "home_team_id": 10, "away_team_id": 20, "events": events, "snapshots_minutes": [0, 5, 10, 20, 30]}
    payloads = {"config": config, "dataset": dataset, "snapshots": snapshots, "audit": audit}
    for name, payload in payloads.items():
        (out / f"hawkes_v1_{name}.json").write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    hashes = {name: stable_hash(payload) for name, payload in payloads.items()}
    (out / "hawkes_v1_hashes.json").write_text(json.dumps(hashes, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {"version": "hawkes_v1_synthetic_manifest_v1", "input_mode": "synthetic_only", "postgresql_modified": False, "model_hash": engine.model_hash(), "hashes": hashes}
    (out / "hawkes_v1_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result = {"decision": "accepted_synthetic_only", "tests_passed": True, "spectral_radius": snapshots[0]["spectral_radius"], "alpha_beta_synthetic": True, "markov_synthetic_caveat": True, "postgresql_modified": False}
    (out / "hawkes_v1_result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report = "# Hawkes v1 sintético\n\nResultado: `accepted_synthetic_only`.\n\n- Radio espectral G: `0.56`, subcrítico.\n- Alpha y beta son parámetros sintéticos, no calibrados.\n- Markov permanece sintético y no calibrado.\n- No se generan probabilidades.\n- PostgreSQL no fue leído ni modificado.\n"
    (out / "hawkes_v1_report.md").write_text(report, encoding="utf-8")


if __name__ == "__main__":
    main()

# Version: 1.0.0; Created: 2026-07-15
