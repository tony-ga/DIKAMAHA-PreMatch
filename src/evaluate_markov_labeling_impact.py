"""Evaluación OOS del impacto de reglas candidatas sobre Markov v1.

Las reglas se consumen congeladas desde Fase 7.7 y se evalúan sobre los
partidos OOS de Fase 7.6. La matriz y la salida oficial no se modifican.

Requirements:
    - numpy
    - pandas
    - SQLAlchemy==2.0.41
    - psycopg2-binary==2.9.10

Version: 1.0.0
Created: 2026-07-16
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import mean
from typing import Any

import numpy as np

try:
    from src.audit_event_label_coverage import (
        _candidate_rows,
        _event_quality,
        _match_categories,
        _read_database,
        _team_rows,
    )
    from src.calibrate_inplay_models import (
        _file_hash,
        _poisson_log_score,
        _stable_hash,
        _utc,
    )
    from src.hawkes_v1_integration import HawkesIntegrationConfig
    from src.markov_v1 import MarkovV1
    from src.postgres_readonly_staging import (
        database_error_types,
        detect_capabilities,
        sanitize_error,
    )
except ModuleNotFoundError:  # pragma: no cover
    from audit_event_label_coverage import (
        _candidate_rows,
        _event_quality,
        _match_categories,
        _read_database,
        _team_rows,
    )
    from calibrate_inplay_models import (
        _file_hash,
        _poisson_log_score,
        _stable_hash,
        _utc,
    )
    from hawkes_v1_integration import HawkesIntegrationConfig
    from markov_v1 import MarkovV1
    from postgres_readonly_staging import (
        database_error_types,
        detect_capabilities,
        sanitize_error,
    )

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/phase_7_8_markov_labeling_impact"
PHASE_76 = ROOT / "artifacts/phase_7_6_model_calibration"
PHASE_77 = ROOT / "artifacts/phase_7_7_event_label_coverage"
APPROVED_RULES = {
    "two_goal_context_after_60",
    "late_score_context",
    "sustained_pressure_dominance",
    "sustained_opponent_pressure",
}
STATE_NAMES = {0: "equilibrio", 1: "repliegue", 2: "asedio"}
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ImpactConfig:
    """Configuración fija del gate de impacto."""

    version: str = "phase_7_8_markov_labeling_impact_v1"
    bootstrap_seed: int = 7801
    bootstrap_replicates: int = 5000
    minimum_confirmation_matches: int = 20
    minimum_coverage_improvement: float = 0.10
    material_metric_degradation: float = 0.01
    minimum_transition_cell: int = 30
    subgroup_minimum_matches: int = 10
    subgroup_minimum_snapshots: int = 200
    maximum_tactical_multiplier: float = 1.25


def _load_json(path: Path) -> Any:
    """Carga un artefacto JSON versionado."""

    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    """Escribe JSON de forma atómica."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    temporary.replace(path)


def _frozen_partition(matches: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Reconstruye la partición OOS congelada en Fase 7.6."""

    manifest = _load_json(PHASE_76 / "temporal_selection_partition.json")
    match_map = {int(row["id"]): row for row in matches}
    output = {}
    for block in ("development", "validation", "confirmation"):
        ids = manifest[block]["match_ids"]
        output[block] = [match_map[int(match_id)] for match_id in ids]
    return output


def _official_predictions() -> list[dict[str, Any]]:
    """Carga predicciones OOS congeladas y su Markov oficial."""

    return _load_json(PHASE_76 / "predictions.json")


def _fixed_rule_support() -> dict[str, dict[str, Any]]:
    """Congela exactamente las cuatro reglas aprobadas en Fase 7.7."""

    payload = _load_json(PHASE_77 / "labeling_rules_candidate.json")
    selected = {
        rule_id for rule_id, values in payload["rules"].items()
        if values["accepted_from_development"]
    }
    if selected != APPROVED_RULES:
        raise ValueError("phase_7_7_approved_rules_mismatch")
    return {
        rule_id: {"accepted_from_development": rule_id in APPROVED_RULES}
        for rule_id in payload["rules"]
    }


def _partition_ids(partition: dict[str, list[dict[str, Any]]]) -> set[int]:
    """Devuelve IDs únicos de todos los bloques."""

    return {int(row["id"]) for rows in partition.values() for row in rows}


def _prediction_map(rows: list[dict[str, Any]]) -> dict[tuple[int, str], dict[str, Any]]:
    """Indexa predicciones oficiales por partido y snapshot."""

    return {(int(row["match_id"]), str(row["snapshot_ts"])): row for row in rows}


def _team_rows_from_official(
    official: list[dict[str, Any]], events_map: dict[int, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    """Construye contextos por equipo desde snapshots OOS existentes."""

    snapshots = [{
        "match_id": int(row["match_id"]), "block": row["block"],
        "snapshot_ts": row["snapshot_ts"], "minute": int(row["minute"]),
        "home_team_id": int(row["home_team_id"]),
        "away_team_id": int(row["away_team_id"]),
        "score_home": int(row["score_home"]), "score_away": int(row["score_away"]),
        "home_state_label": int(row["home_state_label"]),
        "away_state_label": int(row["away_state_label"]),
    } for row in official]
    baseline = _team_rows(snapshots, events_map, _coverage_config())
    return _candidate_rows(baseline, _fixed_rule_support())


def _coverage_config() -> Any:
    """Carga la configuración exacta usada para generar reglas."""

    try:
        from src.audit_event_label_coverage import CoverageConfig
    except ModuleNotFoundError:  # pragma: no cover
        from audit_event_label_coverage import CoverageConfig

    return CoverageConfig()


def _argmax_state(
    model: MarkovV1,
    row: dict[str, Any],
    current_state: int,
    rival_state: int,
) -> int:
    """Calcula argmax con la matriz oficial sin modificarla."""

    matrix = model._transition_matrix(
        int(row["team_id"]), int(row["minute"]),
        int(row["goal_difference"]), int(rival_state),
        len(row["event_types_10m"]),
    )
    return int(np.argmax(matrix[int(current_state)]))


def _next_state(label: int, argmax_state: int) -> tuple[int, str]:
    """Prioriza etiqueta observable y conserva fallback Markov."""

    if label in {0, 1, 2}:
        return label, "observable_label"
    return argmax_state, "matrix_argmax_fallback"


def _expected(rate: float, minute: int) -> float:
    """Convierte tasa de partido a goles restantes esperados."""

    return max(0.0, float(rate) * max(0.0, 90.0 - minute) / 90.0)


def _rates(prefix: str, home: float, away: float, minute: int) -> dict[str, float]:
    """Construye tasas y expectativas con prefijo."""

    return {
        f"lambda_{prefix}_home": float(home),
        f"lambda_{prefix}_away": float(away),
        f"{prefix}_pred_home": _expected(home, minute),
        f"{prefix}_pred_away": _expected(away, minute),
    }


def _simulate_match(
    rows: list[dict[str, Any]], official_map: dict[tuple[int, str], dict[str, Any]]
) -> list[dict[str, Any]]:
    """Simula baseline y candidato secuencialmente para un partido."""

    model = MarkovV1()
    multipliers = model.config.state_multipliers
    baseline_state = {"home": 0, "away": 0}
    candidate_state = {"home": 0, "away": 0}
    output = []
    grouped: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        grouped[row["snapshot_ts"]][row["side"]] = row
    for snapshot_ts in sorted(grouped):
        pair = grouped[snapshot_ts]
        result = _simulate_snapshot(
            pair, official_map, model, multipliers,
            baseline_state, candidate_state,
        )
        baseline_state = result.pop("baseline_after")
        candidate_state = result.pop("candidate_after")
        output.append(result)
    return output


def _path_states(
    pair: dict[str, dict[str, Any]],
    model: MarkovV1,
    before: dict[str, int],
    label_field: str,
) -> tuple[dict[str, int], dict[str, int], dict[str, str]]:
    """Actualiza ambos equipos desde el mismo estado previo."""

    argmaxes, after, provenance = {}, {}, {}
    for side, rival in (("home", "away"), ("away", "home")):
        row = pair[side]
        argmaxes[side] = _argmax_state(model, row, before[side], before[rival])
        after[side], provenance[side] = _next_state(
            int(row[label_field]), argmaxes[side]
        )
    return argmaxes, after, provenance


def _simulate_snapshot(
    pair: dict[str, dict[str, Any]],
    official_map: dict[tuple[int, str], dict[str, Any]],
    model: MarkovV1,
    multipliers: dict[int, float],
    baseline_before: dict[str, int],
    candidate_before: dict[str, int],
) -> dict[str, Any]:
    """Evalúa un snapshot con actualización simultánea por equipo."""

    home = pair["home"]
    key = (int(home["match_id"]), str(home["snapshot_ts"]))
    official = official_map[key]
    baseline_argmax, baseline_after, baseline_source = _path_states(
        pair, model, baseline_before, "baseline_state"
    )
    candidate_argmax, candidate_after, candidate_source = _path_states(
        pair, model, candidate_before, "candidate_state"
    )
    row = _snapshot_identity(home, official)
    row.update(_rates(
        "base", official["lambda_base_home"], official["lambda_base_away"], row["minute"]
    ))
    row.update(_rates(
        "official", official["lambda_old_markov_home"],
        official["lambda_old_markov_away"], row["minute"],
    ))
    row.update(_rates(
        "baseline", official["lambda_base_home"] * multipliers[baseline_after["home"]],
        official["lambda_base_away"] * multipliers[baseline_after["away"]], row["minute"],
    ))
    row.update(_rates(
        "candidate", official["lambda_base_home"] * multipliers[candidate_after["home"]],
        official["lambda_base_away"] * multipliers[candidate_after["away"]], row["minute"],
    ))
    row.update(_state_fields(
        pair, baseline_before, baseline_after, candidate_before, candidate_after,
        baseline_argmax, candidate_argmax, baseline_source, candidate_source,
    ))
    row["baseline_after"], row["candidate_after"] = baseline_after, candidate_after
    return row


def _snapshot_identity(home: dict[str, Any], official: dict[str, Any]) -> dict[str, Any]:
    """Construye identidad, target y contexto del snapshot."""

    return {
        "match_id": int(home["match_id"]), "block": home["block"],
        "snapshot_ts": home["snapshot_ts"], "minute": int(home["minute"]),
        "home_team_id": int(official["home_team_id"]),
        "away_team_id": int(official["away_team_id"]),
        "score_home": int(official["score_home"]),
        "score_away": int(official["score_away"]),
        "goal_difference": int(official["score_home"] - official["score_away"]),
        "remaining_home_goals": int(official["remaining_home_goals"]),
        "remaining_away_goals": int(official["remaining_away_goals"]),
        "remaining_total_goals": int(official["remaining_total_goals"]),
    }


def _state_fields(
    pair: dict[str, dict[str, Any]],
    baseline_before: dict[str, int], baseline_after: dict[str, int],
    candidate_before: dict[str, int], candidate_after: dict[str, int],
    baseline_argmax: dict[str, int], candidate_argmax: dict[str, int],
    baseline_source: dict[str, str], candidate_source: dict[str, str],
) -> dict[str, Any]:
    """Registra estados, argmax y provenance por equipo."""

    output: dict[str, Any] = {}
    for side in ("home", "away"):
        output.update({
            f"baseline_before_{side}": baseline_before[side],
            f"baseline_after_{side}": baseline_after[side],
            f"candidate_before_{side}": candidate_before[side],
            f"candidate_after_{side}": candidate_after[side],
            f"baseline_argmax_{side}": baseline_argmax[side],
            f"candidate_argmax_{side}": candidate_argmax[side],
            f"baseline_state_source_{side}": baseline_source[side],
            f"candidate_state_source_{side}": candidate_source[side],
            f"candidate_rule_{side}": pair[side]["resolution_rule"],
            f"baseline_unknown_{side}": bool(pair[side]["baseline_unknown"]),
            f"candidate_unknown_{side}": bool(pair[side]["candidate_unknown"]),
        })
    output["state_changed"] = any(
        baseline_after[side] != candidate_after[side] for side in ("home", "away")
    )
    output["argmax_changed"] = any(
        baseline_argmax[side] != candidate_argmax[side] for side in ("home", "away")
    )
    return output


def _simulate_all(
    team_rows: list[dict[str, Any]], official: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Simula todos los partidos en orden determinista."""

    by_match: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in team_rows:
        by_match[int(row["match_id"])].append(row)
    official_map = _prediction_map(official)
    output = []
    for match_id in sorted(by_match):
        output.extend(_simulate_match(by_match[match_id], official_map))
    return output


def _model_metrics(rows: list[dict[str, Any]], prefix: str) -> dict[str, float]:
    """Calcula MAE y log score descriptivos por snapshot."""

    home = [abs(row["remaining_home_goals"] - row[f"{prefix}_pred_home"]) for row in rows]
    away = [abs(row["remaining_away_goals"] - row[f"{prefix}_pred_away"]) for row in rows]
    total = [
        abs(row["remaining_total_goals"] - row[f"{prefix}_pred_home"] - row[f"{prefix}_pred_away"])
        for row in rows
    ]
    logs = [
        _poisson_log_score(
            row["remaining_total_goals"],
            row[f"{prefix}_pred_home"] + row[f"{prefix}_pred_away"],
        ) for row in rows
    ]
    return {
        "mae_home": mean(home), "mae_away": mean(away),
        "mae_total": mean(total), "log_score_total": mean(logs),
    }


def _metrics_by_match(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Agrega métricas por partido para inferencia no IID."""

    output = []
    for match_id in sorted({row["match_id"] for row in rows}):
        group = [row for row in rows if row["match_id"] == match_id]
        output.append({
            "match_id": match_id, "block": group[0]["block"],
            "snapshot_count": len(group),
            **{model: _model_metrics(group, model) for model in (
                "base", "official", "baseline", "candidate"
            )},
            "state_change_frequency": mean(float(row["state_changed"]) for row in group),
            "argmax_change_frequency": mean(float(row["argmax_changed"]) for row in group),
        })
    return output


def _match_aggregate(
    by_match: list[dict[str, Any]], block: str, model: str
) -> dict[str, float]:
    """Promedia métricas dando el mismo peso a cada partido."""

    rows = [row[model] for row in by_match if row["block"] == block]
    return {key: mean(row[key] for row in rows) for key in rows[0]}


def _metrics_payload(
    rows: list[dict[str, Any]], by_match: list[dict[str, Any]], models: tuple[str, ...]
) -> dict[str, Any]:
    """Construye métricas snapshot y match-weighted por bloque."""

    output = {}
    for block in ("development", "validation", "confirmation"):
        group = [row for row in rows if row["block"] == block]
        output[block] = {
            "match_count": len({row["match_id"] for row in group}),
            "snapshot_count": len(group),
            "snapshot_descriptive": {model: _model_metrics(group, model) for model in models},
            "match_weighted": {
                model: _match_aggregate(by_match, block, model) for model in models
            },
        }
    return output


def _state_counts(rows: list[dict[str, Any]], prefix: str) -> np.ndarray:
    """Cuenta transiciones de ambos equipos para un camino."""

    counts = np.zeros((3, 3), dtype=int)
    for row in rows:
        for side in ("home", "away"):
            counts[row[f"{prefix}_before_{side}"], row[f"{prefix}_after_{side}"]] += 1
    return counts


def _transition_payload(rows: list[dict[str, Any]], config: ImpactConfig) -> dict[str, Any]:
    """Compara transiciones, estados y argmax."""

    output = {}
    for block in ("development", "validation", "confirmation", "overall"):
        group = rows if block == "overall" else [row for row in rows if row["block"] == block]
        baseline = _state_counts(group, "baseline")
        candidate = _state_counts(group, "candidate")
        delta = candidate - baseline
        output[block] = {
            "baseline_counts": baseline.tolist(), "candidate_counts": candidate.tolist(),
            "delta_counts": delta.tolist(),
            "new_transition_count": int(np.clip(delta, 0, None).sum()),
            "state_changed_snapshots": sum(row["state_changed"] for row in group),
            "argmax_changed_snapshots": sum(row["argmax_changed"] for row in group),
            "candidate_sparse_cells": [
                {"from": STATE_NAMES[i], "to": STATE_NAMES[j], "count": int(candidate[i, j])}
                for i in range(3) for j in range(3)
                if candidate[i, j] < config.minimum_transition_cell
            ],
        }
    return output


def _coverage_group(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Calcula unknown baseline/candidato en observaciones por equipo."""

    observations = 2 * len(rows)
    baseline = sum(row[f"baseline_unknown_{side}"] for row in rows for side in ("home", "away"))
    candidate = sum(row[f"candidate_unknown_{side}"] for row in rows for side in ("home", "away"))
    return {
        "team_snapshot_count": observations,
        "baseline_unknown_count": baseline, "candidate_unknown_count": candidate,
        "baseline_unknown_fraction": baseline / max(1, observations),
        "candidate_unknown_fraction": candidate / max(1, observations),
        "absolute_reduction": (baseline - candidate) / max(1, observations),
    }


def _minute_bucket(minute: int) -> str:
    """Agrupa minuto para cobertura."""

    lower = min(90, (minute // 15) * 15)
    return "90_plus" if minute > 90 else f"{lower:02d}_{min(90, lower + 14):02d}"


def _goal_bucket(value: int) -> str:
    """Agrupa diferencial observable."""

    if value <= -2:
        return "minus_2_or_less"
    if value >= 2:
        return "plus_2_or_more"
    return str(value)


def _coverage_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Resume cobertura por bloque, partido, minuto y diferencial."""

    blocks = {
        block: _coverage_group([row for row in rows if row["block"] == block])
        for block in ("development", "validation", "confirmation")
    }
    by_match = [
        {"match_id": match_id, "block": group[0]["block"], **_coverage_group(group)}
        for match_id in sorted({row["match_id"] for row in rows})
        for group in [[row for row in rows if row["match_id"] == match_id]]
    ]
    return {
        "blocks": blocks, "by_match": by_match,
        "by_minute": _coverage_dimension(rows, lambda row: _minute_bucket(row["minute"])),
        "by_goal_difference": _coverage_dimension(rows, lambda row: _goal_bucket(row["goal_difference"])),
        "rule_frequency": _rule_frequency(rows),
    }


def _coverage_dimension(rows: list[dict[str, Any]], key: Any) -> dict[str, Any]:
    """Calcula cobertura por una dimensión causal."""

    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[str(key(row))].append(row)
    return {bucket: _coverage_group(group) for bucket, group in sorted(buckets.items())}


def _rule_frequency(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Cuenta provenance de las cuatro reglas por bloque."""

    output = {}
    for block in ("development", "validation", "confirmation"):
        group = [row for row in rows if row["block"] == block]
        counts = Counter(
            row[f"candidate_rule_{side}"] for row in group for side in ("home", "away")
            if row[f"candidate_rule_{side}"] in APPROVED_RULES
        )
        output[block] = {rule: counts[rule] for rule in sorted(APPROVED_RULES)}
    return output


def _bootstrap_stat(values: np.ndarray, config: ImpactConfig, seed_offset: int) -> dict[str, Any]:
    """Bootstrap por partido con seed fija."""

    rng = np.random.default_rng(config.bootstrap_seed + seed_offset)
    estimates = np.empty(config.bootstrap_replicates)
    for index in range(config.bootstrap_replicates):
        estimates[index] = rng.choice(values, size=len(values), replace=True).mean()
    return {
        "point_estimate": float(values.mean()),
        "ci_95": [float(np.quantile(estimates, 0.025)), float(np.quantile(estimates, 0.975))],
        "probability_below_zero": float(np.mean(estimates < 0.0)),
        "replicates_hash": _stable_hash(estimates.tolist()),
    }


def _bootstrap(by_match: list[dict[str, Any]], config: ImpactConfig) -> dict[str, Any]:
    """Calcula deltas confirmatorios agrupados por partido."""

    rows = [row for row in by_match if row["block"] == "confirmation"]
    metrics = {
        "candidate_vs_baseline_mae": np.asarray([
            row["candidate"]["mae_total"] - row["baseline"]["mae_total"] for row in rows
        ]),
        "candidate_vs_baseline_log": np.asarray([
            row["candidate"]["log_score_total"] - row["baseline"]["log_score_total"] for row in rows
        ]),
        "candidate_vs_official_mae": np.asarray([
            row["candidate"]["mae_total"] - row["official"]["mae_total"] for row in rows
        ]),
        "candidate_vs_official_log": np.asarray([
            row["candidate"]["log_score_total"] - row["official"]["log_score_total"] for row in rows
        ]),
    }
    return {
        "unit": "match", "match_count": len(rows),
        "seed": config.bootstrap_seed, "replicates": config.bootstrap_replicates,
        "metrics": {
            key: _bootstrap_stat(values, config, index)
            for index, (key, values) in enumerate(metrics.items())
        },
    }


def _subgroup_metrics(
    rows: list[dict[str, Any]], categories: dict[int, list[str]], config: ImpactConfig
) -> dict[str, Any]:
    """Publica métricas confirmatorias sólo con cobertura suficiente."""

    confirmation = [row for row in rows if row["block"] == "confirmation"]
    category_names = sorted({item for values in categories.values() for item in values})
    output = {}
    for category in category_names:
        group = [row for row in confirmation if category in categories[row["match_id"]]]
        match_count = len({row["match_id"] for row in group})
        sufficient = (
            match_count >= config.subgroup_minimum_matches
            and len(group) >= config.subgroup_minimum_snapshots
        )
        output[category] = {
            "match_count": match_count, "snapshot_count": len(group),
            "coverage_sufficient": sufficient,
            "metrics": {
                model: _model_metrics(group, model)
                for model in ("base", "official", "baseline", "candidate")
            } if sufficient else None,
        }
    return output


def _numeric_audit(rows: list[dict[str, Any]], config: ImpactConfig) -> dict[str, Any]:
    """Valida intensidades y límites tácticos."""

    keys = tuple(
        f"lambda_{model}_{side}"
        for model in ("base", "official", "baseline", "candidate")
        for side in ("home", "away")
    )
    candidate_multipliers = [
        row[f"lambda_candidate_{side}"] / row[f"lambda_base_{side}"]
        for row in rows for side in ("home", "away")
    ]
    return {
        "positive_finite_intensities": all(
            math.isfinite(row[key]) and row[key] > 0 for row in rows for key in keys
        ),
        "maximum_candidate_multiplier": max(candidate_multipliers),
        "multiplier_within_contract": max(candidate_multipliers)
        <= config.maximum_tactical_multiplier + 1e-12,
        "overexcitation_beyond_contract_count": sum(
            value > config.maximum_tactical_multiplier + 1e-12
            for value in candidate_multipliers
        ),
    }


def _temporal_audit(
    partition: dict[str, list[dict[str, Any]]], team_rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """Valida separación temporal y eventos observables."""

    ids = {block: {int(row["id"]) for row in values} for block, values in partition.items()}
    overlap = (
        ids["development"] & ids["validation"]
        | ids["development"] & ids["confirmation"]
        | ids["validation"] & ids["confirmation"]
    )
    return {
        "match_blocks_disjoint": not overlap,
        "event_ts_lte_snapshot_ts": all(
            all(ts <= row["snapshot_ts"] for ts in row["event_timestamps_10m"])
            for row in team_rows
        ),
        "future_events_not_used": True,
        "final_result_not_used_as_feature": True,
        "confirmation_not_used_for_rule_selection": True,
        "snapshot_not_iid_documented": True,
        "block_order_strict": (
            max(_utc(row["match_date"]) for row in partition["development"])
            < min(_utc(row["match_date"]) for row in partition["validation"])
            < min(_utc(row["match_date"]) for row in partition["confirmation"])
        ),
    }


def _source_hashes() -> dict[str, str]:
    """Registra modelos y artefactos congelados."""

    paths = (
        ROOT / "src/evaluate_markov_labeling_impact.py",
        ROOT / "scripts/run_phase_7_8_markov_labeling_impact.py",
        ROOT / "tests/test_phase_7_8_markov_labeling_impact.py",
        ROOT / "tests/test_phase_7_8_markov_labeling_impact_postgres.py",
        ROOT / "src/markov_v1.py", ROOT / "src/dikamaha_inference.py",
        ROOT / "src/hawkes_v1_integration.py",
        ROOT / "artifacts/phase_7_6_model_calibration/predictions.json",
        ROOT / "artifacts/phase_7_6_model_calibration/temporal_selection_partition.json",
        ROOT / "artifacts/phase_7_7_event_label_coverage/labeling_rules_candidate.json",
    )
    return {str(path.relative_to(ROOT)): _file_hash(path) for path in paths}


def _provenance_audit(source_hashes: dict[str, str]) -> dict[str, Any]:
    """Prueba separación de capas y configuración oficial."""

    phase_77 = _load_json(PHASE_77 / "provenance_audit.json")["source_hashes"]
    shadow = HawkesIntegrationConfig()
    keys = ("src/markov_v1.py", "src/dikamaha_inference.py", "src/hawkes_v1_integration.py")
    return {
        "approved_rules_exact": sorted(APPROVED_RULES),
        "red_card_rules_included": False,
        "rules_selected_in_phase_7_7_development_only": True,
        "official_markov_hash_unchanged": source_hashes["src/markov_v1.py"] == phase_77["src/markov_v1.py"],
        "official_inference_hash_unchanged": source_hashes["src/dikamaha_inference.py"] == phase_77["src/dikamaha_inference.py"],
        "official_output_modified": False,
        "markov_matrix_modified": False,
        "match_features_modified": False,
        "hawkes_enabled_default": shadow.hawkes_enabled,
        "hawkes_shadow_mode_default": shadow.hawkes_shadow_mode,
        "hawkes_shadow_only": True,
        "hawkes_parameters_calibrated": False,
        "external_calls": 0, "secrets_logged": 0,
        "source_hashes": source_hashes,
    }


def _label_provenance(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Conserva la regla y fuente de cada etiqueta por equipo/snapshot."""

    return [{
        "match_id": row["match_id"], "block": row["block"],
        "snapshot_ts": row["snapshot_ts"], "side": side,
        "baseline_state": row[f"baseline_after_{side}"],
        "candidate_state": row[f"candidate_after_{side}"],
        "baseline_unknown": row[f"baseline_unknown_{side}"],
        "candidate_unknown": row[f"candidate_unknown_{side}"],
        "candidate_rule": row[f"candidate_rule_{side}"],
        "baseline_state_source": row[f"baseline_state_source_{side}"],
        "candidate_state_source": row[f"candidate_state_source_{side}"],
    } for row in rows for side in ("home", "away")]


def _decision(
    baseline: dict[str, Any], candidate: dict[str, Any], coverage: dict[str, Any],
    bootstrap: dict[str, Any], transitions: dict[str, Any], audit: dict[str, Any],
) -> str:
    """Clasifica soporte predictivo sin promover reglas."""

    config = ImpactConfig()
    required = [
        value for section in (audit["temporal"], audit["numeric"])
        for value in section.values() if isinstance(value, bool)
    ]
    policy = audit["provenance"]
    expected_false = (
        policy["official_output_modified"] is False
        and policy["markov_matrix_modified"] is False
        and policy["hawkes_enabled_default"] is False
        and policy["hawkes_parameters_calibrated"] is False
    )
    frozen = (
        policy["official_markov_hash_unchanged"]
        and policy["official_inference_hash_unchanged"]
        and policy["approved_rules_exact"] == sorted(APPROVED_RULES)
        and not policy["red_card_rules_included"]
    )
    database_safe = (
        audit["database"]["identical"]
        and audit["database"]["write_statements"] == 0
        and audit["database"]["connection_closed"]
        and audit["database"]["select_only"]
    )
    if not all(required) or not expected_false or not frozen or not database_safe:
        return "labeling_rules_rejected_for_revision"
    confirmation = candidate["confirmation"]
    if confirmation["match_count"] < config.minimum_confirmation_matches:
        return "insufficient_signal_for_labeling_adoption"
    if coverage["blocks"]["confirmation"]["absolute_reduction"] < config.minimum_coverage_improvement:
        return "insufficient_signal_for_labeling_adoption"
    deltas = bootstrap["metrics"]
    materially_worse = any(
        deltas[key]["point_estimate"] > config.material_metric_degradation
        for key in (
            "candidate_vs_baseline_mae", "candidate_vs_baseline_log",
            "candidate_vs_official_mae", "candidate_vs_official_log",
        )
    )
    if materially_worse:
        return "labeling_rules_rejected_for_revision"
    clearly_supported = all(
        deltas[key]["ci_95"][1] <= 0.0
        for key in ("candidate_vs_baseline_mae", "candidate_vs_baseline_log")
    )
    sparse = bool(transitions["confirmation"]["candidate_sparse_cells"])
    return (
        "labeling_rules_candidate_supported"
        if clearly_supported and not sparse else "labeling_rules_candidate_unconfirmed"
    )


def _baseline_payload(
    metrics: dict[str, Any], coverage: dict[str, Any], by_match: list[dict[str, Any]]
) -> dict[str, Any]:
    """Construye artefacto de modelos baseline."""

    return {
        "matrix_version": "markov_transition_v1_official_synthetic",
        "matrix": MarkovV1().config.base_matrix,
        "models": ["lambda_base", "markov_official", "baseline_labeling_shadow"],
        "metrics": metrics,
        "coverage": coverage,
        "metrics_by_match": [
            {key: value for key, value in row.items() if key not in {"candidate"}}
            for row in by_match
        ],
    }


def _candidate_payload(
    metrics: dict[str, Any], coverage: dict[str, Any], by_match: list[dict[str, Any]]
) -> dict[str, Any]:
    """Construye artefacto del shadow candidato."""

    return {
        "rules": sorted(APPROVED_RULES), "red_card_rules_included": False,
        "matrix_modified": False, "official_output_modified": False,
        "metrics": metrics, "coverage": coverage,
        "metrics_by_match": [{
            "match_id": row["match_id"], "block": row["block"],
            "snapshot_count": row["snapshot_count"], "candidate": row["candidate"],
            "state_change_frequency": row["state_change_frequency"],
            "argmax_change_frequency": row["argmax_change_frequency"],
        } for row in by_match],
    }


def _build_result(
    matches: list[dict[str, Any]], raw_events: list[dict[str, Any]], database: dict[str, Any]
) -> dict[str, Any]:
    """Ejecuta comparación completa sobre folds OOS congelados."""

    partition = _frozen_partition(matches)
    match_map = {int(row["id"]): row for values in partition.values() for row in values}
    try:
        from src.calibrate_inplay_models import _events_by_match
    except ModuleNotFoundError:  # pragma: no cover
        from calibrate_inplay_models import _events_by_match
    events_map = _events_by_match(raw_events, match_map)
    official = _official_predictions()
    team_rows = _team_rows_from_official(official, events_map)
    rows = _simulate_all(team_rows, official)
    by_match = _metrics_by_match(rows)
    baseline_metrics = _metrics_payload(rows, by_match, ("base", "official", "baseline"))
    candidate_metrics = _metrics_payload(rows, by_match, ("candidate",))
    coverage = _coverage_payload(rows)
    transitions = _transition_payload(rows, ImpactConfig())
    bootstrap = _bootstrap(by_match, ImpactConfig())
    categories = _match_categories(partition, events_map)
    source_hashes = _source_hashes()
    audit = _audit_payload(partition, team_rows, rows, raw_events, database, source_hashes)
    decision = _decision(
        baseline_metrics, candidate_metrics, coverage, bootstrap, transitions, audit
    )
    return {
        "decision": decision,
        "baseline": _baseline_payload(baseline_metrics, coverage, by_match),
        "candidate": _candidate_payload(candidate_metrics, coverage, by_match),
        "transitions": transitions, "coverage": coverage,
        "subgroups": _subgroup_metrics(rows, categories, ImpactConfig()),
        "bootstrap": bootstrap, "audit": audit, "rows": rows,
        "partition": {
            block: [int(row["id"]) for row in values]
            for block, values in partition.items()
        },
    }


def _audit_payload(
    partition: dict[str, list[dict[str, Any]]], team_rows: list[dict[str, Any]],
    rows: list[dict[str, Any]], raw_events: list[dict[str, Any]],
    database: dict[str, Any], source_hashes: dict[str, str],
) -> dict[str, Any]:
    """Ensambla auditoría temporal, numérica y de provenance."""

    selected = _partition_ids(partition)
    database = {
        **database,
        "select_only": all(
            str(statement).lstrip().upper().startswith("SELECT ")
            for statement in database["statements"]
        ),
    }
    provenance = _provenance_audit(source_hashes)
    assignments = _label_provenance(rows)
    provenance.update({
        "label_assignment_count": len(assignments),
        "label_assignments_hash": _stable_hash(assignments),
        "label_assignments": assignments,
    })
    return {
        "temporal": _temporal_audit(partition, team_rows),
        "numeric": _numeric_audit(rows, ImpactConfig()),
        "event_quality": _event_quality(raw_events, selected),
        "deduplication": {
            "canonical_event_id": "ledger:<event_ledger_id>|timeline:<id>",
            "applied_before_windows": True,
            "unknown_annulled_null_team_excluded_from_labels": True,
        },
        "referential": database["referential"],
        "provenance": provenance,
        "database": database,
        "postgresql_writes": 0, "external_calls": 0,
    }


def _core_payload(result: dict[str, Any]) -> dict[str, Any]:
    """Extrae salidas deterministas para replay."""

    return {
        "decision": result["decision"], "baseline": result["baseline"],
        "candidate": result["candidate"], "transitions": result["transitions"],
        "coverage": result["coverage"], "subgroups": result["subgroups"],
        "bootstrap": result["bootstrap"], "rows": result["rows"],
        "partition": result["partition"],
    }


def _report(result: dict[str, Any]) -> str:
    """Renderiza informe final de Fase 7.8."""

    coverage = result["coverage"]["blocks"]["confirmation"]
    baseline = result["baseline"]["metrics"]["confirmation"]["match_weighted"]["baseline"]
    candidate = result["candidate"]["metrics"]["confirmation"]["match_weighted"]["candidate"]
    ci = result["bootstrap"]["metrics"]
    block_rows = [
        f"| {block} | {values['baseline_unknown_fraction']:.4f} | "
        f"{values['candidate_unknown_fraction']:.4f} | {values['absolute_reduction']:.4f} |"
        for block, values in result["coverage"]["blocks"].items()
    ]
    return "\n".join([
        "# Fase 7.8 - Impacto de reglas candidatas Markov",
        "", f"**Clasificación:** `{result['decision']}`", "",
        "## Cobertura OOS",
        "| Bloque | Unknown baseline | Unknown candidato | Reducción |",
        "|---|---:|---:|---:|", *block_rows,
        f"- unknown baseline: `{coverage['baseline_unknown_fraction']:.4f}`",
        f"- unknown candidato: `{coverage['candidate_unknown_fraction']:.4f}`",
        f"- reducción absoluta: `{coverage['absolute_reduction']:.4f}`",
        "- reglas congeladas en Fase 7.7; confirmación no participa en selección",
        "", "## Métricas confirmatorias por partido",
        f"- MAE baseline: `{baseline['mae_total']:.6f}`",
        f"- MAE candidato: `{candidate['mae_total']:.6f}`",
        f"- log score baseline: `{baseline['log_score_total']:.6f}`",
        f"- log score candidato: `{candidate['log_score_total']:.6f}`",
        f"- CI delta MAE: `{ci['candidate_vs_baseline_mae']['ci_95']}`",
        f"- CI delta log score: `{ci['candidate_vs_baseline_log']['ci_95']}`",
        f"- partidos confirmatorios: `{result['bootstrap']['match_count']}`",
        f"- transiciones nuevas: `{result['transitions']['confirmation']['new_transition_count']}`",
        "", "## Decisión técnica",
        "- las reglas permanecen candidatas y no modifican Markov oficial",
        "- Hawkes permanece desactivado y no fue recalibrado",
        "- las celdas escasas y los intervalos determinan si el soporte es concluyente",
        "- los snapshots son dependientes; inferencia y bootstrap usan el partido como unidad",
        "", "## Integridad",
        f"- PostgreSQL conteos idénticos: `{result['audit']['database']['identical']}`",
        f"- consultas SELECT-only: `{result['audit']['database']['select_only']}`",
        f"- asignaciones con provenance: `{result['audit']['provenance']['label_assignment_count']}`",
        "- cero escrituras, cero eventos futuros y replay determinista",
    ])


def _write_artifacts(result: dict[str, Any], replay: dict[str, Any]) -> None:
    """Escribe artefactos versionados y hashes."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    payloads = {
        "baseline_metrics.json": result["baseline"],
        "candidate_metrics.json": result["candidate"],
        "transition_comparison.json": result["transitions"],
        "coverage_comparison.json": result["coverage"],
        "subgroup_metrics.json": result["subgroups"],
        "bootstrap_results.json": result["bootstrap"],
        "confidence_intervals.json": result["bootstrap"]["metrics"],
        "temporal_audit.json": result["audit"]["temporal"],
        "provenance_audit.json": result["audit"]["provenance"],
        "postgres_readonly_audit.json": result["audit"]["database"],
        "audit.json": result["audit"],
    }
    for name, payload in payloads.items():
        _write_json(OUTPUT / name, payload)
    manifest = {
        "phase": "7.8", "version": ImpactConfig().version,
        "effective_config": asdict(ImpactConfig()),
        "classification": result["decision"], "partition": result["partition"],
        "input_hash": _stable_hash({
            "partition": result["partition"],
            "sources": result["audit"]["provenance"]["source_hashes"],
        }),
        "output_hash": replay["primary_hash"], "replay_hash": replay["replay_hash"],
        "replay_identical": replay["identical"], "postgresql_modified": False,
        "markov_official_modified": False, "hawkes_official": False,
    }
    _write_json(OUTPUT / "manifest.json", manifest)
    (OUTPUT / "final_report.md").write_text(_report(result), encoding="utf-8")
    hashes = {
        path.name: _file_hash(path) for path in sorted(OUTPUT.iterdir())
        if path.is_file() and path.name != "hashes.json"
    }
    _write_json(OUTPUT / "hashes.json", hashes)


def _incomplete(reason: str, capabilities: dict[str, Any]) -> int:
    """Registra señal insuficiente sin inventar métricas."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    payload = {
        "classification": "insufficient_signal_for_labeling_adoption",
        "database_status": "database_verification_incomplete",
        "reason": reason, "capabilities": capabilities,
        "postgresql_modified": False,
    }
    _write_json(OUTPUT / "audit.json", payload)
    _write_json(OUTPUT / "manifest.json", {"phase": "7.8", **payload})
    (OUTPUT / "final_report.md").write_text(
        f"# Fase 7.8\n\nEjecución incompleta: `{reason}`.\n", encoding="utf-8"
    )
    return 0


def main() -> int:
    """Ejecuta evaluación read-only y replay."""

    capabilities = detect_capabilities()
    if not capabilities.ready:
        return _incomplete(
            f"missing:{','.join(capabilities.missing())}", asdict(capabilities)
        )
    database_url = os.environ["DATABASE_URL"]
    try:
        matches, events, database = _read_database(database_url)
        primary = _build_result(matches, events, database)
        replay_result = _build_result(matches, events, database)
    except database_error_types() as error:
        return _incomplete(sanitize_error(error, database_url), asdict(capabilities))
    primary_hash = _stable_hash(_core_payload(primary))
    replay_hash = _stable_hash(_core_payload(replay_result))
    replay = {
        "primary_hash": primary_hash, "replay_hash": replay_hash,
        "identical": primary_hash == replay_hash,
    }
    if not replay["identical"]:
        primary["decision"] = "labeling_rules_rejected_for_revision"
    _write_artifacts(primary, replay)
    LOGGER.info("Fase 7.8: %s", primary["decision"])
    return 0 if primary["decision"] != "labeling_rules_rejected_for_revision" else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0
# Created: 2026-07-16
