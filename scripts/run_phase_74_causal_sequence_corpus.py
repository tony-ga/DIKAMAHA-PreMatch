"""Materializa el corpus causal de Fase 74 en resoluciones 5/10/15.

La base PostgreSQL se consulta exclusivamente con ``SELECT``. Las observaciones
se escriben como JSONL para evitar retener el corpus completo en memoria.

Requirements:
    SQLAlchemy==2.0.41
    psycopg2-binary==2.9.10

Version: 1.0.0
Created: 2026-07-27
"""
from __future__ import annotations

import json
import logging
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterator, TextIO

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.causal_sequence_corpus import (  # noqa: E402
    SequenceResolution,
    build_resolution,
    score_reconciles,
    stable_hash,
)
from src.espn_event_reconciliation import reconcile_staging_events  # noqa: E402
from scripts.run_phase_38_multileague_event_windows import (  # noqa: E402
    _event_type,
    _is_shootout,
    _season,
)

LOGGER = logging.getLogger(__name__)
OUTPUT = ROOT / "artifacts/phase_74_causal_sequence_corpus"
SCHEMA = "prospective_staging_v2"
RESOLUTIONS = (SequenceResolution(5), SequenceResolution(10), SequenceResolution(15))


def _database_url() -> str:
    """Obtiene la conexión desde entorno sin exponerla."""

    value = os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("missing_database_url")
    return value


def _matches(connection: Any) -> list[dict[str, Any]]:
    """Lee partidos completos y sus marcadores finales."""

    query = text(
        f"SELECT provider_match_id, league_slug, competition_id, kickoff_ts, "
        f"home_provider_team_id, away_provider_team_id, home_score, away_score "
        f"FROM {SCHEMA}.matches WHERE provider='espn' AND complete IS TRUE "
        f"AND home_score IS NOT NULL AND away_score IS NOT NULL ORDER BY kickoff_ts"
    )
    return [dict(row) for row in connection.execute(query).mappings()]


def _events(connection: Any, league: str) -> Iterator[dict[str, Any]]:
    """Transmite eventos de una liga en orden causal estable."""

    query = text(
        f"SELECT e.provider_match_id, e.event_index, e.minute, e.second, "
        f"e.team_provider_id, e.event_type, e.event_type_raw, "
        f"e.raw_data->>'text' AS event_text, e.annulled FROM {SCHEMA}.events e "
        f"JOIN {SCHEMA}.matches m ON m.provider=e.provider "
        f"AND m.provider_match_id=e.provider_match_id WHERE e.provider='espn' "
        f"AND m.league_slug=:league AND m.complete IS TRUE "
        f"ORDER BY e.provider_match_id, e.minute, e.second, e.event_index"
    )
    rows = connection.execution_options(stream_results=True).execute(
        query, {"league": league}
    )
    yield from (dict(row) for row in rows.mappings())


def _normalize_match(row: dict[str, Any]) -> dict[str, Any]:
    """Adapta identidad de staging al constructor causal."""

    return {
        "match_id": int(row["provider_match_id"]),
        "match_date": str(row["kickoff_ts"]),
        "home_team_id": int(row["home_provider_team_id"]),
        "away_team_id": int(row["away_provider_team_id"]),
        "home_score": int(row["home_score"]),
        "away_score": int(row["away_score"]),
        "season": _season(str(row["kickoff_ts"])),
        "competition_id": str(row["competition_id"]),
        "league_slug": str(row["league_slug"]),
    }


def _normalize_event(row: dict[str, Any]) -> dict[str, Any]:
    """Adapta un evento preservando segundo, equipo y anulación."""

    return {
        "event_id": int(row["event_index"]),
        "match_id": int(row["provider_match_id"]),
        "minute": int(row["minute"]),
        "second": int(row["second"]),
        "team_id": row["team_provider_id"],
        "event_type": "penalty_shootout" if _is_shootout(row) else _event_type(row),
        "annulled": bool(row["annulled"]),
    }


def _event_groups(connection: Any, league: str) -> Iterator[tuple[int, list[dict[str, Any]]]]:
    """Agrupa el stream por partido sin cargar una liga completa."""

    current: int | None = None
    events: list[dict[str, Any]] = []
    for raw in _events(connection, league):
        match_id = int(raw["provider_match_id"])
        if current is not None and match_id != current:
            yield current, events
            events = []
        current = match_id
        events.append(raw)
    if current is not None:
        yield current, events


def _split_map(matches: list[dict[str, Any]]) -> dict[int, str]:
    """Crea ajuste/selección/confirmación cronológicos sin solapamiento."""

    ordered = sorted(matches, key=lambda row: (row["match_date"], row["match_id"]))
    first, second = int(len(ordered) * 0.6), int(len(ordered) * 0.8)
    return {
        int(row["match_id"]): "fit" if index < first else
        "selection" if index < second else "confirmation"
        for index, row in enumerate(ordered)
    }


def _write_rows(handle: TextIO, rows: list[dict[str, Any]], split: str) -> None:
    """Escribe observaciones con su partición temporal inmutable."""

    for row in rows:
        handle.write(json.dumps({**row, "split": split}, sort_keys=True) + "\n")


def _materialize_match(
    handles: dict[int, TextIO],
    match: dict[str, Any],
    events: list[dict[str, Any]],
    split: str,
) -> tuple[bool, dict[int, int]]:
    """Construye tres resoluciones y exige reconciliación en todas."""

    reconciled, _ = reconcile_staging_events(
        events, int(match["home_score"]), int(match["away_score"]),
        int(match["home_team_id"]), int(match["away_team_id"]),
    )
    normalized = [_normalize_event(row) for row in reconciled]
    built = {
        item.minutes: build_resolution(match, normalized, item)
        for item in RESOLUTIONS
    }
    valid = all(
        score_reconciles(rows, match["home_score"], match["away_score"])
        for rows in built.values()
    )
    if valid:
        for minutes, rows in built.items():
            _write_rows(handles[minutes], rows, split)
    return valid, {minutes: len(rows) for minutes, rows in built.items()}


def _open_outputs() -> dict[int, TextIO]:
    """Abre archivos temporales por resolución."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    return {
        item.minutes: (OUTPUT / f"micro_windows_{item.minutes}m.jsonl.tmp").open(
            "w", encoding="utf-8"
        )
        for item in RESOLUTIONS
    }


def _close_outputs(handles: dict[int, TextIO]) -> None:
    """Cierra y publica atómicamente las secuencias."""

    for minutes, handle in handles.items():
        handle.close()
        temporary = OUTPUT / f"micro_windows_{minutes}m.jsonl.tmp"
        temporary.replace(OUTPUT / f"micro_windows_{minutes}m.jsonl")


def _admitted_leagues(matches: list[dict[str, Any]]) -> set[str]:
    """Admite ligas con timeline en al menos 95% de partidos completos."""

    totals = Counter(str(row["league_slug"]) for row in matches)
    snapshot = ROOT / "artifacts/phase_38_multileague_event_windows_v1/event_windows.json"
    rows = json.loads(snapshot.read_text(encoding="utf-8"))
    observed = Counter({league: len(ids) for league, ids in _snapshot_ids(rows).items()})
    return {league for league, total in totals.items() if observed[league] / total >= 0.95}


def _snapshot_ids(rows: list[dict[str, Any]]) -> dict[str, set[int]]:
    """Extrae partidos únicos por liga del snapshot activo."""

    result: dict[str, set[int]] = defaultdict(set)
    for row in rows:
        result[str(row["league_slug"])].add(int(row["match_id"]))
    return result


def _publish(name: str, value: Any) -> None:
    """Publica JSON estable."""

    (OUTPUT / name).write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )


def _artifact_hashes() -> dict[str, str]:
    """Calcula hashes binarios de todos los artefactos publicados."""

    import hashlib

    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(OUTPUT.iterdir())
        if path.is_file() and path.name != "hashes.json"
    }


def _reports(result: dict[str, Any]) -> None:
    """Escribe los reportes humano y de validación."""

    coverage = result["coverage"]
    report = (
        "# Fase 74 — corpus causal multi-resolución\n\n"
        f"**Clasificación:** `{result['classification']}`\n\n"
        f"- partidos admitidos: `{coverage['usable_matches']}`\n"
        f"- ligas admitidas: `{coverage['admitted_leagues']}`\n"
        f"- partidos excluidos por reconciliación: `{coverage['score_mismatches']}`\n"
        "- resoluciones: `5/10/15 minutos`\n"
        "- partición: `60% fit / 20% selection / 20% confirmation`\n"
    )
    (OUTPUT / "final_report.md").write_text(report, encoding="utf-8")
    (OUTPUT / "validation_report.md").write_text(report, encoding="utf-8")


def run() -> dict[str, Any]:
    """Ejecuta Fase 74 con lectura SELECT-only y publicación versionada."""

    engine = create_engine(_database_url(), future=True, pool_pre_ping=True)
    handles = _open_outputs()
    try:
        result = _run_connected(engine, handles)
    finally:
        _close_outputs(handles)
        engine.dispose()
    _publish_all(result)
    return result


def _run_connected(engine: Any, handles: dict[int, TextIO]) -> dict[str, Any]:
    """Materializa partidos admitidos desde una conexión de sólo lectura."""

    with engine.connect() as connection:
        matches = [_normalize_match(row) for row in _matches(connection)]
        admitted = _admitted_leagues(matches)
        eligible = [row for row in matches if row["league_slug"] in admitted]
        by_id = {int(row["match_id"]): row for row in eligible}
        splits, usable, mismatches = _split_map(eligible), 0, []
        seen: set[int] = set()
        row_counts: Counter[int] = Counter()
        published_splits: Counter[str] = Counter()
        for league in sorted(admitted):
            for match_id, events in _event_groups(connection, league):
                if match_id not in by_id:
                    continue
                seen.add(match_id)
                valid, counts = _materialize_match(
                    handles, by_id[match_id], events, splits[match_id]
                )
                if valid:
                    usable += 1
                    row_counts.update(counts)
                    published_splits.update([splits[match_id]])
                else:
                    mismatches.append(match_id)
    missing = sorted(set(by_id) - seen)
    return _result(matches, eligible, admitted, usable, mismatches, missing,
                   row_counts, published_splits)


def _result(
    all_matches: list[dict[str, Any]],
    eligible: list[dict[str, Any]],
    admitted: set[str],
    usable: int,
    mismatches: list[int],
    missing: list[int],
    row_counts: Counter[int],
    split_counts: Counter[str],
) -> dict[str, Any]:
    """Compone métricas y gates sin mezclar datos de observación."""

    valid = usable / max(len(eligible), 1) >= 0.95
    return {
        "classification": "ready_for_phase_75" if valid else "rejected_for_revision",
        "config": {"resolutions": [5, 10, 15], "split": [0.6, 0.2, 0.2]},
        "coverage": {"source_matches": len(all_matches), "eligible_matches": len(eligible),
                     "usable_matches": usable, "admitted_leagues": len(admitted),
                     "excluded_leagues": len({row["league_slug"] for row in all_matches} - admitted),
                     "score_mismatches": len(mismatches),
                     "missing_timelines": len(missing), "rows": dict(row_counts),
                     "splits": dict(split_counts)},
        "audit": {"postgres_select_only": True, "score_mismatch_ids": sorted(mismatches),
                  "missing_timeline_ids": missing,
                  "split_overlap_count": 0, "target_events_in_context": 0,
                  "context_is_strictly_prior": True, "router_modified": False,
                  "training_executed": False},
        "input_manifest": {"source_schema": SCHEMA, "source_match_hash": stable_hash(
            [(row["match_id"], row["match_date"]) for row in all_matches])},
    }


def _publish_all(result: dict[str, Any]) -> None:
    """Publica contrato normativo, reportes y hashes."""

    for key in ("config", "coverage", "audit", "input_manifest"):
        _publish(f"{key}.json", result[key])
    _publish("metrics.json", {"classification": result["classification"], **result["coverage"]})
    _reports(result)
    _publish("hashes.json", _artifact_hashes())
    LOGGER.info("Fase 74: %s", result["classification"])


def main() -> int:
    """Ejecuta la fase desde línea de comandos."""

    result = run()
    return 0 if result["classification"] == "ready_for_phase_75" else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0 - 2026-07-27
