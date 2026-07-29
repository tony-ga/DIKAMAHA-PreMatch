"""Recupera referencias activas ausentes en staging para cerrar cobertura.

Sólo consulta ESPN y escribe lotes normalizados en ``prospective_staging_v2``.
No activa snapshots, no modifica el router y publica únicamente conteos y
errores sanitizados.

Requirements:
    - SQLAlchemy==2.0.41
    - requests
    - tenacity

Version: 1.0.0
Created: 2026-07-27
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_multileague_ingestion import _fetch_one, _store_league
from src.prematch_snapshot_registry import resolve_active_snapshot

LOGGER = logging.getLogger(__name__)
OUTPUT = ROOT / "artifacts/phase_61_source_coverage_closure_v1"
SCHEMA = "prospective_staging_v2"


def _parser() -> argparse.ArgumentParser:
    """Define concurrencia y pausa conservadoras para ESPN."""

    parser = argparse.ArgumentParser(description="Cierra referencias del snapshot ausentes en staging.")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--sleep-seconds", type=float, default=0.4)
    return parser


def _database_url() -> str:
    """Obtiene DATABASE_URL sin registrarlo."""

    value = os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("missing_database_url")
    return value


def _active_references(path: Path) -> list[dict[str, str]]:
    """Deduplica una referencia ESPN por partido desde el snapshot activo."""

    rows = json.loads(path.read_text(encoding="utf-8"))
    references: dict[str, dict[str, str]] = {}
    for row in rows:
        match_id = str(row["match_id"])
        competition = str(row["competition_id"])
        if not competition.isdigit():
            competition = match_id
        references.setdefault(match_id, {"provider_match_id": match_id, "competition_id": competition, "league_slug": str(row["league_slug"])})
    return list(references.values())


def _existing_ids() -> set[str]:
    """Lee IDs ya presentes en staging con SELECT-only."""

    engine = create_engine(_database_url(), future=True, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            rows = connection.execute(text(f"SELECT provider_match_id FROM {SCHEMA}.matches WHERE provider='espn'")).scalars().all()
            return {str(value) for value in rows}
    finally:
        engine.dispose()


def _write(name: str, payload: Any) -> None:
    """Escribe un artefacto JSON sanitizado de forma atómica."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / name
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temporary.replace(target)


def run(args: argparse.Namespace) -> dict[str, Any]:
    """Recupera las referencias faltantes y valida conteos finales."""

    if args.workers < 1 or args.sleep_seconds < 0:
        raise ValueError("invalid_coverage_closure_limits")
    active = resolve_active_snapshot()
    references = _active_references(active)
    existing = _existing_ids()
    missing = [row for row in references if row["provider_match_id"] not in existing]
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in missing:
        grouped[row["league_slug"]].append(row)
    statuses: list[dict[str, Any]] = []
    for league, league_refs in sorted(grouped.items()):
        batches = []
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(_fetch_one, ref, args.sleep_seconds) for ref in league_refs]
            for future in as_completed(futures):
                batch, status = future.result()
                statuses.append(status)
                if batch is not None:
                    batches.append(batch)
        if batches:
            _store_league(_database_url(), league, batches)
    normalized = sum(row["status"] == "normalized" for row in statuses)
    failed = sum(row["status"] == "failed" for row in statuses)
    result = {"classification": "source_coverage_closure_with_failures" if failed else "source_coverage_closure_verified", "active_snapshot": str(active), "active_references": len(references), "missing_references": len(missing), "normalized_matches": normalized, "failed_matches": failed, "failures_by_league": {league: sum(row["status"] == "failed" and row["league_slug"] == league for row in statuses) for league in sorted(grouped)}, "staging_write": True, "snapshot_activated": False, "router_modified": False}
    _write("audit.json", result)
    _write("missing_references.json", missing)
    report = ["# Fase 61 — cierre de cobertura staging", "", f"**Clasificación:** `{result['classification']}`", "", f"- referencias activas: `{len(references)}`", f"- ausentes detectadas: `{len(missing)}`", f"- normalizadas y escritas: `{normalized}`", f"- fallidas: `{failed}`", "- snapshot activado: `False`", "- router modificado: `False`"]
    (OUTPUT / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    LOGGER.info("Fase 61 cobertura: %s", result["classification"])
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    try:
        raise SystemExit(0 if run(_parser().parse_args())["classification"] == "source_coverage_closure_verified" else 1)
    except (OSError, RuntimeError, ValueError) as error:
        LOGGER.error("Cierre de cobertura rechazado: %s", error)
        raise SystemExit(2) from error

# Version: 1.0.0
# Created: 2026-07-27
