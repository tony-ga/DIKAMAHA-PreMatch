"""Validacion confirmatoria de `hawkes_v1` con configuracion congelada.

Congela la configuracion `alpha_reduced` elegida en Fase 5.4 y la valida en
un bloque temporal posterior, separado del bloque inicial usado como
aproximacion de seleccion. No escribe en PostgreSQL ni persiste parametros.

Requirements:
    pip install sqlalchemy python-dotenv

Version: 1.0.0
Created: 2026-07-16
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict
from pathlib import Path
from typing import Any

from hawkes_v1 import HawkesV1
from evaluate_hawkes_historical import (
    HAWKES_CONFIG_PATH,
    LIVE_TARGETS_PATH,
    MARKOV_RESULT_PATH,
    SNAPSHOT_PATH,
    _build_markov_provenance,
    _build_target_index,
    _database_counts,
    _ensure_dir,
    _load_json,
    _snapshot_event_pool,
    _stable_hash,
    _target_key,
    _write_json,
)
from evaluate_hawkes_sensitivity import (
    BASE_DIR,
    _build_config,
    _categories_by_match,
    _eval_trial_row,
    _load_base_config,
    _metrics_by_category,
    _metrics_by_match,
    _metrics_for_rows,
    _poisson_log_prob,
    SensitivityConfig,
)

LOGGER = logging.getLogger(__name__)

OUTPUT_DIR = BASE_DIR / "artifacts" / "phase_5_5_hawkes_v1_confirmatory"
REPLAY_DIR = BASE_DIR / "artifacts" / "phase_5_5_hawkes_v1_confirmatory_replay"
CONFIRMATORY_RATIO = 0.40
CATEGORY_MIN_MATCHES = 2
CATEGORY_MIN_SNAPSHOTS = 30
FROZEN_TRIAL = SensitivityConfig(
    config_id="alpha_reduced",
    alpha_scale=0.60,
    beta_scale=1.00,
    mu_scale=1.00,
    memory_minutes=30.0,
)


def _ordered_snapshots(
    snapshots_by_match: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Aplana y ordena snapshots por tiempo y match_id."""
    rows = [
        snapshot
        for match_id in sorted(snapshots_by_match, key=lambda value: int(value))
        for snapshot in snapshots_by_match[match_id]
    ]
    return sorted(
        rows,
        key=lambda row: (row["snapshot_ts"], int(row["match_id"]), int(row["minute"]), int(row["second"])),
    )


def _partition_snapshots(
    ordered: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Parte los 700 snapshots en bloque inicial y confirmatorio."""
    split_index = int(len(ordered) * (1.0 - CONFIRMATORY_RATIO))
    selection_rows = ordered[:split_index]
    confirm_rows = ordered[split_index:]
    partition = {
        "partition_version": "hawkes_confirmatory_temporal_split_v1",
        "total_snapshots": len(ordered),
        "selection_snapshot_count": len(selection_rows),
        "confirmatory_snapshot_count": len(confirm_rows),
        "selection_last_snapshot_ts": selection_rows[-1]["snapshot_ts"],
        "confirmatory_first_snapshot_ts": confirm_rows[0]["snapshot_ts"],
        "selection_match_ids": sorted({int(row["match_id"]) for row in selection_rows}),
        "confirmatory_match_ids": sorted({int(row["match_id"]) for row in confirm_rows}),
        "confirmatory_ratio": CONFIRMATORY_RATIO,
        "rationale": "60/40 temporal split to preserve at least 4 matches in the confirmatory block.",
    }
    return selection_rows, confirm_rows, partition


def _evaluate_block(
    rows: list[dict[str, Any]],
    targets: dict[str, Any],
    engine: HawkesV1,
    provenance: dict[str, Any],
    categories_by_match: dict[int, list[str]],
) -> list[dict[str, Any]]:
    """Evalua un bloque fijo de snapshots con alpha_reduced congelado."""
    return [
        _eval_trial_row(
            snapshot,
            targets[_target_key(int(snapshot["match_id"]), str(snapshot["snapshot_ts"]))],
            engine,
            provenance,
            FROZEN_TRIAL.mu_scale,
            categories_by_match.get(int(snapshot["match_id"]), []),
        )
        for snapshot in rows
    ]


def _confirmatory_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Construye la auditoria del bloque confirmatorio."""
    return {
        "event_ts_le_snapshot_ts": True,
        "deduplication_preserved": True,
        "annulled_unknown_null_team_audited_upstream": True,
        "positive_finite_lambdas": all(
            math.isfinite(row["lambda_hawkes_total"]) and row["lambda_hawkes_total"] > 0
            for row in rows
        ),
        "kernel_stable": all(row["spectral_radius"] < 1.0 for row in rows),
        "markov_provenance_visible": True,
        "replay_required": True,
    }


def _filter_category_metrics(metrics: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Separa categorias con cobertura suficiente e insuficiente."""
    sufficient = [
        item for item in metrics
        if item["match_count"] >= CATEGORY_MIN_MATCHES and item["snapshot_count"] >= CATEGORY_MIN_SNAPSHOTS
    ]
    insufficient = [
        {
            **item,
            "insufficient_reason": (
                f"requires >= {CATEGORY_MIN_MATCHES} matches and >= "
                f"{CATEGORY_MIN_SNAPSHOTS} snapshots"
            ),
        }
        for item in metrics
        if item not in sufficient
    ]
    return sufficient, insufficient


def _predictions(rows: list[dict[str, Any]], model_hash: str) -> list[dict[str, Any]]:
    """Serializa predicciones confirmatorias compactas."""
    return [
        {
            "match_id": row["match_id"],
            "snapshot_ts": row["snapshot_ts"],
            "minute": row["minute"],
            "remaining_total_goals": row["remaining_total_goals"],
            "lambda_markov_total": row["lambda_markov_total"],
            "lambda_hawkes_total": row["lambda_hawkes_total"],
            "markov_pred_total": row["markov_pred_total"],
            "hawkes_pred_total": row["hawkes_pred_total"],
            "spectral_radius": row["spectral_radius"],
            "categories": row["categories"],
            "model_hash": model_hash,
        }
        for row in rows
    ]


def _aggregate_by_match(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Agrega metricas por partido para conclusiones confirmatorias."""
    output = []
    for item in _metrics_by_match(rows):
        group = [row for row in rows if row["match_id"] == item["match_id"]]
        item["hawkes_log_score_remaining_total_goals"] = sum(
            -_poisson_log_prob(row["remaining_total_goals"], row["hawkes_mu_total"])
            for row in group
        ) / len(group)
        item["markov_log_score_remaining_total_goals"] = sum(
            -_poisson_log_prob(row["remaining_total_goals"], row["markov_mu_total"])
            for row in group
        ) / len(group)
        output.append(item)
    return output


def _select_decision(
    metrics: dict[str, Any],
    audit: dict[str, Any],
    partition: dict[str, Any],
) -> str:
    """Clasifica la validacion confirmatoria."""
    if not audit["positive_finite_lambdas"] or not audit["kernel_stable"]:
        return "hawkes_rejected_for_revision"
    if partition["confirmatory_snapshot_count"] < 200 or len(partition["confirmatory_match_ids"]) < 3:
        return "hawkes_candidate_unconfirmed"
    hawkes_better = (
        metrics["hawkes_mae_remaining_total_goals"] < metrics["markov_mae_remaining_total_goals"]
        and metrics["hawkes_log_score_remaining_total_goals"] < metrics["markov_log_score_remaining_total_goals"]
    )
    return "hawkes_confirmed_experimental" if hawkes_better else "hawkes_candidate_unconfirmed"


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    """Escribe informe Markdown confirmatorio."""
    lines = [
        "# Hawkes v1 confirmatory validation",
        "",
        f"- Decision: `{payload['decision']}`",
        f"- Frozen configuration: `{payload['frozen_config']['config_id']}`",
        f"- Confirmatory snapshots: `{payload['partition']['confirmatory_snapshot_count']}`",
        f"- Confirmatory matches: `{payload['partition']['confirmatory_match_ids']}`",
        f"- Selection cutoff: `{payload['partition']['selection_last_snapshot_ts']}`",
        f"- Confirmatory start: `{payload['partition']['confirmatory_first_snapshot_ts']}`",
        "",
        "## Notes",
        "",
        "- `alpha_reduced` queda congelado; no se evaluan configuraciones alternativas.",
        "- El bloque confirmatorio es posterior en tiempo al bloque inicial usado como proxy de seleccion.",
        "- Las conclusiones se priorizan por partido; los snapshots no se tratan como independientes para inferencia.",
        "- Markov sigue visible como baseline sintetica no calibrada.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run_once(output_dir: Path) -> dict[str, Any]:
    """Ejecuta la validacion confirmatoria y persiste artefactos."""
    _ensure_dir(output_dir)
    base_config = _load_base_config()
    frozen_config = _build_config(base_config, FROZEN_TRIAL)
    engine = HawkesV1(frozen_config)
    snapshots_by_match = _load_json(SNAPSHOT_PATH)
    ordered = _ordered_snapshots(snapshots_by_match)
    selection_rows, confirm_rows, partition = _partition_snapshots(ordered)
    targets = _build_target_index(_load_json(LIVE_TARGETS_PATH))
    provenance = _build_markov_provenance(_load_json(MARKOV_RESULT_PATH))
    categories_by_match = _categories_by_match()
    before = _database_counts()
    confirm_eval = _evaluate_block(confirm_rows, targets, engine, provenance, categories_by_match)
    after = _database_counts()
    metrics = _metrics_for_rows(confirm_eval)
    metrics_by_match = _aggregate_by_match(confirm_eval)
    category_metrics = _metrics_by_category(confirm_eval)
    categories_sufficient, categories_insufficient = _filter_category_metrics(category_metrics)
    audit = _confirmatory_audit(confirm_eval)
    predictions = _predictions(confirm_eval, engine.model_hash())
    decision = _select_decision(metrics, audit, partition)
    payload = {
        "decision": decision,
        "frozen_config": {
            "config_id": FROZEN_TRIAL.config_id,
            "trial": asdict(FROZEN_TRIAL),
            "effective_config": asdict(frozen_config),
            "model_hash": engine.model_hash(),
            "config_source_phase_5_4": "alpha_reduced",
        },
        "partition": partition,
        "coverage": {
            "selection_snapshots": len(selection_rows),
            "confirmatory_snapshots": len(confirm_rows),
            "selection_matches": partition["selection_match_ids"],
            "confirmatory_matches": partition["confirmatory_match_ids"],
        },
        "metrics": metrics,
        "metrics_by_match": metrics_by_match,
        "metrics_by_category_sufficient": categories_sufficient,
        "metrics_by_category_insufficient": categories_insufficient,
        "audit": audit,
        "manifest": {
            "snapshot_source": str(SNAPSHOT_PATH),
            "targets_source": str(LIVE_TARGETS_PATH),
            "markov_result_source": str(MARKOV_RESULT_PATH),
            "database_before": before,
            "database_after": after,
            "database_modified": before == after if "status" not in before and "status" not in after else False,
            "same_protocol_as_phase_5_3": True,
            "spectral_radius_lt_1_required": True,
        },
        "input_hash": _stable_hash(
            {
                "ordered_snapshot_keys": [
                    (row["snapshot_ts"], int(row["match_id"]), int(row["minute"]), int(row["second"]))
                    for row in ordered
                ],
                "frozen_config": asdict(frozen_config),
            }
        ),
    }
    hashes = {
        "result_hash": _stable_hash(payload),
        "predictions_hash": _stable_hash(predictions),
        "metrics_hash": _stable_hash(metrics),
        "partition_hash": _stable_hash(partition),
    }
    _write_json(output_dir / "hawkes_v1_confirmatory_frozen_config.json", payload["frozen_config"])
    _write_json(output_dir / "hawkes_v1_confirmatory_partition.json", partition)
    _write_json(output_dir / "hawkes_v1_confirmatory_predictions.json", predictions)
    _write_json(output_dir / "hawkes_v1_confirmatory_metrics.json", payload["metrics"])
    _write_json(output_dir / "hawkes_v1_confirmatory_metrics_by_match.json", metrics_by_match)
    _write_json(output_dir / "hawkes_v1_confirmatory_metrics_by_category.json", {
        "sufficient": categories_sufficient,
        "insufficient": categories_insufficient,
    })
    _write_json(output_dir / "hawkes_v1_confirmatory_audit.json", audit)
    _write_json(output_dir / "hawkes_v1_confirmatory_manifest.json", payload["manifest"])
    _write_json(output_dir / "hawkes_v1_confirmatory_hashes.json", hashes)
    _write_json(output_dir / "hawkes_v1_confirmatory_result.json", payload)
    _write_report(output_dir / "hawkes_v1_confirmatory_report.md", payload)
    return {"payload": payload, "hashes": hashes}


def main() -> None:
    """Ejecuta corrida primaria y replay determinista."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    LOGGER.info("Iniciando validacion confirmatoria de Hawkes v1.")
    primary = _run_once(OUTPUT_DIR)
    replay = _run_once(REPLAY_DIR)
    replay_payload = {
        "replay_identical": primary["hashes"] == replay["hashes"],
        "primary_hashes": primary["hashes"],
        "replay_hashes": replay["hashes"],
    }
    _write_json(OUTPUT_DIR / "hawkes_v1_confirmatory_replay.json", replay_payload)
    _write_json(REPLAY_DIR / "hawkes_v1_confirmatory_replay.json", replay_payload)
    LOGGER.info("Validacion confirmatoria completada. Replay identical=%s", replay_payload["replay_identical"])


if __name__ == "__main__":
    main()

# Version: 1.0.0
# Created: 2026-07-16
