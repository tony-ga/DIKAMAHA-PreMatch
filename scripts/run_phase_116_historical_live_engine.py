"""Ejecuta el replay histórico read-only de Fase 116."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluate_live_probability_engine_historical import (  # noqa: E402
    HistoricalLiveEngineConfig,
    evaluate_historical_live_engine,
    write_live_engine_artifacts,
)


def _hawkes_policy(path: Path) -> frozenset[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return frozenset(str(value) for value in payload.get("allowed_leagues", []))


def main() -> int:
    parser = argparse.ArgumentParser(description="Fase 116: replay motor live")
    parser.add_argument(
        "--output", type=Path,
        default=ROOT / "artifacts" / "phase_116_live_probability_engine_v1",
    )
    parser.add_argument("--bootstrap-replicates", type=int, default=2000)
    parser.add_argument("--snapshot-minutes", default="15,30,45,60,75")
    parser.add_argument(
        "--hawkes-policy", type=Path,
        default=(
            ROOT / "artifacts" / "phase_114_live_markov_hawkes_v1"
            / "hawkes_league_policy.json"
        ),
    )
    args = parser.parse_args()
    minutes = tuple(int(value) for value in args.snapshot_minutes.split(","))
    if not minutes or any(value <= 0 or value >= 90 for value in minutes):
        raise ValueError("snapshot_minutes_invalid")
    if args.bootstrap_replicates < 100:
        raise ValueError("bootstrap_replicates_must_be_at_least_100")
    load_dotenv(ROOT / ".env")
    database_url = os.getenv("DATABASE_URL", "").strip().strip("\"'")
    if not database_url:
        raise RuntimeError("DATABASE_URL_missing")
    config = replace(
        HistoricalLiveEngineConfig(),
        snapshot_minutes=minutes,
        bootstrap_replicates=args.bootstrap_replicates,
    )
    result = evaluate_historical_live_engine(
        database_url, config,
        allowed_hawkes_leagues=_hawkes_policy(args.hawkes_policy),
    )
    write_live_engine_artifacts(result, args.output)
    print(json.dumps({
        "phase": 116,
        "classification": result["classification"],
        "coverage": result["coverage"],
        "gates": result["gates"],
        "replay_hash": result["replay_hash"],
    }, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
