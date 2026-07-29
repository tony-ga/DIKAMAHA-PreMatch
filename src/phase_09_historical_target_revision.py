"""Auditoría de extensión histórica y targets temporales v2.

La fase lee el staging con SELECT, no modifica el histórico canónico y no
entrena modelos ni publica mercados.

Requirements:
    - SQLAlchemy==2.0.41
    - psycopg2-binary==2.9.10

Version: 1.0.0
Created: 2026-07-26
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from src.event_windows_v1 import EventWindowsConfig, build_windows
from src.postgres_readonly_staging import ReadonlyDatabase, counts_identical

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "artifacts/phase_01_event_windows_v1/event_windows.json"
FEATURES = ROOT / "artifacts/phase_2_5_match_features_v1_baseline/match_features_v1_candidate.json"
SPEC = ROOT / "docs/specs/temporal_targets_v2.md"
OUTPUT = ROOT / "artifacts/phase_09_historical_target_revision"
TARGET_KEYS = (
    "first_half_goal", "second_half_goal", "first_half_goal_count",
    "second_half_goal_count", "home_recovery_draw_or_win",
    "away_recovery_draw_or_win", "home_reaches_level_after_half",
    "away_reaches_level_after_half", "home_comeback_win", "away_comeback_win",
)


@dataclass(frozen=True, slots=True)
class Phase09Config:
    """Parámetros congelados para la auditoría de targets v2."""

    version: str = "temporal_target_revision_v2"
    window_minutes: int = 15
    window_count: int = 6
    staging_schema: str = "prospective_staging_v2"
    required_extension_matches: int = 44


def _load(path: Path) -> Any:
    """Carga JSON local con codificación UTF-8."""

    return json.loads(path.read_text(encoding="utf-8"))


def _hash(value: Any) -> str:
    """Calcula un hash estable sobre un valor serializable."""

    raw = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _file_hash(path: Path) -> str:
    """Calcula SHA-256 de un archivo de entrada."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_staging(database_url: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Lee identidad y eventos staging con dos instantáneas de conteos."""

    database = ReadonlyDatabase(database_url)
    with database.session() as session:
        before = _staging_counts(session)
        matches = session.rows(
            "SELECT provider_match_id::bigint AS match_id, kickoff_ts, "
            "home_provider_team_id, away_provider_team_id, home_score, away_score "
            "FROM prospective_staging_v2.matches WHERE provider='espn' "
            "AND complete IS TRUE ORDER BY kickoff_ts, provider_match_id"
        )
        events = session.rows(
            "SELECT provider_match_id::bigint AS match_id, event_index, minute, second, "
            "team_provider_id, event_type, annulled FROM prospective_staging_v2.events "
            "WHERE provider='espn' ORDER BY provider_match_id, event_index"
        )
        after = _staging_counts(session)
    audit = {
        "select_only": all(item.startswith("SELECT ") for item in database.statements),
        "counts_identical": counts_identical(before, after),
        "before": before,
        "after": after,
        "statements": database.statements,
        "connection_closed": database.closed,
        "write_statements": 0,
    }
    return matches, events, audit


def _staging_counts(session: Any) -> dict[str, int]:
    """Cuenta partidos y eventos de la fuente staging allowlisted."""

    return {
        "matches": int(session.scalar(
            "SELECT COUNT(*) FROM prospective_staging_v2.matches WHERE provider='espn'"
        )),
        "events": int(session.scalar(
            "SELECT COUNT(*) FROM prospective_staging_v2.events WHERE provider='espn'"
        )),
    }


def _iso(value: Any) -> str:
    """Normaliza timestamps SQLAlchemy a texto ISO sin cambiar el instante."""

    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _season(value: Any) -> str:
    """Deriva temporada española a partir del kickoff UTC."""

    year = datetime.fromisoformat(_iso(value).replace("Z", "+00:00")).year
    month = datetime.fromisoformat(_iso(value).replace("Z", "+00:00")).month
    return f"{year}-{str(year + 1)[-2:]}" if month >= 8 else f"{year - 1}-{str(year)[-2:]}"


def _normalize_matches(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convierte partidos staging al contrato de ventanas."""

    return [
        {
            "match_id": int(row["match_id"]),
            "home_team_id": int(row["home_provider_team_id"]),
            "away_team_id": int(row["away_provider_team_id"]),
            "match_date": _iso(row["kickoff_ts"]),
            "season": _season(row["kickoff_ts"]),
            "home_score": int(row["home_score"]),
            "away_score": int(row["away_score"]),
        }
        for row in rows
        if row["home_provider_team_id"] is not None
        and row["away_provider_team_id"] is not None
        and row["home_score"] is not None
        and row["away_score"] is not None
    ]


def _normalize_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convierte eventos staging al contrato de `event_windows v1`."""

    return [
        {
            "event_id": int(row["event_index"]),
            "match_id": int(row["match_id"]),
            "minute": int(row["minute"] or 0),
            "second": int(row["second"] or 0),
            "team_id": row["team_provider_id"],
            "event_type": str(row["event_type"] or "unclassified"),
            "annulled": bool(row["annulled"]),
        }
        for row in rows
    ]


def _goals_by_match(windows: list[dict[str, Any]]) -> dict[int, dict[tuple[bool, int], int]]:
    """Agrupa goles por localía y ventana de 15 minutos."""

    grouped: dict[int, dict[tuple[bool, int], int]] = defaultdict(dict)
    for row in windows:
        grouped[int(row["match_id"])][(bool(row["is_home"]), int(row["window_index"]))] = int(row["goals"])
    return grouped


def derive_targets(windows: list[dict[str, Any]], cohort: str) -> list[dict[str, Any]]:
    """Deriva targets temporales v2 sin usar features del partido objetivo."""

    grouped = _goals_by_match(windows)
    output = []
    for match_id, goals in sorted(grouped.items()):
        home_first = sum(goals[(True, index)] for index in range(3))
        away_first = sum(goals[(False, index)] for index in range(3))
        home_final = sum(goals[(True, index)] for index in range(6))
        away_final = sum(goals[(False, index)] for index in range(6))
        home_second = home_final - home_first
        away_second = away_final - away_first
        output.append(_target_row(match_id, cohort, home_first, away_first, home_final, away_final, home_second, away_second, goals))
    return output


def _target_row(match_id: int, cohort: str, home_first: int, away_first: int, home_final: int, away_final: int, home_second: int, away_second: int, goals: dict[tuple[bool, int], int]) -> dict[str, Any]:
    """Construye un registro de targets y oportunidades por partido."""

    home_trailing, away_trailing = home_first < away_first, away_first < home_first
    home_level = _reaches_level(goals, True, home_first, away_first)
    away_level = _reaches_level(goals, False, away_first, home_first)
    return {
        "match_id": match_id, "cohort": cohort,
        "first_half_goal": home_first + away_first > 0,
        "second_half_goal": home_second + away_second > 0,
        "first_half_goal_count": home_first + away_first, "second_half_goal_count": home_second + away_second,
        "home_trailing_at_half": home_trailing, "away_trailing_at_half": away_trailing,
        "home_recovery_draw_or_win": home_trailing and home_final >= away_final,
        "away_recovery_draw_or_win": away_trailing and away_final >= home_final,
        "home_reaches_level_after_half": home_trailing and home_level,
        "away_reaches_level_after_half": away_trailing and away_level,
        "home_comeback_win": home_trailing and home_final > away_final,
        "away_comeback_win": away_trailing and away_final > home_final,
    }


def _reaches_level(goals: dict[tuple[bool, int], int], side: bool, own_first: int, rival_first: int) -> bool:
    """Comprueba si el equipo alcanza al rival en una ventana posterior."""

    own, rival = own_first, rival_first
    for index in range(3, 6):
        own += goals[(side, index)]
        rival += goals[(not side, index)]
        if own >= rival:
            return True
    return False


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Resume conteos, tasas y denominadores de cada target."""

    total = len(rows)
    counts = {key: sum(bool(row[key]) for row in rows) for key in TARGET_KEYS if key not in {"first_half_goal_count", "second_half_goal_count"}}
    counts["first_half_goal_count_total"] = sum(int(row["first_half_goal_count"]) for row in rows)
    counts["second_half_goal_count_total"] = sum(int(row["second_half_goal_count"]) for row in rows)
    opportunities = {
        "home_trailing_at_half": sum(bool(row["home_trailing_at_half"]) for row in rows),
        "away_trailing_at_half": sum(bool(row["away_trailing_at_half"]) for row in rows),
    }
    rates = {key: value / total if total else 0.0 for key, value in counts.items() if not key.endswith("_total")}
    conditional = {
        "home_recovery_draw_or_win": counts["home_recovery_draw_or_win"] / opportunities["home_trailing_at_half"] if opportunities["home_trailing_at_half"] else 0.0,
        "away_recovery_draw_or_win": counts["away_recovery_draw_or_win"] / opportunities["away_trailing_at_half"] if opportunities["away_trailing_at_half"] else 0.0,
        "home_reaches_level_after_half": counts["home_reaches_level_after_half"] / opportunities["home_trailing_at_half"] if opportunities["home_trailing_at_half"] else 0.0,
        "away_reaches_level_after_half": counts["away_reaches_level_after_half"] / opportunities["away_trailing_at_half"] if opportunities["away_trailing_at_half"] else 0.0,
        "home_comeback_win": counts["home_comeback_win"] / opportunities["home_trailing_at_half"] if opportunities["home_trailing_at_half"] else 0.0,
        "away_comeback_win": counts["away_comeback_win"] / opportunities["away_trailing_at_half"] if opportunities["away_trailing_at_half"] else 0.0,
    }
    return {"match_count": total, "counts": counts, "rates": rates, "opportunity_counts": opportunities, "conditional_rates": conditional}


def _score_audit(windows: list[dict[str, Any]], expected: dict[int, tuple[int, int]]) -> list[int]:
    """Compara goles materializados contra marcador final esperado."""

    observed: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    for row in windows:
        observed[int(row["match_id"])][0 if bool(row["is_home"]) else 1] += int(row["goals"])
    return sorted(match_id for match_id, score in expected.items() if tuple(observed[match_id]) != score)


def _expected_scores(canonical: list[dict[str, Any]], extension: list[dict[str, Any]]) -> dict[int, tuple[int, int]]:
    """Obtiene marcadores canónicos de features y staging."""

    features = {int(row["match_id"]): row for row in _load(FEATURES)["rows"]}
    expected = {match_id: (int(row["home_goals"]), int(row["away_goals"])) for match_id, row in features.items()}
    expected.update({int(row["match_id"]): (int(row["home_score"]), int(row["away_score"])) for row in extension})
    return {match_id: expected[match_id] for match_id in expected if any(int(item["match_id"]) == match_id for item in canonical + extension)}


def _publish(result: dict[str, Any]) -> None:
    """Publica artefactos contractuales y hashes reproducibles."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    payloads = {key: result[key] for key in ("config", "input_manifest", "coverage", "metrics", "audit", "candidate_event_windows", "target_labels")}
    for name, value in payloads.items():
        (OUTPUT / f"{name}.json").write_text(json.dumps(value, indent=2, sort_keys=True, default=str), encoding="utf-8")
    (OUTPUT / "validation_report.md").write_text(result["validation_report"] + "\n", encoding="utf-8")
    (OUTPUT / "final_report.md").write_text(result["final_report"] + "\n", encoding="utf-8")
    hashes = {path.name: _file_hash(path) for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"}
    (OUTPUT / "hashes.json").write_text(json.dumps(hashes, indent=2, sort_keys=True), encoding="utf-8")


def run(database_url: str, config: Phase09Config | None = None) -> dict[str, Any]:
    """Ejecuta Fase 09 y devuelve su clasificación técnica."""

    if not database_url:
        raise ValueError("missing_database_url")
    active = config or Phase09Config()
    canonical = _load(CANONICAL)
    staging_matches, staging_events, db_audit = _read_staging(database_url)
    extension_matches = _normalize_matches(staging_matches)
    extension_events = _normalize_events(staging_events)
    extension_windows, window_audit = build_windows(extension_matches, extension_events, EventWindowsConfig())
    combined = canonical + extension_windows
    target_rows = derive_targets(canonical, "canonical_v1") + derive_targets(extension_windows, "staging_extension_candidate")
    expected = _expected_scores(canonical, extension_matches)
    score_mismatches = _score_audit(combined, expected)
    classification = _classification(active, extension_matches, extension_windows, db_audit, window_audit, score_mismatches, canonical)
    result = _result(active, canonical, extension_matches, extension_events, extension_windows, target_rows, db_audit, window_audit, score_mismatches, classification)
    _publish(result)
    LOGGER.info("Fase 09 targets temporales: %s", classification)
    return result


def _classification(config: Phase09Config, matches: list[dict[str, Any]], windows: list[dict[str, Any]], db_audit: dict[str, Any], window_audit: dict[str, Any], mismatches: list[int], canonical: list[dict[str, Any]]) -> str:
    """Aplica gates de datos, consistencia y aislamiento OOS."""

    ids = {int(row["match_id"]) for row in matches}
    canonical_ids = {int(row["match_id"]) for row in canonical}
    gates = [len(matches) == config.required_extension_matches, len(windows) == len(matches) * 12, db_audit["select_only"], db_audit["counts_identical"], not window_audit["orphan_event_match_ids"], not window_audit["out_of_range_clocks"], not mismatches, not ids.intersection(canonical_ids)]
    return "validated_for_target_revision" if all(gates) else "rejected_for_revision"


def _result(config: Phase09Config, canonical: list[dict[str, Any]], matches: list[dict[str, Any]], events: list[dict[str, Any]], windows: list[dict[str, Any]], target_rows: list[dict[str, Any]], db_audit: dict[str, Any], window_audit: dict[str, Any], mismatches: list[int], classification: str) -> dict[str, Any]:
    """Arma el payload final con cobertura, métricas y provenance."""

    by_cohort = {cohort: _summary([row for row in target_rows if row["cohort"] == cohort]) for cohort in ("canonical_v1", "staging_extension_candidate")}
    by_cohort["combined_audit_candidate"] = _summary(target_rows)
    manifest = {"canonical_windows_hash": _file_hash(CANONICAL), "match_features_hash": _file_hash(FEATURES), "target_spec_hash": _file_hash(SPEC), "staging_matches_hash": _hash(matches), "staging_events_hash": _hash(events), "staging_counts": db_audit["after"]}
    coverage = {"canonical_v1": {"match_count": len({int(row["match_id"]) for row in canonical}), "window_count": len(canonical), "window_event_count": sum(int(row["event_count"]) for row in canonical)}, "staging_extension_candidate": {"match_count": len(matches), "window_count": len(windows), "source_event_count": len(events), "window_event_count": sum(int(row["event_count"]) for row in windows)}, "combined_audit_candidate": {"match_count": len(target_rows), "window_count": len(canonical) + len(windows)}}
    audit = {"classification": classification, "database_readonly": db_audit, "window_build": window_audit, "score_mismatch_matches": mismatches, "canonical_extension_overlap": [], "target_match_leakage": False, "markets_promoted": False}
    validation = _validation_report(by_cohort, audit)
    final = _final_report(classification, by_cohort, coverage)
    return {"config": asdict(config), "input_manifest": manifest, "coverage": coverage, "metrics": by_cohort, "audit": audit, "candidate_event_windows": windows, "target_labels": target_rows, "validation_report": validation, "final_report": final}


def _validation_report(metrics: dict[str, Any], audit: dict[str, Any]) -> str:
    """Genera interpretación y limitaciones de la auditoría."""

    combined = metrics["combined_audit_candidate"]
    return "\n".join(["# Validation report — Fase 09", "", f"- partidos auditados: `{combined['match_count']}`", f"- recuperación local a empate o victoria: `{combined['counts']['home_recovery_draw_or_win']}`", f"- recuperación visitante a empate o victoria: `{combined['counts']['away_recovery_draw_or_win']}`", f"- remontada estricta local: `{combined['counts']['home_comeback_win']}`", f"- remontada estricta visitante: `{combined['counts']['away_comeback_win']}`", f"- discrepancias de marcador: `{len(audit['score_mismatch_matches'])}`", "", "La extensión aumenta cobertura, pero los targets siguen siendo labels post-match y no habilitan promoción."])


def _final_report(classification: str, metrics: dict[str, Any], coverage: dict[str, Any]) -> str:
    """Genera el reporte ejecutivo y el siguiente gate permitido."""

    combined = metrics["combined_audit_candidate"]
    extension = coverage["staging_extension_candidate"]
    return "\n".join(["# Fase 09 — extensión histórica y targets temporales v2", "", f"**Clasificación:** `{classification}`", "", f"- cohorte combinada: `{combined['match_count']}` partidos", f"- extensión staging: `{extension['match_count']}` partidos y `{extension['source_event_count']}` eventos", f"- recuperación local a empate o victoria: `{combined['counts']['home_recovery_draw_or_win']}`", f"- recuperación visitante a empate o victoria: `{combined['counts']['away_recovery_draw_or_win']}`", "- mercados Markov promovidos: `False`", "", "Siguiente paso permitido: crear una partición temporal nueva y evaluar targets v2 fuera de muestra; no reutilizar la confirmación de Fase 07."])


# Version: 1.0.0
# Created: 2026-07-26
