"""Ejecuta ciclos ESPN live raw-first y predicción shadow opcional.

La inferencia sólo se ejecuta cuando existe un prior pre-match congelado para
el evento. Sin prior, el ciclo conserva la captura y falla cerrado para modelo.

Version: 1.0.0
Created: 2026-08-07
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
import time
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.dikamaha_inference import DikamahaInferenceEngine, LiveSnapshotInput  # noqa: E402
from src.espn_live_follower import (  # noqa: E402
    EspnLiveMatchFollower,
    FileLiveRawStore,
    live_inference_payload,
)
from src.espn_prospective_connector import (  # noqa: E402
    EspnConnectorConfig,
    EspnConnectorError,
    EspnProspectiveConnector,
    EspnResourceUnavailable,
)
from src.live_prediction_runtime import load_hawkes_league_policy  # noqa: E402

LOGGER = logging.getLogger(__name__)
CATALOG = ROOT / "docs" / "league_catalog_v1.json"
DEFAULT_HAWKES_POLICY = (
    ROOT / "artifacts" / "phase_114_live_markov_hawkes_v1"
    / "hawkes_league_policy.json"
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fase 114: captura e inferencia live shadow")
    parser.add_argument("--date", default=date.today().strftime("%Y%m%d"))
    parser.add_argument("--league", action="append", dest="leagues")
    parser.add_argument("--max-leagues", type=int)
    parser.add_argument("--cycles", type=int, default=1)
    parser.add_argument("--interval-seconds", type=float, default=30.0)
    parser.add_argument("--league-delay-seconds", type=float, default=0.25)
    parser.add_argument("--raw-root", type=Path, default=ROOT / "data" / "live" / "raw_v1")
    parser.add_argument("--prior-file", type=Path)
    parser.add_argument("--disable-hawkes", action="store_true")
    parser.add_argument("--hawkes-rho", type=float)
    parser.add_argument("--hawkes-rho-goal", type=float)
    parser.add_argument("--hawkes-rho-next-event", type=float)
    parser.add_argument("--hawkes-policy-file", type=Path, default=DEFAULT_HAWKES_POLICY)
    parser.add_argument("--ignore-hawkes-policy", action="store_true")
    return parser


def _enabled_leagues() -> list[str]:
    payload = json.loads(CATALOG.read_text(encoding="utf-8"))
    rows = payload.get("leagues")
    if not isinstance(rows, list):
        raise ValueError("malformed_league_catalog")
    leagues = [str(row["slug"]) for row in rows if isinstance(row, dict) and row.get("enabled") is True]
    if not leagues:
        raise ValueError("empty_enabled_league_catalog")
    return leagues


def _load_priors(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("events")
    if not isinstance(rows, dict):
        raise ValueError("prior_file_requires_events_object")
    output: dict[str, dict[str, Any]] = {}
    for event_id, raw in rows.items():
        if not isinstance(raw, dict):
            raise ValueError("invalid_frozen_prior_row")
        home = float(raw["lambda_base_home"])
        away = float(raw["lambda_base_away"])
        if not all(math.isfinite(value) and value > 0.0 for value in (home, away)):
            raise ValueError("invalid_frozen_prior_intensity")
        if not raw.get("source_hash") or not raw.get("cutoff_ts"):
            raise ValueError("frozen_prior_requires_hash_and_cutoff")
        output[str(event_id)] = {**raw, "lambda_base_home": home, "lambda_base_away": away}
    return output


def _load_hawkes_policy(path: Path | None) -> dict[str, Any] | None:
    """Carga una allowlist seleccionada sólo en validación."""

    if path is None:
        return None
    if not path.exists():
        if path.resolve() == DEFAULT_HAWKES_POLICY.resolve():
            return None
        raise FileNotFoundError(path)
    return load_hawkes_league_policy(path)


def _prediction(
    engine: DikamahaInferenceEngine,
    snapshot: dict[str, Any],
    prior: dict[str, Any] | None,
    *,
    enable_hawkes: bool,
    hawkes_rho: float | None,
    hawkes_rho_goal: float | None,
    hawkes_rho_next_event: float | None,
    hawkes_policy: dict[str, Any] | None,
) -> dict[str, Any]:
    event_id = str(snapshot["provider_event_id"])
    if prior is None:
        return {"provider_event_id": event_id, "status": "no_frozen_prematch_prior"}
    cutoff = datetime.fromisoformat(str(prior["cutoff_ts"]).replace("Z", "+00:00"))
    kickoff = datetime.fromisoformat(str(snapshot["kickoff_ts"]).replace("Z", "+00:00"))
    if cutoff.tzinfo is None or kickoff.tzinfo is None or cutoff >= kickoff:
        return {"provider_event_id": event_id, "status": "invalid_frozen_prematch_cutoff"}
    identity_checks = {
        "provider_event_id": event_id,
        "home_team_id": str(snapshot["home_team_id"]),
        "away_team_id": str(snapshot["away_team_id"]),
        "league_slug": str(snapshot["league_slug"]),
    }
    for key, expected in identity_checks.items():
        if prior.get(key) is not None and str(prior[key]) != expected:
            return {"provider_event_id": event_id, "status": "invalid_frozen_prematch_identity"}
    if prior.get("kickoff_ts") is not None:
        prior_kickoff = datetime.fromisoformat(
            str(prior["kickoff_ts"]).replace("Z", "+00:00"),
        )
        if prior_kickoff.tzinfo is None or prior_kickoff != kickoff:
            return {"provider_event_id": event_id, "status": "invalid_frozen_prematch_identity"}
    admitted = True
    effective_rho_goal = hawkes_rho_goal
    effective_rho_next_event = hawkes_rho_next_event
    if hawkes_policy is not None:
        if any(value is not None for value in (
            hawkes_rho, hawkes_rho_goal, hawkes_rho_next_event,
        )):
            return {"provider_event_id": event_id, "status": "invalid_hawkes_policy_override"}
        admitted = str(snapshot["league_slug"]) in set(hawkes_policy["allowed_leagues"])
        effective_rho_goal = float(hawkes_policy["rho_goal"])
        effective_rho_next_event = float(hawkes_policy["rho_next_event"])
        if not admitted:
            effective_rho_goal = 0.0
            effective_rho_next_event = 0.0
    payload = live_inference_payload(
        snapshot,
        lambda_base_home=float(prior["lambda_base_home"]),
        lambda_base_away=float(prior["lambda_base_away"]),
        enable_hawkes=enable_hawkes,
        hawkes_rho=hawkes_rho,
        hawkes_rho_goal=effective_rho_goal,
        hawkes_rho_next_event=effective_rho_next_event,
        prior_source_hash=str(prior["source_hash"]),
    )
    output = engine.predict_live(LiveSnapshotInput(**payload))
    return {
        "provider_event_id": event_id,
        "status": "shadow_predicted",
        "snapshot_source_hash": snapshot["source_hash"],
        "markov_live": output.experimental_markov_live,
        "hawkes_residual": output.experimental_hawkes_residual,
        "combined_live": output.experimental_combined_live,
        "hawkes_league_admission": {
            "policy_applied": hawkes_policy is not None,
            "admitted": admitted,
            "fallback_exact_markov_live": bool(
                output.experimental_combined_live
                and output.experimental_combined_live.get("fallback_exact_markov_live")
            ),
        },
        "audit": asdict(output.audit),
    }


def run_cycle(
    args: argparse.Namespace,
    priors: dict[str, dict[str, Any]],
    *,
    followers: dict[str, EspnLiveMatchFollower] | None = None,
    raw_store: FileLiveRawStore | None = None,
    engine: DikamahaInferenceEngine | None = None,
    hawkes_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    leagues = list(args.leagues or _enabled_leagues())
    if args.max_leagues is not None:
        if args.max_leagues < 1:
            raise ValueError("max_leagues_must_be_positive")
        leagues = leagues[: args.max_leagues]
    raw_store = raw_store or FileLiveRawStore(args.raw_root)
    engine = engine or DikamahaInferenceEngine()
    followers = followers if followers is not None else {}
    captures: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for index, league in enumerate(leagues):
        try:
            follower = followers.get(league)
            if follower is None:
                connector = EspnProspectiveConnector(EspnConnectorConfig(
                    league=league,
                    cache_ttl_seconds=0,
                    cache_dir=args.raw_root / "_disabled_cache",
                ))
                follower = EspnLiveMatchFollower(connector, raw_store)
                followers[league] = follower
            snapshots = follower.poll_once(args.date)
            errors.extend({"league_slug": league, **row} for row in follower.last_errors)
            for snapshot in snapshots:
                try:
                    captures.append(_prediction(
                        engine,
                        snapshot,
                        priors.get(str(snapshot["provider_event_id"])),
                        enable_hawkes=not args.disable_hawkes,
                        hawkes_rho=args.hawkes_rho,
                        hawkes_rho_goal=args.hawkes_rho_goal,
                        hawkes_rho_next_event=args.hawkes_rho_next_event,
                        hawkes_policy=hawkes_policy,
                    ))
                except (ValueError, OverflowError, FloatingPointError) as exc:
                    errors.append({
                        "league_slug": league,
                        "provider_event_id": str(snapshot.get("provider_event_id") or "unknown"),
                        "error": str(exc)[:160],
                    })
        except (EspnConnectorError, EspnResourceUnavailable, ValueError, OSError) as exc:
            errors.append({"league_slug": league, "error": str(exc)[:160]})
        if index + 1 < len(leagues) and args.league_delay_seconds > 0.0:
            time.sleep(args.league_delay_seconds)
    return {
        "phase": 114,
        "status": "shadow_capture_cycle",
        "date": args.date,
        "league_count": len(leagues),
        "active_match_count": len(captures),
        "predicted_count": sum(row["status"] == "shadow_predicted" for row in captures),
        "captures": captures,
        "errors": errors,
        "official_router_modified": False,
    }


def main() -> int:
    args = _parser().parse_args()
    if len(args.date) != 8 or not args.date.isdigit():
        raise ValueError("date_must_be_YYYYMMDD")
    if args.cycles < 1 or args.interval_seconds < 0.0 or args.league_delay_seconds < 0.0:
        raise ValueError("invalid_cycle_configuration")
    for value in (
        args.hawkes_rho, args.hawkes_rho_goal, args.hawkes_rho_next_event,
    ):
        if value is not None and not 0.0 <= value <= 1.0:
            raise ValueError("hawkes_rho_out_of_range")
    priors = _load_priors(args.prior_file)
    hawkes_policy = (
        None if args.ignore_hawkes_policy
        else _load_hawkes_policy(args.hawkes_policy_file)
    )
    raw_store = FileLiveRawStore(args.raw_root)
    engine = DikamahaInferenceEngine()
    followers: dict[str, EspnLiveMatchFollower] = {}
    for cycle in range(args.cycles):
        print(json.dumps(run_cycle(
            args, priors, followers=followers, raw_store=raw_store, engine=engine,
            hawkes_policy=hawkes_policy,
        ), sort_keys=True, separators=(",", ":")))
        if cycle + 1 < args.cycles and args.interval_seconds > 0.0:
            time.sleep(args.interval_seconds)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
