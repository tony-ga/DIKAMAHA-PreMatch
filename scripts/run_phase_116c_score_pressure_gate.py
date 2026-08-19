"""Gate histórico de la forma temporal de presión (`DEC-216`, Fase 116C).

Replica el protocolo de `evaluate_historical_live_engine` pero evaluando el
mismo corpus con dos configuraciones del motor -la vigente `linear_v1` y la
calibrada `ramp_v2`- para poder compararlas sobre exactamente los mismos
partidos y snapshots.

El gate vinculante es el técnico de Fase 116 (`DEC-155`): causalidad,
normalización, conservación y ausencia de escrituras. El log-loss es
diagnóstico y se reporta con IC95% por partido completo; sólo una
**degradación confirmada** -intervalo enteramente desfavorable- detiene la
activación.

La selección se mira en `validation`; `confirmation` se reporta una sola vez
y no participa en ninguna elección.

Uso:
    python -m scripts.run_phase_116c_score_pressure_gate

Version: 1.0.0
Created: 2026-08-18
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluate_live_markov_hawkes_historical import (  # noqa: E402
    HistoricalLiveConfig,
    _next_event_target,
    _one_x_two_target,
    _prediction_losses,
    _utc,
    aggregate_metrics,
    per_match_metrics,
    read_historical_database,
    temporal_partition,
    walkforward_priors,
)
from src.evaluate_live_probability_engine_historical import (  # noqa: E402
    HistoricalLiveEngineConfig,
    _stable_hash,
)
from src.hawkes_live_v2 import HawkesLiveConfig  # noqa: E402
from src.live_probability_engine_v1 import (  # noqa: E402
    LiveProbabilityEngineConfig,
    LiveProbabilityEngineV1,
)
from src.markov_live_v1 import MarkovLiveInput, MarkovLiveV1  # noqa: E402

CALIBRATION = ROOT / "artifacts/phase_116a_score_pressure_calibration/calibration.json"
HAWKES_POLICY = (
    ROOT / "artifacts/phase_114_live_markov_hawkes_v1/hawkes_league_policy.json")
OUTPUT = ROOT / "artifacts/phase_116c_score_pressure_gate"


def _replay(
    matches: Sequence[dict[str, Any]],
    events_by_match: dict[int, list[dict[str, Any]]],
    priors: dict[int, dict[str, Any]],
    config: HistoricalLiveEngineConfig,
    allowed_hawkes_leagues: frozenset[str],
    engine_config: LiveProbabilityEngineConfig,
) -> list[dict[str, Any]]:
    """Replay idéntico al oficial, con la configuración del motor inyectada."""

    markov = MarkovLiveV1()
    engine = LiveProbabilityEngineV1(engine_config)
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
                float(row["match_clock_seconds"]), str(row["event_id"])),
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
            request = MarkovLiveInput(
                match_id=match_id, home_team_id=home_id, away_team_id=away_id,
                kickoff_ts=kickoff.isoformat(),
                snapshot_ts=(kickoff + timedelta(seconds=clock)).isoformat(),
                match_clock_seconds=clock,
                period=1 if minute < 45 else 2,
                score_home=score_home, score_away=score_away,
                lambda_base_home=float(prior["lambda_base_home"]),
                lambda_base_away=float(prior["lambda_base_away"]),
                events=observed, league_slug=league,
                source_hash=_stable_hash({
                    "prior": prior["source_hash"], "minute": minute,
                    "events": [e.get("event_id") for e in observed],
                }),
            )
            baseline = markov.predict(request)
            rho_goal = (
                config.hawkes_rho_goal if league in allowed_hawkes_leagues
                else 0.0)
            result = engine.predict(
                request, baseline,
                hawkes_config=HawkesLiveConfig(
                    rho=0.0, rho_goal=rho_goal,
                    rho_next_event=config.hawkes_rho_next_event),
            )
            official = result["official_live_prediction"]
            targets = {
                "one_x_two": _one_x_two_target(
                    int(match["home_score"]), int(match["away_score"])),
                "over_2_5": (
                    int(match["home_score"]) + int(match["away_score"]) > 2),
                "btts": (
                    int(match["home_score"]) > 0
                    and int(match["away_score"]) > 0),
                "next_event": _next_event_target(
                    events, clock, markov.config.horizon_minutes,
                    home_id, away_id),
            }
            rows.append({
                "match_id": match_id, "league_slug": league,
                "kickoff_ts": kickoff.isoformat(), "snapshot_minute": minute,
                "events_observed": len(observed), "targets": targets,
                "losses": {"engine": _prediction_losses(official, targets)},
                "output_hash": result["output_hash"],
                "audit_passed": bool(
                    result["live_probability_engine"]["audit"]["passed"]),
                "markets_normalized": bool(abs(
                    official["markets"]["probability_home"]
                    + official["markets"]["probability_draw"]
                    + official["markets"]["probability_away"] - 1.0) < 1e-9),
            })
    return rows


def _paired_bootstrap(
    deltas: np.ndarray, replicates: int, seed: int,
) -> dict[str, Any]:
    """IC95% percentil del delta medio, remuestreando partidos completos."""

    generator = np.random.default_rng(seed)
    means = np.array([
        float(np.mean(generator.choice(deltas, size=len(deltas), replace=True)))
        for _ in range(replicates)
    ])
    low, high = float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))
    return {
        "mean": float(np.mean(deltas)), "ci_low": low, "ci_high": high,
        "crosses_zero": bool(low <= 0.0 <= high),
        "verdict": (
            "indistinguible" if low <= 0.0 <= high
            else ("mejora confirmada" if np.mean(deltas) > 0
                  else "degradación confirmada")),
    }


def _compare(
    baseline_rows: Sequence[dict[str, Any]],
    candidate_rows: Sequence[dict[str, Any]],
    base_cfg: HistoricalLiveConfig,
    replicates: int,
    seed: int,
) -> dict[str, Any]:
    """Compara dos replays sobre los mismos partidos, por partido completo."""

    base_per_match = {
        row["match_id"]: row for row in per_match_metrics(
            [{**r, "losses": {"engine": r["losses"]["engine"]}} for r in baseline_rows],
            base_cfg)
    }
    candidate_per_match = {
        row["match_id"]: row for row in per_match_metrics(
            [{**r, "losses": {"engine": r["losses"]["engine"]}} for r in candidate_rows],
            base_cfg)
    }
    shared = sorted(set(base_per_match) & set(candidate_per_match))
    output: dict[str, Any] = {"matches": len(shared)}
    for metric in ("one_x_two_log_loss", "goal_market_log_loss",
                   "next_event_log_loss", "objective"):
        deltas = np.array([
            float(base_per_match[m]["models"]["engine"][metric])
            - float(candidate_per_match[m]["models"]["engine"][metric])
            for m in shared
        ])
        output[metric] = _paired_bootstrap(deltas, replicates, seed)
    return output


def main() -> int:
    """Ejecuta el gate y publica la evidencia."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--bootstrap-replicates", type=int, default=2000)
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    database_url = os.getenv("DATABASE_URL", "").strip().strip("\"'")
    if not database_url:
        raise RuntimeError("DATABASE_URL_missing")

    calibrated = json.loads(CALIBRATION.read_text(encoding="utf-8"))
    parameters = calibrated["fitted_parameters"]
    print(f"parámetros calibrados: {parameters}", flush=True)

    allowed = frozenset(
        str(v) for v in json.loads(
            HAWKES_POLICY.read_text(encoding="utf-8")).get("allowed_leagues", []))

    cfg = HistoricalLiveEngineConfig()
    base_cfg = HistoricalLiveConfig(
        snapshot_minutes=cfg.snapshot_minutes,
        bootstrap_replicates=args.bootstrap_replicates)

    print("leyendo base histórica (solo lectura)...", flush=True)
    matches, events, database_audit = read_historical_database(database_url)
    priors, prior_audit = walkforward_priors(matches, base_cfg)
    eligible = [r for r in matches if int(r["provider_match_id"]) in priors]
    blocks, partition = temporal_partition(eligible, base_cfg)
    leagues = len({str(r["league_slug"]) for r in eligible})
    print(f"  elegibles={len(eligible)} ligas={leagues}", flush=True)

    engine_configs = {
        "linear_v1": LiveProbabilityEngineConfig(),
        "ramp_v2": LiveProbabilityEngineConfig(**parameters),
    }

    splits: dict[str, Any] = {}
    for split in ("validation", "confirmation"):
        split_matches = [
            r for r in eligible
            if blocks[int(r["provider_match_id"])] == split]
        print(f"\n--- {split}: {len(split_matches)} partidos ---", flush=True)
        replays = {}
        for name, engine_config in engine_configs.items():
            print(f"  replay {name}...", flush=True)
            replays[name] = _replay(
                split_matches, events, priors, cfg, allowed, engine_config)
        comparison = _compare(
            replays["linear_v1"], replays["ramp_v2"], base_cfg,
            args.bootstrap_replicates, cfg.bootstrap_seed)
        splits[split] = {
            "snapshots": {k: len(v) for k, v in replays.items()},
            "aggregate": {
                name: aggregate_metrics(per_match_metrics(rows, base_cfg))
                for name, rows in replays.items()
            },
            "candidate_minus_baseline": comparison,
            "technical": {
                name: {
                    "audits_passed": all(r["audit_passed"] for r in rows),
                    "markets_normalized": all(
                        r["markets_normalized"] for r in rows),
                    "replay_hash": _stable_hash([r["output_hash"] for r in rows]),
                }
                for name, rows in replays.items()
            },
        }
        for metric in ("one_x_two_log_loss", "objective"):
            stats = comparison[metric]
            print(f"    {metric}: {stats['mean']:+.6f} "
                  f"IC95%[{stats['ci_low']:+.6f},{stats['ci_high']:+.6f}] "
                  f"-> {stats['verdict']}", flush=True)

    technical_ok = all(
        block["technical"][name]["audits_passed"]
        and block["technical"][name]["markets_normalized"]
        for block in splits.values() for name in engine_configs
    )
    confirmed_degradation = any(
        splits[split]["candidate_minus_baseline"][metric]["verdict"]
        == "degradación confirmada"
        for split in splits for metric in ("one_x_two_log_loss", "objective")
    )
    promotable = bool(technical_ok and not confirmed_degradation)

    payload = {
        "classification": "ready_for_activation" if promotable else "rejected",
        "protocol": "validation_selects_confirmation_reports_once",
        "unit": "complete_match",
        "calibrated_parameters": parameters,
        "coverage": {
            "eligible_matches": len(eligible), "represented_leagues": leagues,
            "minimum_matches": cfg.minimum_historical_matches,
            "minimum_leagues": cfg.minimum_historical_leagues,
        },
        "database_audit": database_audit,
        "prior_audit": prior_audit,
        "partition": partition,
        "splits": splits,
        "gates": {
            "database_read_only": bool(database_audit["read_only"]),
            "database_unchanged": bool(database_audit["counts_identical"]),
            "priors_strictly_prior": bool(prior_audit["strictly_prior"]),
            "coverage_matches": len(eligible) >= cfg.minimum_historical_matches,
            "coverage_leagues": leagues >= cfg.minimum_historical_leagues,
            "technical_audits_passed": technical_ok,
            "no_confirmed_degradation": not confirmed_degradation,
            "promotable": promotable,
        },
    }

    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "gate.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str),
        encoding="utf-8")
    print(f"\nclasificación: {payload['classification']}", flush=True)
    print(f"artefacto: {args.output / 'gate.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
