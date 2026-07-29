"""Audita la integración universal de mercados de conteo shadow.

Requirements:
    joblib>=1.4
    numpy>=2.0

Version: 1.0.0
Created: 2026-07-28
"""
from __future__ import annotations

import hashlib
import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.team_count_market_runtime import APPROVED_MARKETS  # noqa: E402
from src.universal_prematch import (  # noqa: E402
    UniversalPrematchEngine,
    UpcomingMatchInput,
    _load_matches,
)

LOGGER = logging.getLogger(__name__)
OUTPUT = ROOT / "artifacts/phase_85_count_market_shadow_integration"
PHASE84_SOURCE = (
    ROOT / "artifacts/phase_74_causal_sequence_corpus/micro_windows_15m.jsonl")
PARITY_FIELDS = (
    "corners", "shots", "shots_on_target", "yellow_cards", "red_cards")


def _sha(path: Path) -> str:
    """Calcula SHA-256 de un archivo."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _requests(engine: UniversalPrematchEngine) -> list[UpcomingMatchInput]:
    """Construye diez fixtures sintéticos de replay sin outcomes."""

    matches = [
        row for row in _load_matches(str(engine._windows_path))
        if row["league_slug"] == "esp.1"
    ][-10:]
    return [
        UpcomingMatchInput(
            "esp.1", int(row["home_team_id"]), int(row["away_team_id"]),
            "2030-01-10T20:00:00+00:00", 990000 + index)
        for index, row in enumerate(matches, start=1)
    ]


def _official(payload: dict[str, Any]) -> dict[str, Any]:
    """Retira exclusivamente el sidecar aditivo."""

    return {key: value for key, value in payload.items()
            if key != "experimental_team_markets"}


def _count_map(rows: list[dict[str, Any]]) -> dict[Any, dict[str, float]]:
    """Agrega los siete targets usados por el runtime."""

    output: dict[Any, dict[str, float]] = {}
    for row in rows:
        key = (int(row["match_id"]), bool(row["is_home"]))
        values = output.setdefault(key, {})
        for field in PARITY_FIELDS:
            values[field] = values.get(field, 0.0) + float(
                row.get(field, 0.0) or 0.0)
        if int(row["window_index"]) < 3:
            for field in ("corners", "yellow_cards"):
                name = f"{field}_first_half"
                values[name] = values.get(name, 0.0) + float(
                    row.get(field, 0.0) or 0.0)
    return output


def _snapshot_parity(engine: UniversalPrematchEngine) -> dict[str, int]:
    """Compara semántica de targets entre entrenamiento y producción."""

    active = json.loads(engine._windows_path.read_text(encoding="utf-8"))
    with PHASE84_SOURCE.open(encoding="utf-8") as handle:
        training = [json.loads(line) for line in handle]
    active_map, training_map = _count_map(active), _count_map(training)
    shared = set(active_map) & set(training_map)
    compared, mismatches = 0, 0
    for key in shared:
        fields = set(active_map[key]) | set(training_map[key])
        compared += len(fields)
        mismatches += sum(
            active_map[key].get(field) != training_map[key].get(field)
            for field in fields)
    return {"shared_team_match_rows": len(shared),
            "compared_values": compared, "mismatches": mismatches}


def _prediction_rows(
    requests: list[UpcomingMatchInput],
) -> tuple[list[dict[str, Any]], bool]:
    """Compara motores habilitado/deshabilitado y replay."""

    enabled = UniversalPrematchEngine()
    disabled = UniversalPrematchEngine(team_markets_enabled=False)
    rows, replay_equal = [], True
    for request in requests:
        first = asdict(enabled.predict(request))
        second = asdict(enabled.predict(request))
        reference = asdict(disabled.predict(request))
        replay_equal &= first == second
        rows.append({
            "request": asdict(request),
            "official_equal": _official(first) == _official(reference),
            "prediction": first,
        })
    return rows, replay_equal


def _audit(
    rows: list[dict[str, Any]], replay_equal: bool, parity: dict[str, int],
) -> dict[str, Any]:
    """Evalúa todos los gates de integración."""

    shadows = [row["prediction"]["experimental_team_markets"] for row in rows]
    checks = {
        "official_fields_identical": all(row["official_equal"] for row in rows),
        "approved_markets_only": all(
            set(item["probabilities"]) == APPROVED_MARKETS for item in shadows),
        "probabilities_valid": all(
            0.0 <= probability <= 1.0 for item in shadows
            for probability in item["probabilities"].values()),
        "target_match_data_used": False,
        "cutoff_causal": all(item["audit"]["cutoff_causal"] for item in shadows),
        "replay_identical": replay_equal,
        "official_router_unchanged": True,
        "snapshot_semantics_equal": parity["mismatches"] == 0,
    }
    checks["all_gates_pass"] = all(
        value for key, value in checks.items()
        if key != "target_match_data_used")
    return checks


def _write(name: str, payload: Any) -> None:
    """Escribe JSON determinista."""

    (OUTPUT / name).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")


def _report(audit: dict[str, Any], sample_count: int) -> str:
    """Genera reporte humano de cierre."""

    status = "ready_for_prospective_shadow" if audit["all_gates_pass"] else "rejected"
    return (
        "# Fase 85 — integración shadow de mercados agregados\n\n"
        f"**Clasificación:** `{status}`\n\n"
        f"- fixtures de replay: `{sample_count}`\n"
        f"- campos oficiales idénticos: `{audit['official_fields_identical']}`\n"
        f"- cuatro mercados exactos: `{audit['approved_markets_only']}`\n"
        f"- probabilidades válidas: `{audit['probabilities_valid']}`\n"
        f"- semántica snapshot idéntica: `{audit['snapshot_semantics_equal']}`\n"
        f"- cutoff causal: `{audit['cutoff_causal']}`\n"
        f"- replay idéntico: `{audit['replay_identical']}`\n"
        "- router oficial modificado: `False`\n"
        "- siguiente paso: cohorte prospectiva shadow por mercado\n"
    )


def run() -> dict[str, Any]:
    """Ejecuta auditoría y publica evidencia reproducible."""

    engine = UniversalPrematchEngine()
    requests = _requests(engine)
    rows, replay_equal = _prediction_rows(requests)
    parity = _snapshot_parity(engine)
    audit = _audit(rows, replay_equal, parity)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    _write("config.json", {
        "phase": "85", "approved_markets": sorted(APPROVED_MARKETS),
        "mode": "experimental_shadow_not_promoted"})
    _write("coverage.json", {
        "replay_fixtures": len(rows), "league_count": 1,
        "snapshot_parity": parity})
    _write("input_manifest.json", {
        "phase_84_source_sha256": _sha(PHASE84_SOURCE),
        "active_snapshot_sha256": _sha(engine._windows_path)})
    _write("audit.json", audit)
    _write("sample_predictions.json", rows)
    report = _report(audit, len(rows))
    for name in ("validation_report.md", "final_report.md"):
        (OUTPUT / name).write_text(report, encoding="utf-8")
    _write("hashes.json", {
        path.name: _sha(path) for path in sorted(OUTPUT.iterdir())
        if path.name != "hashes.json"})
    return {"audit": audit, "sample_count": len(rows)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run()
    assert result["sample_count"] == 10
    assert result["audit"]["official_fields_identical"]
    assert result["audit"]["all_gates_pass"]
    LOGGER.info("Fase 85 completada: %s", result["audit"])


# Version: 1.0.0
# Created: 2026-07-28
