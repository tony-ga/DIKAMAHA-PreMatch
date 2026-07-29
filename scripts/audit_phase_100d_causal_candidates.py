"""Audita candidatos contextuales sin permitir promoción automática.

Requirements:
    sqlalchemy>=2

Version: 1.0.0
Created: 2026-07-29
"""

from __future__ import annotations

import json
import logging
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.prematch_raw_store import RawResponse  # noqa: E402

LOGGER = logging.getLogger(__name__)
DATABASE = ROOT / "data" / "phase_100" / "raw_responses.sqlite"
OUTPUT = ROOT / "artifacts" / "phase_100_espn_context_enrichment"
CANDIDATE_ENDPOINTS = ("/standings", "/schedule", "/injuries", "/officials")


def _rows() -> list[RawResponse]:
    """Lee el ledger raw-first sin cambiar registros ni llamar a ESPN."""

    engine = create_engine(f"sqlite+pysqlite:///{DATABASE}", future=True)
    statement = select(RawResponse).order_by(RawResponse.fetched_at.asc(), RawResponse.id.asc())
    with Session(engine) as session:
        return list(session.execute(statement).scalars())


def _fixtures(rows: list[RawResponse]) -> dict[str, tuple[RawResponse, set[str]]]:
    """Identifica fixtures y sus equipos desde el snapshot summary publicado."""

    output: dict[str, tuple[RawResponse, set[str]]] = {}
    for row in rows:
        event_id = row.scope_event_id
        if event_id and row.kickoff_ts and row.endpoint.endswith("/summary"):
            output[event_id] = (row, _teams(row.response_json))
    return output


def _candidate_rows(rows: list[RawResponse]) -> dict[str, list[RawResponse]]:
    """Agrupa recursos potenciales por liga sin interpretar sus valores."""

    output: dict[str, list[RawResponse]] = defaultdict(list)
    for row in rows:
        if row.league_slug and any(row.endpoint.endswith(item) for item in CANDIDATE_ENDPOINTS):
            output[row.league_slug].append(row)
    return output


def _teams(payload: dict[str, Any]) -> set[str]:
    """Extrae únicamente IDs de competidores declarados en el summary ESPN."""

    header = payload.get("header") if isinstance(payload.get("header"), dict) else {}
    competitions = header.get("competitions") if isinstance(header.get("competitions"), list) else []
    competition = competitions[0] if competitions and isinstance(competitions[0], dict) else {}
    rows = competition.get("competitors") if isinstance(competition.get("competitors"), list) else []
    return {str(row.get("team", {}).get("id")) for row in rows
            if isinstance(row, dict) and isinstance(row.get("team"), dict) and row["team"].get("id")}


def _manifest(
    fixtures: dict[str, tuple[RawResponse, set[str]]], candidates: dict[str, list[RawResponse]],
) -> list[dict[str, Any]]:
    """Emite referencias causales a snapshots, no features ni probabilidades."""

    output = []
    for event_id, (fixture, teams) in fixtures.items():
        rows = candidates.get(str(fixture.league_slug), [])
        source_ids = [row.id for row in rows if _is_causal(row, fixture, teams)]
        output.append({"event_id": event_id, "league": fixture.league_slug,
                       "kickoff_ts": fixture.kickoff_ts.isoformat(),
                       "source_raw_response_ids": source_ids,
                       "causal_eligible": bool(source_ids)})
    return sorted(output, key=lambda row: (row["kickoff_ts"], row["event_id"]))


def _is_causal(source: RawResponse, fixture: RawResponse, teams: set[str]) -> bool:
    """Exige cutoff y una relación exacta con liga, fixture o equipos del fixture."""

    if source.fetched_at > fixture.kickoff_ts:  # type: ignore[operator]
        return False
    if source.endpoint.endswith("/standings"):
        return source.entity_id == fixture.league_slug
    if source.endpoint.endswith("/officials"):
        return source.scope_event_id == fixture.scope_event_id
    return str(source.entity_id) in teams


def _report(manifest: list[dict[str, Any]]) -> dict[str, Any]:
    """Declara cobertura real y el bloqueo de evaluación sin outcomes sellados."""

    eligible = sum(1 for row in manifest if row["causal_eligible"])
    return {"phase": "100D", "status": "blocked_by_outcome_coverage",
            "fixtures_with_causal_sources": eligible, "fixture_count": len(manifest),
            "outcomes_available": 0, "walk_forward_executed": False,
            "router_modified": False, "model_promoted": False,
            "reason": "phase_100_snapshots_cover_upcoming_fixtures_only"}


def main() -> int:
    """Materializa evidencia y devuelve un estado explícito de gate causal."""

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    rows = _rows()
    manifest = _manifest(_fixtures(rows), _candidate_rows(rows))
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "phase_100d_causal_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    report = _report(manifest)
    (OUTPUT / "phase_100d_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    LOGGER.info("phase100d_audit fixtures=%s causal=%s", report["fixture_count"], report["fixtures_with_causal_sources"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# Version: 1.0.0
# Created: 2026-07-29
