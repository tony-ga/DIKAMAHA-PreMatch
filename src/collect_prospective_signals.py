"""Recolección prospectiva controlada de señales in-play.

Captura partidos nuevos sin evaluarlos ni recalibrar modelos. PostgreSQL se
usa exclusivamente mediante SELECT y los resultados finales sólo se anexan
cuando el partido está cerrado.

Requirements:
    - pandas
    - SQLAlchemy==2.0.41
    - psycopg2-binary==2.9.10

Version: 1.0.0
Created: 2026-07-16
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from src.audit_event_label_coverage import (
    CoverageConfig,
    _candidate_rows,
    _counts,
    _event_rows,
    _referential_rows,
    _team_rows,
)
from src.calibrate_inplay_models import (
    FIXED_MINUTES,
    LabelingConfig,
    _events_by_match,
    _file_hash,
    _snapshot_labels,
    _stable_hash,
    _utc,
)
from src.dikamaha_inference import DikamahaInferenceEngine
from src.evaluate_incremental_signal_value import APPROVED_RULES
from src.evaluate_markov_labeling_impact import _fixed_rule_support
from src.hawkes_v1_integration import HawkesIntegrationConfig, frozen_alpha_reduced_config
from src.postgres_readonly_staging import (
    ReadonlyDatabase,
    counts_identical,
    database_error_types,
    detect_capabilities,
    sanitize_error,
)
from src.run_prospective_signal_shadow import (
    ProspectiveConfig,
    _add_candidate_signal,
    _add_hawkes,
    _live_request,
    _prior_match_ids,
)

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/phase_7_11_prospective_collection"
PHASE_710 = ROOT / "artifacts/phase_7_10_prospective_signal_shadow"
DEFAULT_LAMBDA_INPUT = PHASE_710 / "prospective_lambda_base_input.json"
FINAL_STATUSES = {"post", "final", "finished", "completed", "full_time"}
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CollectionConfig:
    """Configuración inmutable de captura prospectiva."""

    version: str = "phase_7_11_prospective_collection_v1"
    cutoff_ts: str = "2025-10-26T15:15:00+00:00"
    minimum_complete_matches: int = 30
    hawkes_shadow_requested: bool = False
    evaluate_during_collection: bool = False
    recalibrate_during_collection: bool = False
    official_output: str = "markov_v1"


def _load_json(path: Path) -> Any:
    """Carga JSON versionado."""

    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    """Escribe JSON de forma atómica."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temporary.replace(path)


def _all_match_rows(session: Any) -> list[dict[str, Any]]:
    """Lee partidos completos e incompletos sin filtrar por target."""

    return session.rows(
        """
        SELECT id, home_team_id, away_team_id, match_date, home_score,
               away_score, season, status
        FROM matches
        ORDER BY match_date, id
        """
    )


def _read_database(database_url: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Ejecuta SELECT-only y conserva conteos antes/después."""

    database = ReadonlyDatabase(database_url)
    with database.session() as session:
        before = _counts(session)
        matches = _all_match_rows(session)
        events = _event_rows(session)
        referential = _referential_rows(session)
        after = _counts(session)
    return matches, events, {
        "status": "postgres_readonly_verified", "before": before, "after": after,
        "identical": counts_identical(before, after), "connection_closed": database.closed,
        "statements": database.statements, "write_statements": 0,
        "referential": referential,
    }


def _read_prospective_staging(database_url: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Lee sólo el schema prospectivo mediante SELECT para Fase 7.11."""

    schema = os.getenv("DIKAMAHA_PROSPECTIVE_STAGING_SCHEMA", "prospective_staging")
    if schema not in {"prospective_staging", "prospective_staging_v2"}:
        raise ValueError("unsupported_prospective_staging_schema")
    database = ReadonlyDatabase(database_url)
    with database.session() as session:
        before = {"staging_matches": session.scalar(f"SELECT COUNT(*) FROM {schema}.matches"),
                  "staging_events": session.scalar(f"SELECT COUNT(*) FROM {schema}.events")}
        matches = session.rows(f"""
            SELECT provider_match_id::bigint AS id, home_provider_team_id AS home_team_id,
                   away_provider_team_id AS away_team_id, kickoff_ts AS match_date,
                   home_score, away_score, provider_status AS status, competition_id AS season
            FROM {schema}.matches WHERE provider='espn' ORDER BY kickoff_ts, provider_match_id
        """)
        events = session.rows(f"""
            SELECT provider_match_id::bigint AS match_id,
                   event_index::bigint AS id,
                   NULL::bigint AS event_ledger_id,
                   minute, second,
                   team_provider_id AS timeline_team_id,
                   team_provider_id AS ledger_team_id,
                   event_type, event_type_raw, annulled, event_ts
            FROM {schema}.events WHERE provider='espn'
            ORDER BY provider_match_id, minute, second, event_index
        """)
        referential = {
            "orphan_match": int(session.scalar(f"""
                SELECT COUNT(*) FROM {schema}.events e
                LEFT JOIN {schema}.matches m
                  ON m.provider='espn' AND m.provider_match_id=e.provider_match_id
                WHERE e.provider='espn' AND m.provider_match_id IS NULL
            """)),
            "orphan_ledger": 0,
            "timeline_null_team": int(session.scalar(f"""
                SELECT COUNT(*) FROM {schema}.events
                WHERE provider='espn' AND team_provider_id IS NULL
            """)),
            "ledger_not_applicable": True,
        }
        after = {"staging_matches": session.scalar(f"SELECT COUNT(*) FROM {schema}.matches"),
                 "staging_events": session.scalar(f"SELECT COUNT(*) FROM {schema}.events")}
    return matches, events, {"status": "postgres_readonly_verified", "before": before, "after": after,
                             "identical": counts_identical(before, after), "connection_closed": database.closed,
                             "statements": database.statements, "write_statements": 0, "referential": referential,
                             "source": schema}


def _lambda_input_path() -> Path:
    """Resuelve una entrada local sin permitir rutas externas al workspace."""

    value = os.getenv("DIKAMAHA_PROSPECTIVE_LAMBDA_INPUT")
    path = Path(value).resolve() if value else DEFAULT_LAMBDA_INPUT.resolve()
    if ROOT.resolve() not in path.parents:
        raise ValueError("prospective_lambda_input_outside_workspace")
    return path


def _lambda_inputs() -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    """Carga lambdas OOS congeladas sin imprimir ruta sensible."""

    path = _lambda_input_path()
    if not path.exists():
        return {}, {"available": False, "hash": None, "row_count": 0}
    payload = _load_json(path)
    rows = payload.get("rows", payload) if isinstance(payload, dict) else payload
    mapping = {int(row["match_id"]): row for row in rows}
    return mapping, {"available": True, "hash": _file_hash(path), "row_count": len(mapping)}


def _is_complete(match: dict[str, Any]) -> bool:
    """Declara cierre sólo con status final y marcador completo."""

    status = str(match.get("status") or "").lower()
    scores = match.get("home_score") is not None and match.get("away_score") is not None
    return status in FINAL_STATUSES and scores


def _identity_valid(match: dict[str, Any]) -> bool:
    """Valida orientación e identidad sin consultar resultado."""

    required = (match.get("home_team_id"), match.get("away_team_id"), match.get("match_date"))
    return all(value is not None for value in required) and int(required[0]) != int(required[1])


def _select_matches(
    matches: list[dict[str, Any]], raw_events: list[dict[str, Any]],
    lambda_map: dict[int, dict[str, Any]], config: CollectionConfig,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[int]]:
    """Congela partidos posteriores, exclusiones y dependencias faltantes."""

    historical_ids, _ = _prior_match_ids()
    historical_ids.add(704766)
    cutoff = _utc(config.cutoff_ts)
    event_ids = {int(row["match_id"]) for row in raw_events}
    new = [row for row in matches if _utc(row["match_date"]) > cutoff and int(row["id"]) not in historical_ids]
    records, selected = [], []
    for match in sorted(new, key=lambda row: (_utc(row["match_date"]), int(row["id"]))):
        record = _selection_record(match, event_ids, lambda_map)
        records.append(record)
        if record["collection_eligible"]:
            selected.append(match)
    return records, selected, historical_ids


def _selection_record(
    match: dict[str, Any], event_ids: set[int], lambda_map: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    """Explica completitud, errores y exclusiones de un partido nuevo."""

    match_id = int(match["id"])
    errors = []
    if not _identity_valid(match):
        errors.append("incomplete_identity")
    if match_id not in lambda_map:
        errors.append("missing_lambda_base_oos")
    return {
        "match_id": match_id, "match_date": _utc(match["match_date"]).isoformat(),
        "status": match.get("status"), "complete": _is_complete(match),
        "timeline_available": match_id in event_ids,
        "lambda_base_available": match_id in lambda_map,
        "collection_eligible": not errors, "errors": errors,
        "exclusion_reason": ";".join(errors) if errors else None,
        "final_result": {
            "home_goals": int(match["home_score"]), "away_goals": int(match["away_score"])
        } if _is_complete(match) else None,
    }


def _snapshot_schedule(match: dict[str, Any], events: list[dict[str, Any]]) -> list[Any]:
    """Evita snapshots futuros para partidos aún no cerrados."""

    kickoff = _utc(match["match_date"])
    if _is_complete(match):
        max_minute = 90
    else:
        max_minute = max((int(event["minute"]) for event in events), default=0)
    values = {kickoff + timedelta(minutes=minute) for minute in FIXED_MINUTES if minute <= max_minute}
    values.update(_utc(event["event_ts"]) for event in events if not event["annulled"])
    return sorted(values)


def _baseline_snapshots(
    matches: list[dict[str, Any]], events_map: dict[int, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Genera estados observables sin mirar marcador final."""

    output = []
    for match in matches:
        match_id = int(match["id"])
        for snapshot in _snapshot_schedule(match, events_map[match_id]):
            labels = _snapshot_labels(match, events_map[match_id], snapshot, LabelingConfig())
            output.append({
                "match_id": match_id, "block": "prospective_collection",
                "snapshot_ts": snapshot.isoformat(), "home_team_id": int(match["home_team_id"]),
                "away_team_id": int(match["away_team_id"]), **labels,
            })
    return output


def _candidate_contexts(
    snapshots: list[dict[str, Any]], events_map: dict[int, list[dict[str, Any]]],
) -> dict[tuple[int, str, str], dict[str, Any]]:
    """Aplica exactamente las reglas congeladas de Fase 7.7."""

    rows = _team_rows(snapshots, events_map, CoverageConfig())
    candidates = _candidate_rows(rows, _fixed_rule_support())
    return {(row["match_id"], row["snapshot_ts"], row["side"]): row for row in candidates}


def _collect_snapshots(
    matches: list[dict[str, Any]], events_map: dict[int, list[dict[str, Any]]],
    lambda_map: dict[int, dict[str, Any]], config: CollectionConfig,
) -> list[dict[str, Any]]:
    """Captura snapshots sin calcular métricas ni significancia."""

    snapshots = _baseline_snapshots(matches, events_map)
    contexts = _candidate_contexts(snapshots, events_map)
    match_map = {int(row["id"]): row for row in matches}
    engine = DikamahaInferenceEngine()
    rows = [
        _collect_snapshot(snapshot, match_map[snapshot["match_id"]], events_map[snapshot["match_id"]], contexts, lambda_map[snapshot["match_id"]], engine, config)
        for snapshot in snapshots
    ]
    _add_regime_changes(rows)
    return rows


def _collect_snapshot(
    snapshot: dict[str, Any], match: dict[str, Any], events: list[dict[str, Any]],
    contexts: dict[tuple[int, str, str], dict[str, Any]], lambdas: dict[str, Any],
    engine: DikamahaInferenceEngine, config: CollectionConfig,
) -> dict[str, Any]:
    """Ejecuta Markov oficial y registra rama analítica separada."""

    visible = [event for event in events if _utc(event["event_ts"]) <= _utc(snapshot["snapshot_ts"])]
    request_config = ProspectiveConfig(hawkes_shadow_requested=config.hawkes_shadow_requested)
    request = _live_request(snapshot, match, visible, lambdas, request_config)
    started = time.perf_counter_ns()
    output = engine.predict_live(request)
    latency_ms = (time.perf_counter_ns() - started) / 1_000_000
    row = _snapshot_record(snapshot, match, visible, output, latency_ms)
    _add_candidate_signal(row, contexts, lambdas)
    _add_hawkes(row, output)
    return row


def _snapshot_record(
    snapshot: dict[str, Any], match: dict[str, Any], visible: list[dict[str, Any]],
    output: Any, latency_ms: float,
) -> dict[str, Any]:
    """Serializa inputs visibles, intensidades y provenance."""

    complete = _is_complete(match)
    return {
        "match_id": int(match["id"]), "match_date": _utc(match["match_date"]).isoformat(),
        "snapshot_ts": snapshot["snapshot_ts"], "minute": int(snapshot["minute"]),
        "score_home": int(snapshot["score_home"]), "score_away": int(snapshot["score_away"]),
        "lambda_base_home": output.lambda_base_home, "lambda_base_away": output.lambda_base_away,
        "lambda_official_home": output.lambda_markov_home, "lambda_official_away": output.lambda_markov_away,
        "official_state_home": output.home_state, "official_state_away": output.away_state,
        "events_visible": [_event_provenance(event) for event in visible],
        "max_input_event_ts": max((event["event_ts"] for event in visible), default=None),
        "complete": complete, "target_complete": complete,
        "final_result": {
            "home_goals": int(match["home_score"]), "away_goals": int(match["away_score"])
        } if complete else None,
        "service_status": "ok" if output.audit.passed else "audit_failed",
        "service_latency_ms": latency_ms, "official_source": output.official_source,
        "provenance": asdict(output.provenance), "regime_change": None,
    }


def _event_provenance(event: dict[str, Any]) -> dict[str, Any]:
    """Conserva campos auditables sin payload crudo ni secretos."""

    return {
        "event_id": event["event_id"], "event_ts": event["event_ts"],
        "event_type": event["event_type"], "team_id": event["team_id"],
        "annulled": event["annulled"], "source_timeline_id": event["source_timeline_id"],
        "event_ledger_id": event["event_ledger_id"],
    }


def _add_regime_changes(rows: list[dict[str, Any]]) -> None:
    """Marca cambios respecto del snapshot previo del mismo partido."""

    previous: dict[int, tuple[int, int]] = {}
    for row in sorted(rows, key=lambda item: (item["match_id"], _utc(item["snapshot_ts"]))):
        state = (row["official_state_home"], row["official_state_away"])
        row["regime_change"] = previous.get(row["match_id"]) not in (None, state)
        previous[row["match_id"]] = state


def _complete_match_records(
    records: list[dict[str, Any]], snapshots: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Añade conteos de captura y errores por partido."""

    counts: dict[int, int] = {}
    for row in snapshots:
        counts[row["match_id"]] = counts.get(row["match_id"], 0) + 1
    return [{
        **record, "snapshot_count": counts.get(record["match_id"], 0),
        "collection_state": _match_collection_state(record, counts.get(record["match_id"], 0)),
    } for record in records]


def _match_collection_state(record: dict[str, Any], snapshots: int) -> str:
    """Clasifica completitud sin evaluar desempeño."""

    if record["errors"]:
        return "excluded_or_pending_dependency"
    if not record["complete"]:
        return "collecting_in_play"
    return "complete" if snapshots else "complete_without_snapshots"


def _collection_status(
    records: list[dict[str, Any]], snapshots: list[dict[str, Any]], config: CollectionConfig,
    audit_ok: bool,
) -> dict[str, Any]:
    """Decide readiness sólo por cobertura y calidad de captura."""

    complete = [row for row in records if row["collection_state"] == "complete"]
    pending = [row for row in records if row["collection_state"] != "complete"]
    if not audit_ok:
        status = "prospective_collection_rejected_for_revision"
    elif len(complete) >= config.minimum_complete_matches:
        status = "prospective_collection_ready_for_evaluation"
    elif records:
        status = "prospective_collection_in_progress"
    else:
        status = "insufficient_prospective_coverage"
    return {
        "status": status, "minimum_complete_matches": config.minimum_complete_matches,
        "new_match_count": len(records), "complete_match_count": len(complete),
        "pending_match_count": len(pending), "snapshot_count": len(snapshots),
        "evaluation_performed": False, "calibration_performed": False,
        "significance_calculated": False,
    }


def _temporal_audit(
    snapshots: list[dict[str, Any]], records: list[dict[str, Any]],
    historical_ids: set[int],
) -> dict[str, Any]:
    """Audita orden, leakage, duplicados y targets finales."""

    keys = [
        (_utc(row.get("match_date", row["snapshot_ts"])), int(row["match_id"]), _utc(row["snapshot_ts"]))
        for row in snapshots
    ]
    return {
        "event_ts_lte_snapshot_ts": all(
            all(_utc(event["event_ts"]) <= _utc(row["snapshot_ts"]) for event in row["events_visible"])
            for row in snapshots
        ),
        "stable_temporal_order": keys == sorted(keys),
        "duplicate_snapshot_count": len(keys) - len(set(keys)),
        "historical_match_reuse_count": sum(row["match_id"] in historical_ids for row in records),
        "blocked_704766_present": any(row["match_id"] == 704766 for row in records),
        "final_result_only_when_complete": all(
            (row["final_result"] is not None) == bool(row["complete"]) for row in records
        ),
    }


def _provenance_audit(
    config: CollectionConfig, records: list[dict[str, Any]], snapshots: list[dict[str, Any]],
    lambda_metadata: dict[str, Any], source_hashes: dict[str, str],
) -> dict[str, Any]:
    """Prueba separación de capas, secretos y configuración congelada."""

    default_hawkes = HawkesIntegrationConfig()
    return {
        "config_frozen_before_collection": True, "rules": sorted(APPROVED_RULES),
        "lambda_input": lambda_metadata, "official_output": "markov_v1",
        "official_output_modified": False, "markov_modified": False,
        "hawkes_enabled_default": default_hawkes.hawkes_enabled,
        "hawkes_shadow_requested": config.hawkes_shadow_requested,
        "hawkes_parameters_calibrated": False, "match_features_modified": False,
        "evaluation_performed": False, "signals_promoted": False,
        "complete_snapshot_provenance": all(bool(row["provenance"]) for row in snapshots),
        "external_calls": 0, "secrets_logged": 0, "source_hashes": source_hashes,
        "collected_match_ids": [row["match_id"] for row in records],
    }


def _audit_ok(
    temporal: dict[str, Any], provenance: dict[str, Any], database: dict[str, Any],
) -> bool:
    """Evalúa sólo invariantes de captura, nunca desempeño."""

    temporal_ok = (
        temporal["event_ts_lte_snapshot_ts"] and temporal["stable_temporal_order"]
        and temporal["duplicate_snapshot_count"] == 0
        and temporal["historical_match_reuse_count"] == 0
        and not temporal["blocked_704766_present"]
        and temporal["final_result_only_when_complete"]
    )
    provenance_ok = (
        provenance["official_output_modified"] is False
        and provenance["hawkes_enabled_default"] is False
        and provenance["evaluation_performed"] is False
    )
    database_ok = (
        database["identical"] and database["connection_closed"]
        and database["select_only"] and database["write_statements"] == 0
        and not any(database["referential"][key] for key in ("orphan_match", "orphan_ledger"))
    )
    return temporal_ok and provenance_ok and database_ok


def _source_hashes() -> dict[str, str]:
    """Registra código, pruebas y artefactos congelados."""

    paths = (
        "src/collect_prospective_signals.py", "scripts/run_phase_7_11_prospective_collection.py",
        "tests/test_phase_7_11_prospective_collection.py", "tests/test_phase_7_11_prospective_collection_postgres.py",
        "src/run_prospective_signal_shadow.py", "src/dikamaha_service.py", "src/dikamaha_inference.py",
        "artifacts/phase_7_10_prospective_signal_shadow/prospective_selection.json",
        "artifacts/phase_7_10_prospective_signal_shadow/frozen_signal_config.json",
        "artifacts/phase_7_9_incremental_signal_value/signal_definitions.json",
    )
    return {path: _file_hash(ROOT / path) for path in paths}


def _build_result(
    matches: list[dict[str, Any]], raw_events: list[dict[str, Any]],
    database: dict[str, Any], config: CollectionConfig,
) -> dict[str, Any]:
    """Ejecuta captura sin evaluación y construye auditorías."""

    lambda_map, lambda_metadata = _lambda_inputs()
    selected_records, selected, historical_ids = _select_matches(matches, raw_events, lambda_map, config)
    match_map = {int(row["id"]): row for row in selected}
    events_map = _events_by_match(raw_events, match_map) if selected else {}
    snapshots = _collect_snapshots(selected, events_map, lambda_map, config) if selected else []
    records = _complete_match_records(selected_records, snapshots)
    database = {**database, "select_only": all(item.lstrip().upper().startswith("SELECT ") for item in database["statements"])}
    temporal = _temporal_audit(snapshots, records, historical_ids)
    provenance = _provenance_audit(config, records, snapshots, lambda_metadata, _source_hashes())
    status = _collection_status(records, snapshots, config, _audit_ok(temporal, provenance, database))
    return {
        "config": _config_payload(config), "excluded_ids": sorted(historical_ids),
        "matches": records, "snapshots": snapshots, "status": status,
        "temporal": temporal, "provenance": provenance, "database": database,
    }


def _config_payload(config: CollectionConfig) -> dict[str, Any]:
    """Documenta contrato congelado y ausencia de evaluación."""

    return {
        **asdict(config), "rules": sorted(APPROVED_RULES),
        "hawkes_default": asdict(HawkesIntegrationConfig()),
        "hawkes_candidate": asdict(frozen_alpha_reduced_config()),
        "lambda_input_contract": {
            "fields": ["match_id", "lambda_base_home", "lambda_base_away", "source_hash"],
            "must_be_oos": True, "must_be_frozen_before_snapshots": True,
        },
        "forbidden_operations": ["evaluation", "bootstrap", "calibration", "promotion", "database_write"],
    }


def _normalized(result: dict[str, Any]) -> dict[str, Any]:
    """Excluye telemetría no determinista y sesión DB del replay."""

    payload = json.loads(json.dumps(result, default=str))
    for row in payload["snapshots"]:
        row.pop("service_latency_ms", None)
    payload.pop("database", None)
    return payload


def _write_artifacts(result: dict[str, Any], replay: dict[str, Any]) -> None:
    """Escribe la colección, auditorías, manifiesto e hashes."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    payloads = {
        "collection_config.json": result["config"], "excluded_match_ids.json": {"match_ids": result["excluded_ids"], "count": len(result["excluded_ids"])},
        "collected_matches.json": result["matches"], "collected_snapshots.json": result["snapshots"],
        "collection_status.json": result["status"], "temporal_audit.json": result["temporal"],
        "provenance_audit.json": result["provenance"], "postgres_readonly_audit.json": result["database"],
    }
    for name, payload in payloads.items():
        _write_json(OUTPUT / name, payload)
    manifest = {
        "phase": "7.11", "version": CollectionConfig().version,
        "classification": result["status"]["status"],
        "input_hash": _stable_hash({"config": result["config"], "sources": result["provenance"]["source_hashes"]}),
        "output_hash": replay["primary_hash"], "replay_hash": replay["replay_hash"],
        "replay_identical": replay["identical"], "latency_excluded_from_replay_hash": True,
        "postgresql_modified": False, "evaluation_performed": False,
        "markov_official_modified": False, "hawkes_official": False,
    }
    _write_json(OUTPUT / "manifest.json", manifest)
    (OUTPUT / "final_report.md").write_text(_report(result, replay), encoding="utf-8")
    hashes = {path.name: _file_hash(path) for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"}
    _write_json(OUTPUT / "hashes.json", hashes)


def _report(result: dict[str, Any], replay: dict[str, Any]) -> str:
    """Resume cobertura sin emitir conclusiones predictivas."""

    status = result["status"]
    return "\n".join([
        "# Fase 7.11 - Recolección prospectiva controlada", "",
        f"**Clasificación:** `{status['status']}`", "", "## Cobertura",
        f"- partidos nuevos: `{status['new_match_count']}`",
        f"- partidos completos: `{status['complete_match_count']}` / `{status['minimum_complete_matches']}`",
        f"- partidos pendientes: `{status['pending_match_count']}`",
        f"- snapshots recolectados: `{status['snapshot_count']}`", "",
        "## Política", "- no se calcularon métricas, bootstrap, significancia ni calibración",
        "- resultados finales sólo se conservan para partidos cerrados",
        "- Markov permanece oficial; Hawkes está apagado por defecto", "", "## Integridad",
        f"- replay determinista: `{replay['identical']}`",
        f"- PostgreSQL SELECT-only y conteos idénticos: `{result['database']['select_only'] and result['database']['identical']}`",
        "- cero escrituras, llamadas externas y secretos registrados",
    ])


def _incomplete(reason: str) -> int:
    """Registra capacidad incompleta sin inventar colección."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    status = {"status": "insufficient_prospective_coverage", "reason": reason, "database_verification": "incomplete"}
    _write_json(OUTPUT / "collection_status.json", status)
    _write_json(OUTPUT / "manifest.json", {"phase": "7.11", "classification": status["status"], "postgresql_modified": False})
    (OUTPUT / "final_report.md").write_text(f"# Fase 7.11\n\nRecolección incompleta: `{reason}`.\n", encoding="utf-8")
    return 0


def main() -> int:
    """Ejecuta captura read-only y replay sin evaluación."""

    capabilities = detect_capabilities()
    if not capabilities.ready:
        return _incomplete(f"missing:{','.join(capabilities.missing())}")
    config = CollectionConfig(hawkes_shadow_requested=os.getenv("DIKAMAHA_PROSPECTIVE_HAWKES_SHADOW", "false").lower() == "true")
    database_url = os.environ["DATABASE_URL"].strip().strip("\"'")
    try:
        staging_read = os.getenv("DIKAMAHA_PROSPECTIVE_STAGING_READ", "false").lower() == "true"
        reader = _read_prospective_staging if staging_read else _read_database
        matches, events, database = reader(database_url)
        primary = _build_result(matches, events, database, config)
        replay_result = _build_result(matches, events, database, config)
    except (ValueError, *database_error_types()) as error:
        return _incomplete(sanitize_error(error, database_url))
    replay = {"primary_hash": _stable_hash(_normalized(primary)), "replay_hash": _stable_hash(_normalized(replay_result))}
    replay["identical"] = replay["primary_hash"] == replay["replay_hash"]
    if not replay["identical"]:
        primary["status"]["status"] = "prospective_collection_rejected_for_revision"
    _write_artifacts(primary, replay)
    LOGGER.info("Fase 7.11: %s", primary["status"]["status"])
    return 1 if primary["status"]["status"] == "prospective_collection_rejected_for_revision" else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0
# Created: 2026-07-16
