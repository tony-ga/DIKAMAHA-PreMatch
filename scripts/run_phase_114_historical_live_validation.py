"""Ejecuta el gate histórico read-only de Markov Live + Hawkes."""

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

from src.evaluate_live_markov_hawkes_historical import (  # noqa: E402
    HistoricalLiveConfig,
    evaluate_historical_live,
    write_historical_artifacts,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fase 114: validación histórica live")
    parser.add_argument(
        "--output", type=Path,
        default=ROOT / "artifacts" / "phase_114_live_markov_hawkes_v1",
    )
    parser.add_argument("--bootstrap-replicates", type=int, default=2000)
    args = parser.parse_args()
    if args.bootstrap_replicates < 100:
        raise ValueError("bootstrap_replicates_must_be_at_least_100")
    load_dotenv(ROOT / ".env")
    database_url = os.getenv("DATABASE_URL", "").strip().strip("\"'")
    if not database_url:
        raise RuntimeError("DATABASE_URL_missing")
    config = replace(
        HistoricalLiveConfig(), bootstrap_replicates=args.bootstrap_replicates,
    )
    result = evaluate_historical_live(database_url, config)
    write_historical_artifacts(result, args.output)
    print(json.dumps({
        "phase": 114,
        "classification": result["classification"],
        "coverage": result["coverage"],
        "selection": {
            "markov_state_scale": result["selection"]["selected_markov_state_scale"],
            "hawkes_rho_goal": result["selection"]["selected_hawkes_rho_goal"],
            "hawkes_rho_next_event": result["selection"]["selected_hawkes_rho_next_event"],
        },
        "gates": result["gates"],
        "replay_hash": result["replay_hash"],
    }, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
