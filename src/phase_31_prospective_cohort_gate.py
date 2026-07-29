"""Gate read-only para detectar una cohorte prospectiva independiente.

Version: 1.0.0
Created: 2026-07-26
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.espn_phase_7_15_r5 import _model_reused_ids
from src.postgres_readonly_staging import ReadonlyDatabase, counts_identical, database_error_types, detect_capabilities, sanitize_error

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/phase_31_prospective_cohort_gate"
MULTILEAGUE_WINDOWS = ROOT / "artifacts/phase_38_multileague_event_windows_v1/event_windows.json"
SCHEMA = "prospective_staging_v2"
CUTOFF = "2025-10-26T15:15:00+00:00"
FINAL_STATUSES = {"post", "final", "finished", "completed", "full_time"}
LOGGER = logging.getLogger(__name__)


def _utc(value: Any) -> datetime:
    """Normaliza una fecha a UTC."""

    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _database_url() -> str:
    """Obtiene DATABASE_URL sin comillas ni retornos de carro."""

    value = os.getenv("DATABASE_URL", "").strip().strip("\"'")
    if not value:
        raise ValueError("missing_database_url")
    return value


def _read() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Lee partidos y eventos mediante SELECT-only."""

    database = ReadonlyDatabase(_database_url())
    with database.session() as session:
        before = {"matches": int(session.scalar(f"SELECT COUNT(*) FROM {SCHEMA}.matches")), "events": int(session.scalar(f"SELECT COUNT(*) FROM {SCHEMA}.events"))}
        rows = session.rows(f"""
            SELECT m.provider_match_id::bigint AS match_id, m.kickoff_ts,
                   m.home_provider_team_id AS home_team_id,
                   m.away_provider_team_id AS away_team_id,
                   m.provider_status, m.complete, m.home_score, m.away_score,
                   (SELECT COUNT(*) FROM {SCHEMA}.events e
                    WHERE e.provider='espn' AND e.provider_match_id=m.provider_match_id) AS event_count
            FROM {SCHEMA}.matches m WHERE m.provider='espn'
            ORDER BY m.kickoff_ts, m.provider_match_id
        """)
        after = {"matches": int(session.scalar(f"SELECT COUNT(*) FROM {SCHEMA}.matches")), "events": int(session.scalar(f"SELECT COUNT(*) FROM {SCHEMA}.events"))}
    audit = {"source": SCHEMA, "before": before, "after": after, "identical": counts_identical(before, after), "connection_closed": database.closed, "select_only": all(item.startswith("SELECT ") for item in database.statements), "write_statements": 0, "statements": database.statements}
    return rows, audit


def _eligible(rows: list[dict[str, Any]], reused: set[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Separa candidatos independientes de registros excluidos."""

    candidates, rejected = [], []
    for row in rows:
        match_id = str(int(row["match_id"]))
        reasons = []
        if _utc(row["kickoff_ts"]) <= _utc(CUTOFF):
            reasons.append("before_or_at_cutoff")
        if match_id in reused:
            reasons.append("model_reuse")
        if not row["complete"] or str(row["provider_status"]).lower() not in FINAL_STATUSES:
            reasons.append("not_final")
        if row["home_score"] is None or row["away_score"] is None:
            reasons.append("missing_final_score")
        if row["home_team_id"] is None or row["away_team_id"] is None or row["home_team_id"] == row["away_team_id"]:
            reasons.append("invalid_team_identity")
        if int(row["event_count"]) == 0:
            reasons.append("missing_events")
        item = {
            "match_id": int(row["match_id"]),
            "kickoff_ts": _utc(row["kickoff_ts"]).isoformat(),
            "home_team_id": int(row["home_team_id"]) if row["home_team_id"] is not None else None,
            "away_team_id": int(row["away_team_id"]) if row["away_team_id"] is not None else None,
            "event_count": int(row["event_count"]),
            "complete": bool(row["complete"]),
            "reasons": reasons,
        }
        (rejected if reasons else candidates).append(item)
    return candidates, rejected


def _classification(candidate_count: int, minimum: int = 30) -> str:
    """Clasifica cobertura sin evaluar desempeño."""

    if candidate_count >= minimum:
        return "cohort_ready_for_confirmatory_evaluation"
    return "waiting_for_new_independent_cohort"


def _reused_ids() -> tuple[set[str], list[str]]:
    """Une el catálogo histórico con todos los IDs del corpus multi-liga."""

    reused = set(_model_reused_ids())
    sources = ["official_model_catalog"]
    if MULTILEAGUE_WINDOWS.exists():
        rows = json.loads(MULTILEAGUE_WINDOWS.read_text(encoding="utf-8"))
        reused.update(str(int(row["match_id"])) for row in rows if row.get("match_id") is not None)
        sources.append(str(MULTILEAGUE_WINDOWS.relative_to(ROOT)))
    return reused, sources


def _hash(path: Path) -> str:
    """Calcula SHA-256 de un archivo."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, payload: Any) -> None:
    """Escribe JSON determinista y atómico."""

    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temporary.replace(path)


def run() -> dict[str, Any]:
    """Ejecuta el gate y publica candidatos sin métricas."""

    rows, database = _read()
    reused, reuse_sources = _reused_ids()
    candidates, rejected = _eligible(rows, reused)
    result = {"classification": _classification(len(candidates)), "cutoff_ts": CUTOFF, "source": SCHEMA, "staging_match_count": len(rows), "candidate_count": len(candidates), "candidate_matches": candidates, "rejected_count": len(rejected), "rejected_matches": rejected, "reuse_catalog_count": len(reused), "reuse_catalog_sources": reuse_sources, "evaluation_performed": False, "bootstrap_performed": False, "router_modified": False, "markets_promoted": False, "database": database}
    config = {"version": "phase_31_prospective_cohort_gate_v2", "minimum_complete_matches": 30, "unit": "complete_match", "read_only": True, "multi_model_reuse_catalog": True}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name, payload in {"gate_config.json": config, "gate_result.json": result}.items():
        _write(OUTPUT / name, payload)
    manifest = {"phase": "31", "classification": result["classification"], "source_hash": _hash(Path(__file__))}
    _write(OUTPUT / "manifest.json", manifest)
    report = f"# Fase 31 — gate de cohorte prospectiva\n\n**Clasificación:** `{result['classification']}`\n\n- partidos staging: `{len(rows)}`\n- candidatos independientes: `{len(candidates)}`\n- rechazados: `{len(rejected)}`\n- PostgreSQL SELECT-only: `{database['select_only']}`\n- conteos idénticos: `{database['identical']}`\n- evaluación ejecutada: `False`\n- bootstrap ejecutado: `False`\n- router modificado: `False`\n"
    (OUTPUT / "final_report.md").write_text(report, encoding="utf-8")
    _write(OUTPUT / "hashes.json", {path.name: _hash(path) for path in sorted(OUTPUT.iterdir()) if path.name != "hashes.json"})
    LOGGER.info("Fase 31 gate: %s", result["classification"])
    return result


def main() -> int:
    """Ejecuta el gate y convierte errores de infraestructura en evidencia."""

    try:
        capabilities = detect_capabilities()
        if not capabilities.ready:
            raise ValueError(f"missing:{','.join(capabilities.missing())}")
        run()
    except (ValueError, OSError, *database_error_types()) as error:
        LOGGER.error("Fase 31 no ejecutada: %s", sanitize_error(error))
        return 1
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0
# Created: 2026-07-26
