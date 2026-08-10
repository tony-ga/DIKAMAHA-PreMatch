"""Replay histórico causal para ``live_probability_engine_v1``.

La unidad estadística es siempre el partido completo. PostgreSQL se abre a
través del lector read-only de Fase 114 y ningún snapshot raw se persiste.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from src.evaluate_live_markov_hawkes_historical import (
    HistoricalLiveConfig,
    _next_event_target,
    _one_x_two_target,
    _prediction_losses,
    _utc,
    aggregate_metrics,
    metrics_by_league,
    per_match_metrics,
    read_historical_database,
    temporal_partition,
    walkforward_priors,
)
from src.hawkes_live_v2 import HawkesLiveConfig
from src.live_probability_engine_v1 import (
    LiveProbabilityEngineConfig,
    LiveProbabilityEngineV1,
)
from src.markov_live_v1 import MarkovLiveInput, MarkovLiveV1


@dataclass(frozen=True, slots=True)
class HistoricalLiveEngineConfig:
    """Configuración reproducible del gate histórico Fase 116."""

    version: str = "phase_116_historical_live_engine_v1"
    snapshot_minutes: tuple[int, ...] = (15, 30, 45, 60, 75)
    bootstrap_replicates: int = 2000
    bootstrap_seed: int = 11607
    minimum_historical_matches: int = 5000
    minimum_historical_leagues: int = 20
    hawkes_rho_goal: float = 1.0
    hawkes_rho_next_event: float = 0.0


def _stable_hash(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str,
        ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _replay_rows(
    matches: Sequence[dict[str, Any]],
    events_by_match: dict[int, list[dict[str, Any]]],
    priors: dict[int, dict[str, Any]],
    config: HistoricalLiveEngineConfig,
    allowed_hawkes_leagues: frozenset[str],
) -> list[dict[str, Any]]:
    markov = MarkovLiveV1()
    engine = LiveProbabilityEngineV1()
    rows: list[dict[str, Any]] = []
    for match in sorted(
        matches,
        key=lambda row: (_utc(row["kickoff_ts"]), int(row["provider_match_id"])),
    ):
        match_id = int(match["provider_match_id"])
        prior = priors.get(match_id)
        if prior is None:
            continue
        kickoff = _utc(match["kickoff_ts"])
        home_id = int(match["home_team_id"])
        away_id = int(match["away_team_id"])
        league = str(match["league_slug"])
        events = sorted(
            events_by_match.get(match_id, []),
            key=lambda row: (
                float(row["match_clock_seconds"]), str(row["event_id"]),
            ),
        )
        for minute in config.snapshot_minutes:
            clock = float(minute * 60)
            observed = tuple(
                event for event in events
                if float(event["match_clock_seconds"]) <= clock
            )
            goals = [
                event for event in observed
                if event["event_type"] in {"goal", "penalty_scored"}
            ]
            score_home = sum(event.get("team_id") == home_id for event in goals)
            score_away = sum(event.get("team_id") == away_id for event in goals)
            source_hash = _stable_hash({
                "prior": prior["source_hash"], "minute": minute,
                "events": [event.get("event_id") for event in observed],
            })
            request = MarkovLiveInput(
                match_id=match_id,
                home_team_id=home_id,
                away_team_id=away_id,
                kickoff_ts=kickoff.isoformat(),
                snapshot_ts=(kickoff + timedelta(seconds=clock)).isoformat(),
                match_clock_seconds=clock,
                period=1 if minute < 45 else 2,
                score_home=score_home,
                score_away=score_away,
                lambda_base_home=float(prior["lambda_base_home"]),
                lambda_base_away=float(prior["lambda_base_away"]),
                events=observed,
                league_slug=league,
                source_hash=source_hash,
            )
            baseline = markov.predict(request)
            rho_goal = (
                config.hawkes_rho_goal if league in allowed_hawkes_leagues
                else 0.0
            )
            result = engine.predict(
                request, baseline,
                hawkes_config=HawkesLiveConfig(
                    rho=0.0,
                    rho_goal=rho_goal,
                    rho_next_event=config.hawkes_rho_next_event,
                ),
            )
            official = result["official_live_prediction"]
            targets = {
                "one_x_two": _one_x_two_target(
                    int(match["home_score"]), int(match["away_score"]),
                ),
                "over_2_5": (
                    int(match["home_score"]) + int(match["away_score"]) > 2
                ),
                "btts": (
                    int(match["home_score"]) > 0
                    and int(match["away_score"]) > 0
                ),
                "next_event": _next_event_target(
                    events, clock, markov.config.horizon_minutes,
                    home_id, away_id,
                ),
            }
            rows.append({
                "match_id": match_id,
                "league_slug": league,
                "kickoff_ts": kickoff.isoformat(),
                "snapshot_minute": minute,
                "events_observed": len(observed),
                "targets": targets,
                "losses": {
                    "markov": _prediction_losses(baseline, targets),
                    "engine": _prediction_losses(official, targets),
                },
                "output_hashes": {
                    "markov": baseline["output_hash"],
                    "engine": result["output_hash"],
                },
                "audit_passed": bool(
                    result["live_probability_engine"]["audit"]["passed"]
                ),
            })
    return rows


def _bootstrap_engine_delta(
    per_match: Sequence[dict[str, Any]],
    config: HistoricalLiveEngineConfig,
) -> dict[str, Any]:
    rng = np.random.default_rng(config.bootstrap_seed)
    output: dict[str, Any] = {}
    for metric in (
        "one_x_two_log_loss", "goal_market_log_loss",
        "next_event_log_loss", "objective",
    ):
        values = np.asarray([
            float(row["models"]["engine"][metric])
            - float(row["models"]["markov"][metric])
            for row in per_match
        ])
        samples = rng.choice(
            values,
            size=(config.bootstrap_replicates, len(values)),
            replace=True,
        ).mean(axis=1)
        output[metric] = {
            "mean_delta": float(values.mean()),
            "ci_95": [
                float(np.quantile(samples, 0.025)),
                float(np.quantile(samples, 0.975)),
            ],
            "probability_improvement": float(np.mean(samples < 0.0)),
            "replicates": config.bootstrap_replicates,
            "unit": "complete_match",
        }
    return output


def evaluate_historical_live_engine(
    database_url: str,
    config: HistoricalLiveEngineConfig | None = None,
    *,
    allowed_hawkes_leagues: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Evalúa el motor fijo sin seleccionar con datos de confirmación."""

    cfg = config or HistoricalLiveEngineConfig()
    base_cfg = HistoricalLiveConfig(
        snapshot_minutes=cfg.snapshot_minutes,
        bootstrap_replicates=cfg.bootstrap_replicates,
    )
    matches, events, database_audit = read_historical_database(database_url)
    priors, prior_audit = walkforward_priors(matches, base_cfg)
    eligible = [
        row for row in matches if int(row["provider_match_id"]) in priors
    ]
    blocks, partition = temporal_partition(eligible, base_cfg)
    split_results: dict[str, Any] = {}
    all_audits_passed = True
    replay_hashes: dict[str, str] = {}
    for split in ("development", "validation", "confirmation"):
        split_matches = [
            row for row in eligible
            if blocks[int(row["provider_match_id"])] == split
        ]
        rows = _replay_rows(
            split_matches, events, priors, cfg, allowed_hawkes_leagues,
        )
        complete_match = per_match_metrics(rows, base_cfg)
        all_audits_passed = all_audits_passed and all(
            row["audit_passed"] for row in rows
        )
        split_results[split] = {
            "snapshot_count": len(rows),
            "aggregate": aggregate_metrics(complete_match),
            "by_league": metrics_by_league(complete_match),
            "bootstrap_engine_minus_markov": _bootstrap_engine_delta(
                complete_match, cfg,
            ),
        }
        replay_hashes[split] = _stable_hash([
            row["output_hashes"] for row in rows
        ])
    represented_leagues = len({str(row["league_slug"]) for row in eligible})
    gates = {
        "database_read_only": bool(database_audit["read_only"]),
        "database_unchanged": bool(database_audit["counts_identical"]),
        "priors_strictly_prior": bool(prior_audit["strictly_prior"]),
        "atomic_kickoffs": bool(prior_audit["atomic_same_kickoff_updates"]),
        "snapshot_audits_passed": all_audits_passed,
        "minimum_historical_matches": (
            len(eligible) >= cfg.minimum_historical_matches
        ),
        "minimum_historical_leagues": (
            represented_leagues >= cfg.minimum_historical_leagues
        ),
        "provider_probabilities_used": False,
        "provider_odds_used": False,
        "pre_match_router_modified": False,
    }
    result = {
        "phase": 116,
        "classification": "official_immediate_with_historical_evaluation",
        "config": asdict(cfg),
        "engine_config": asdict(LiveProbabilityEngineConfig()),
        "database_audit": database_audit,
        "prior_audit": prior_audit,
        "partition": partition,
        "coverage": {
            "eligible_matches": len(eligible),
            "represented_leagues": represented_leagues,
            "snapshot_minutes": list(cfg.snapshot_minutes),
            "hawkes_admitted_leagues": len(allowed_hawkes_leagues),
        },
        "splits": split_results,
        "gates": gates,
        "replay_hashes": replay_hashes,
    }
    result["replay_hash"] = _stable_hash(result)
    return result


def write_live_engine_artifacts(result: dict[str, Any], output: Path) -> None:
    """Publica evidencia agregada y hashes sin copiar datos de partidos."""

    output.mkdir(parents=True, exist_ok=True)
    payloads = {
        "config.json": {
            "evaluation": result["config"],
            "engine": result["engine_config"],
        },
        "input_manifest.json": {
            "database_audit": result["database_audit"],
            "prior_audit": result["prior_audit"],
            "partition": result["partition"],
        },
        "coverage.json": result["coverage"],
        "audit.json": {
            "classification": result["classification"],
            "gates": result["gates"],
            "replay_hash": result["replay_hash"],
        },
        "metrics.json": result["splits"],
    }
    for name, payload in payloads.items():
        (output / name).write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
    confirmation = result["splits"]["confirmation"]
    delta = confirmation["bootstrap_engine_minus_markov"]["objective"]
    report = [
        "# Fase 116 — replay histórico del motor live",
        "",
        f"Clasificación: `{result['classification']}`.",
        "",
        f"- partidos elegibles: `{result['coverage']['eligible_matches']}`;",
        f"- ligas representadas: `{result['coverage']['represented_leagues']}`;",
        f"- snapshots de confirmación: `{confirmation['snapshot_count']}`;",
        f"- delta objetivo motor vs Markov: `{delta['mean_delta']}`;",
        f"- IC95 agrupado por partido: `{delta['ci_95']}`;",
        f"- hash del replay: `{result['replay_hash']}`.",
        "",
        "La activación inicial fue fijada como inmediata. Estas métricas son",
        "evidencia estadística y no una afirmación de superioridad económica.",
    ]
    (output / "final_report.md").write_text(
        "\n".join(report) + "\n", encoding="utf-8",
    )
    hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(output.iterdir()) if path.is_file()
    }
    (output / "hashes.json").write_text(
        json.dumps(hashes, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


__all__ = [
    "HistoricalLiveEngineConfig", "evaluate_historical_live_engine",
    "write_live_engine_artifacts",
]
