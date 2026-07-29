"""Evalúa el estado predictivo congelado sobre la nueva cohorte disponible.

Requirements:
    numpy>=2.0
    SQLAlchemy==2.0.41
    psycopg2-binary==2.9.10

Version: 1.0.0
Created: 2026-07-27
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_phase_38_multileague_event_windows import (  # noqa: E402
    _normalize_events,
    _normalize_matches,
)
from scripts.run_phase_76_latent_state_discovery import (  # noqa: E402
    _attach_temporal_context,
    _record,
)
from src.causal_sequence_corpus import (  # noqa: E402
    SequenceResolution,
    build_resolution,
    score_reconciles,
)
from src.domain_robust_states import rolling_domain_features  # noqa: E402
from src.espn_event_reconciliation import reconcile_staging_events  # noqa: E402
from src.latent_state_discovery import (  # noqa: E402
    duration_nll,
    league_order_stability,
    next_goal_risk,
    occupancy,
)

COLLECTION = ROOT / "artifacts/phase_76_sealed_holdout"
MODEL = ROOT / "artifacts/phase_76_domain_robust_reaudit"
OUTPUT = ROOT / "artifacts/phase_76_independent_cohort_evaluation"
SCHEMA = "prospective_staging_v2"
LOGGER = logging.getLogger(__name__)


def _database_url() -> str:
    """Obtiene DATABASE_URL sin exponerlo."""

    value = os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("missing_database_url")
    return value


def _ids() -> list[str]:
    """Carga la identidad sellada de la cohorte."""

    rows = json.loads((COLLECTION / "references.json").read_text())
    return [str(row["provider_match_id"]) for row in rows]


def _source() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Lee partidos y eventos exclusivamente con SELECT."""

    engine = create_engine(_database_url(), future=True, pool_pre_ping=True)
    matches_query = text(
        f"SELECT provider_match_id, league_slug, competition_id, kickoff_ts, "
        f"home_provider_team_id, away_provider_team_id, home_score, away_score "
        f"FROM {SCHEMA}.matches WHERE provider='espn' "
        f"AND provider_match_id = ANY(:ids) ORDER BY kickoff_ts"
    )
    events_query = text(
        f"SELECT provider_match_id, event_index, minute, second, "
        f"team_provider_id, event_type, event_type_raw, "
        f"raw_data->>'text' event_text, annulled FROM {SCHEMA}.events "
        f"WHERE provider='espn' AND provider_match_id = ANY(:ids) "
        f"ORDER BY provider_match_id, minute, second, event_index"
    )
    try:
        with engine.connect() as connection:
            matches = [dict(row) for row in connection.execute(
                matches_query, {"ids": _ids()}).mappings()]
            events = [dict(row) for row in connection.execute(
                events_query, {"ids": _ids()}).mappings()]
            return matches, events
    finally:
        engine.dispose()


def _windows() -> tuple[list[dict[str, Any]], list[int]]:
    """Materializa 5 minutos y excluye cualquier marcador inconsistente."""

    raw_matches, raw_events = _source()
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in raw_events:
        grouped[int(row["provider_match_id"])].append(row)
    output, rejected = [], []
    for raw in raw_matches:
        match = {**_normalize_matches([raw])[0],
                 "league_slug": str(raw["league_slug"])}
        reconciled, _ = reconcile_staging_events(
            grouped.get(int(match["match_id"]), []),
            int(match["home_score"]),
            int(match["away_score"]),
            int(match["home_team_id"]),
            int(match["away_team_id"]),
        )
        rows = build_resolution(
            match, _normalize_events(reconciled),
            SequenceResolution(5),
        )
        if score_reconciles(rows, match["home_score"], match["away_score"]):
            output.extend(rows)
        else:
            rejected.append(int(match["match_id"]))
    return output, rejected


def _records(windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convierte ventanas a secuencias direccionales sin labels en features."""

    records = [_record({**row, "split": "independent"})
               for row in windows]
    records.sort(key=lambda row: (row["match_date"], row["match_id"],
                                  row["team_id"], row["window_index"]))
    _attach_temporal_context(records)
    return records


def _parameters() -> dict[str, Any]:
    """Carga exclusivamente parámetros congelados."""

    return json.loads((MODEL / "model_parameters.json").read_text())


def _infer(
    records: list[dict[str, Any]],
    parameters: dict[str, Any],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Calcula riesgo y estado sin reentrenamiento."""

    features = np.asarray([row["features"] for row in records], dtype=float)
    sequence_ids = np.asarray(
        [row["sequence_id"] for row in records], dtype=np.int64
    )
    selected = _transform(features, sequence_ids, parameters)
    mean = np.asarray(parameters["scaler_mean"])
    scale = np.asarray(parameters["scaler_scale"])
    coefficients = np.asarray(parameters["coefficients"])
    logits = ((selected - mean) / scale) @ coefficients
    logits += float(parameters["intercept"])
    risks = 1.0 / (1.0 + np.exp(-logits))
    states = np.digitize(risks, np.asarray(parameters["boundaries"]))
    targets = np.asarray([row["next_goal"] for row in records], dtype=float)
    return risks, states, targets


def _transform(
    features: np.ndarray,
    sequence_ids: np.ndarray,
    parameters: dict[str, Any],
) -> np.ndarray:
    """Aplica exactamente la transformación congelada del candidato."""

    transform = parameters.get("feature_transform")
    if transform == "rolling_domain_features_v1":
        return rolling_domain_features(features, sequence_ids)
    columns = parameters.get("feature_columns")
    if columns is None:
        raise ValueError("missing_feature_transform_contract")
    return features[:, columns]


def _metrics(
    records: list[dict[str, Any]],
    states: np.ndarray,
    targets: np.ndarray,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    """Evalúa semántica y duración con parámetros congelados."""

    state_count = len(parameters["boundaries"]) + 1
    risks, support = next_goal_risk(states, targets, state_count)
    sequence_ids = np.asarray([row["sequence_id"] for row in records],
                              dtype=np.int64)
    explicit = np.asarray(parameters["duration_explicit"])
    geometric = np.asarray(parameters["duration_geometric"])
    leagues = np.asarray([row["league_slug"] for row in records], dtype=str)
    return {"risk": risks.tolist(), "support": support.tolist(),
            "spread": float(np.ptp(risks)),
            "occupancy": occupancy(states, state_count),
            "league_order": league_order_stability(
                leagues, states, targets, risks),
            "duration": {"explicit_nll": duration_nll(
                            explicit, sequence_ids, states),
                         "geometric_nll": duration_nll(
                            geometric, sequence_ids, states),
                         "improvement": duration_nll(
                            geometric, sequence_ids, states)
                         - duration_nll(explicit, sequence_ids, states)}}


def _assignments(
    records: list[dict[str, Any]],
    risks: np.ndarray,
    states: np.ndarray,
) -> list[dict[str, Any]]:
    """Serializa inferencia sin incorporar outcomes."""

    return [{"match_id": row["match_id"], "team_id": row["team_id"],
             "window_index": row["window_index"], "state": int(state),
             "risk_score": float(risk)}
            for row, risk, state in zip(records, risks, states)]


def _write(name: str, value: Any) -> None:
    """Publica JSON estable."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / name).write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )


def _write_jsonl(name: str, rows: list[dict[str, Any]]) -> None:
    """Publica asignaciones de forma atómica."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT / f"{name}.tmp"
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    temporary.replace(OUTPUT / name)


def _hashes() -> dict[str, str]:
    """Calcula hashes SHA-256."""

    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(OUTPUT.iterdir())
            if path.is_file() and path.name != "hashes.json"}


def run() -> dict[str, Any]:
    """Materializa y evalúa la cohorte sin modificar el candidato."""

    windows, rejected = _windows()
    records, parameters = _records(windows), _parameters()
    risks, states, targets = _infer(records, parameters)
    metrics = _metrics(records, states, targets, parameters)
    matches = len({row["match_id"] for row in records})
    leagues = len({row["league_slug"] for row in records})
    classification = _classification(metrics, matches, leagues, rejected)
    result = {"classification": classification,
              "coverage": {"matches": matches, "leagues": leagues,
                           "windows": len(windows), "directional_rows": len(records),
                           "minimum_required_matches": 200,
                           "minimum_required_leagues": 10},
              "audit": {"model_refit": False, "thresholds_changed": False,
                        "score_mismatch_ids": rejected, "postgres_select_only": True,
                        "router_modified": False},
              "metrics": metrics,
              "assignments": _assignments(records, risks, states)}
    _publish(result)
    return result


def _classification(
    metrics: dict[str, Any],
    matches: int,
    leagues: int,
    rejected: list[int],
) -> str:
    """Aplica los gates congelados de Fase 76."""

    occupancy_values = list(metrics["occupancy"].values())
    league_order = metrics["league_order"]
    admitted = int(league_order["admitted"])
    stable = int(league_order["stable"])
    coverage_ok = matches >= 200 and leagues >= 10
    semantic_ok = metrics["spread"] >= 0.05
    occupancy_ok = bool(occupancy_values) and min(occupancy_values) >= 0.05
    league_ok = admitted > 0 and stable / admitted >= 0.75
    duration_ok = metrics["duration"]["improvement"] > 0.0
    integrity_ok = len(rejected) / max(matches + len(rejected), 1) <= 0.02
    return ("ready_for_phase_77" if all((
        coverage_ok, semantic_ok, occupancy_ok, league_ok,
        duration_ok, integrity_ok,
    )) else "rejected_for_revision")


def _publish(result: dict[str, Any]) -> None:
    """Publica evidencia y clasificación controlada."""

    _write_jsonl("state_assignments.jsonl", result.pop("assignments"))
    for name in ("coverage", "audit", "metrics"):
        _write(f"{name}.json", result[name])
    _write("input_manifest.json", {
        "collection_hash": hashlib.sha256(
            (COLLECTION / "references.json").read_bytes()).hexdigest(),
        "model_parameters_hash": hashlib.sha256(
            (MODEL / "model_parameters.json").read_bytes()).hexdigest(),
    })
    _write("config.json", {"model": "predictive_latent_state_v3",
                           "model_hash": hashlib.sha256(
                               (MODEL / "model_parameters.json").read_bytes()
                           ).hexdigest()})
    report = (
        "# Evaluación cohorte independiente Fase 76\n\n"
        f"**Clasificación:** `{result['classification']}`\n\n"
        f"- partidos: `{result['coverage']['matches']}`\n"
        f"- ligas: `{result['coverage']['leagues']}`\n"
        f"- spread: `{result['metrics']['spread']:.6f}`\n"
        f"- mejora duración: `{result['metrics']['duration']['improvement']:.6f}`\n"
        "- reentrenamiento: `False`\n"
    )
    (OUTPUT / "final_report.md").write_text(report, encoding="utf-8")
    (OUTPUT / "validation_report.md").write_text(report, encoding="utf-8")
    _write("hashes.json", _hashes())
    LOGGER.info("Evaluación independiente: %s", result["classification"])


def main() -> int:
    """Ejecuta evaluación y exige aprobación completa."""

    return 0 if run()["classification"] == "ready_for_phase_77" else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0 - 2026-07-27
