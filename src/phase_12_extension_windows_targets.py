"""Materialización y auditoría de la extensión histórica de Fase 11.

La fase transforma únicamente artefactos locales candidatos en ventanas y
targets post-partido. No actualiza PostgreSQL ni entrena modelos.

Requirements:
    - numpy

Version: 1.0.0
Created: 2026-07-26
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from src.event_windows_v1 import EventWindowsConfig, build_windows
from src.phase_09_historical_target_revision import derive_targets

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
PHASE11 = ROOT / "artifacts/phase_11_historical_extension_fetch"
CANONICAL = ROOT / "artifacts/phase_01_event_windows_v1/event_windows.json"
PHASE09 = ROOT / "artifacts/phase_09_historical_target_revision/target_labels.json"
OUTPUT = ROOT / "artifacts/phase_12_extension_windows_targets"


@dataclass(frozen=True, slots=True)
class Phase12Config:
    """Parámetros congelados para ventanas y targets de la extensión."""

    version: str = "extension_windows_targets_v2"
    window_minutes: int = 15
    window_count: int = 6
    expected_extension_matches: int = 241
    cohort: str = "phase11_extension_candidate"
    source_dir: str = "artifacts/phase_11_historical_extension_fetch"
    output_dir: str = "artifacts/phase_12_extension_windows_targets"
    phase_label: str = "Fase 12"


def _load(path: Path) -> Any:
    """Carga un artefacto JSON local."""

    return json.loads(path.read_text(encoding="utf-8"))


def _file_hash(path: Path) -> str:
    """Calcula el SHA-256 de un archivo."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash(value: Any) -> str:
    """Calcula un hash estable para una estructura serializable."""

    raw = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _season(kickoff: str) -> str:
    """Deriva la temporada española desde el kickoff UTC."""

    year = int(kickoff[:4])
    month = int(kickoff[5:7])
    return f"{year}-{str(year + 1)[-2:]}" if month >= 8 else f"{year - 1}-{str(year)[-2:]}"


def _normalize_matches(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convierte partidos ESPN al contrato de `event_windows v1`."""

    return [{
        "match_id": int(row["provider_match_id"]),
        "home_team_id": int(row["home_provider_team_id"]),
        "away_team_id": int(row["away_provider_team_id"]),
        "match_date": str(row["kickoff_ts"]),
        "season": _season(str(row["kickoff_ts"])),
        "home_score": int(row["home_score"]),
        "away_score": int(row["away_score"]),
    } for row in rows]


def _normalize_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convierte eventos ESPN al contrato de construcción de ventanas."""

    return [{
        "event_id": int(row["event_index"]),
        "match_id": int(row["provider_match_id"]),
        "minute": int(row["minute"] or 0),
        "second": int(row["second"] or 0),
        "team_id": row["team_provider_id"],
        "event_type": str(row["event_type"] or "unclassified"),
        "annulled": bool(row["annulled"]),
    } for row in rows]


def _score_mismatches(windows: list[dict[str, Any]], matches: list[dict[str, Any]]) -> list[int]:
    """Compara goles observados en ventanas con el marcador final."""

    observed: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    for row in windows:
        side = 0 if bool(row["is_home"]) else 1
        observed[int(row["match_id"])][side] += int(row["goals"])
    expected = {int(row["match_id"]): (int(row["home_score"]), int(row["away_score"])) for row in matches}
    return sorted(match_id for match_id, score in expected.items() if tuple(observed[match_id]) != score)


def _target_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Resume positivos y oportunidades de la cohorte de extensión."""

    total = len(rows)
    boolean_keys = (
        "first_half_goal", "second_half_goal", "home_recovery_draw_or_win",
        "away_recovery_draw_or_win", "home_reaches_level_after_half",
        "away_reaches_level_after_half", "home_comeback_win", "away_comeback_win",
    )
    counts = {key: sum(bool(row[key]) for row in rows) for key in boolean_keys}
    opportunities = {
        "home_trailing_at_half": sum(bool(row["home_trailing_at_half"]) for row in rows),
        "away_trailing_at_half": sum(bool(row["away_trailing_at_half"]) for row in rows),
    }
    conditional = {
        key: counts[key] / opportunities["home_trailing_at_half"]
        if opportunities["home_trailing_at_half"] else 0.0
        for key in ("home_recovery_draw_or_win", "home_reaches_level_after_half", "home_comeback_win")
    }
    conditional.update({
        key: counts[key] / opportunities["away_trailing_at_half"]
        if opportunities["away_trailing_at_half"] else 0.0
        for key in ("away_recovery_draw_or_win", "away_reaches_level_after_half", "away_comeback_win")
    })
    return {"match_count": total, "counts": counts, "opportunity_counts": opportunities, "conditional_rates": conditional}


def _classification(config: Phase12Config, matches: list[dict[str, Any]], windows: list[dict[str, Any]], audit: dict[str, Any]) -> str:
    """Aplica gates de identidad, cobertura y consistencia de marcador."""

    expected = config.expected_extension_matches
    gates = (
        len(matches) == expected,
        len(windows) == expected * 12,
        len({int(row["match_id"]) for row in matches}) == len(matches),
        not audit["score_mismatch_matches"],
        not audit["window_orphans"],
        not audit["out_of_range_clocks"],
        not audit["canonical_overlap"],
        not audit["previous_extension_overlap"],
    )
    return "validated_for_target_revision" if all(gates) else "rejected_for_revision"


def _publish(result: dict[str, Any], output: Path) -> None:
    """Publica artefactos contractuales y hashes reproducibles."""

    output.mkdir(parents=True, exist_ok=True)
    payloads = ("config", "input_manifest", "coverage", "metrics", "audit", "event_windows", "target_labels")
    for name in payloads:
        (output / f"{name}.json").write_text(json.dumps(result[name], indent=2, sort_keys=True, default=str), encoding="utf-8")
    (output / "validation_report.md").write_text(result["validation_report"] + "\n", encoding="utf-8")
    (output / "final_report.md").write_text(result["final_report"] + "\n", encoding="utf-8")
    hashes = {path.name: _file_hash(path) for path in sorted(output.iterdir()) if path.is_file() and path.name != "hashes.json"}
    (output / "hashes.json").write_text(json.dumps(hashes, indent=2, sort_keys=True), encoding="utf-8")


def run(config: Phase12Config | None = None) -> dict[str, Any]:
    """Construye ventanas, targets y auditoría de Fase 11."""

    active = config or Phase12Config()
    source = ROOT / active.source_dir
    output = ROOT / active.output_dir
    raw_matches = _load(source / "candidate_matches.json")
    raw_events = _load(source / "candidate_events.json")
    matches, events = _normalize_matches(raw_matches), _normalize_events(raw_events)
    windows, temporal = build_windows(matches, events, EventWindowsConfig())
    targets = derive_targets(windows, active.cohort)
    canonical_ids = {int(row["match_id"]) for row in _load(CANONICAL)}
    previous_ids = {int(row["match_id"]) for row in _load(PHASE09) if row["cohort"] != "canonical_v1"}
    audit = {"score_mismatch_matches": _score_mismatches(windows, matches), "window_orphans": temporal["orphan_event_match_ids"], "out_of_range_clocks": temporal["out_of_range_clocks"], "canonical_overlap": sorted({int(row["match_id"]) for row in matches} & canonical_ids), "previous_extension_overlap": sorted({int(row["match_id"]) for row in matches} & previous_ids), "raw_source_preserved_outside_public_artifacts": True, "postgresql_modified": False}
    classification = _classification(active, matches, windows, audit)
    summary = _target_summary(targets)
    input_manifest = {"source_matches_hash": _file_hash(source / "candidate_matches.json"), "source_events_hash": _file_hash(source / "candidate_events.json"), "canonical_windows_hash": _file_hash(CANONICAL), "phase09_targets_hash": _file_hash(PHASE09), "window_config_hash": _hash(asdict(EventWindowsConfig()))}
    coverage = {"match_count": len(matches), "window_count": len(windows), "source_event_count": len(events), "window_event_count": sum(int(row["event_count"]) for row in windows), "rows_per_match": 12}
    audit["classification"] = classification
    validation = f"# Validation report — Fase 12\n\n- clasificación: `{classification}`\n- partidos: `{len(matches)}`\n- discrepancias de marcador: `{len(audit['score_mismatch_matches'])}`\n- targets post-partido; no se usan para entrenar ni predecir."
    final = f"# {active.phase_label} — ventanas y targets de extensión\n\n**Clasificación:** `{classification}`\n\n- partidos auditados: `{len(matches)}`\n- ventanas: `{len(windows)}`\n- eventos fuente: `{len(events)}`\n- recuperación local: `{summary['counts']['home_recovery_draw_or_win']}`\n- recuperación visitante: `{summary['counts']['away_recovery_draw_or_win']}`\n- mercados promovidos: `False`\n\nSiguiente paso: repetir la evaluación OOS con partición temporal y priors congelados antes del kickoff."
    result = {"config": asdict(active), "input_manifest": input_manifest, "coverage": coverage, "metrics": summary, "audit": audit, "event_windows": windows, "target_labels": targets, "validation_report": validation, "final_report": final}
    _publish(result, output)
    LOGGER.info("%s ventanas y targets: %s", active.phase_label, classification)
    return result


# Version: 1.0.0
# Created: 2026-07-26
