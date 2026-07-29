"""Congela una cohorte futura independiente antes de calibrar Markov.

Lee sólo fixtures futuros ya verificados por el flujo universal. No solicita
play-by-play, no incorpora resultados y no modifica snapshots ni router.

Version: 1.0.0
Created: 2026-07-27
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "artifacts/phase_56_multileague_upcoming_flow_v1/audit.json"
OUTPUT = ROOT / "artifacts/phase_62_independent_cohort_lock_v1"
LOGGER = logging.getLogger(__name__)


def _fixture(result: dict[str, Any]) -> dict[str, Any] | None:
    """Extrae sólo identidad y cutoff de un resultado HTTP 200 futuro."""

    fixture = result.get("fixture")
    if result.get("status_code") != 200 or not isinstance(fixture, dict):
        return None
    return {key: fixture[key] for key in ("match_id", "competition_id", "league_slug", "kickoff_ts", "home_team_id", "away_team_id", "provider_status")}


def run() -> dict[str, Any]:
    """Valida y publica la cohorte bloqueada sin observar targets."""

    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)
    fixtures = [_fixture(result) for result in source.get("results", [])]
    locked = [row for row in fixtures if row and datetime.fromisoformat(str(row["kickoff_ts"]).replace("Z", "+00:00")) > now]
    locked = sorted(locked, key=lambda row: (str(row["kickoff_ts"]), int(row["match_id"])))
    ids = [int(row["match_id"]) for row in locked]
    result = {"classification": "independent_cohort_locked" if locked and len(ids) == len(set(ids)) else "independent_cohort_lock_rejected", "locked_at_utc": now.isoformat(), "source": str(SOURCE.relative_to(ROOT)), "fixture_count": len(locked), "fixture_ids": ids, "fixtures": locked, "results_observed": False, "plays_requested": False, "target_data_used": False, "snapshot_modified": False, "router_modified": False, "cohort_hash": hashlib.sha256(json.dumps(locked, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "cohort.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    (OUTPUT / "final_report.md").write_text("\n".join(["# Fase 62 — cohorte independiente", "", f"**Clasificación:** `{result['classification']}`", "", f"- fixtures bloqueados: `{len(locked)}`", f"- IDs únicos: `{len(set(ids))}`", "- resultados observados: `False`", "- play-by-play solicitado: `False`", "- snapshot modificado: `False`", "- router modificado: `False`", "", "La cohorte queda congelada antes del kickoff para evaluación confirmatoria posterior."]) + "\n", encoding="utf-8")
    LOGGER.info("Cohorte independiente: %s", result["classification"])
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    raise SystemExit(0 if run()["classification"] == "independent_cohort_locked" else 1)

# Version: 1.0.0
# Created: 2026-07-27
