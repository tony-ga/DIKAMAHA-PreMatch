"""Audita el contrato legible de mercados para interfaz.

Version: 1.0.0
Created: 2026-07-29
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

from scripts.run_phase_89_team_market_markov_integration import (  # noqa: E402
    _official,
    _requests,
)
from src.team_count_market_runtime import (  # noqa: E402
    APPROVED_MARKETS,
    MARKOV_APPROVED_MARKETS,
)
from src.universal_prematch import UniversalPrematchEngine  # noqa: E402

LOGGER = logging.getLogger(__name__)
OUTPUT = ROOT / "artifacts/phase_93_user_market_contract"


def _sha(path: Path) -> str:
    """Calcula SHA-256 por streaming."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write(name: str, payload: Any) -> None:
    """Escribe JSON determinista."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / name).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")


def run() -> dict[str, Any]:
    """Compara contrato habilitado, replay y salida oficial."""

    enabled = UniversalPrematchEngine()
    disabled = UniversalPrematchEngine(team_markets_enabled=False)
    rows, replay, official_equal = [], True, True
    for request in _requests(enabled):
        first = asdict(enabled.predict(request))
        second = asdict(enabled.predict(request))
        reference = asdict(disabled.predict(request))
        replay &= first == second
        official_equal &= _official(first) == _official(reference)
        rows.append(first["experimental_team_markets"])
    audit = _audit(rows, replay, official_equal)
    return _publish(rows, audit, enabled._windows_path)


def _audit(
    rows: list[dict[str, Any]], replay: bool, official_equal: bool,
) -> dict[str, bool]:
    """Valida equivalencia entre vista y diccionarios."""

    approved = APPROVED_MARKETS | MARKOV_APPROVED_MARKETS
    checks = {
        "nine_markets_present": all(
            set(row["probabilities"]) == approved for row in rows),
        "view_keys_exact": all(
            {item["key"] for item in row["user_market_view"]} == approved
            for row in rows),
        "view_probabilities_equal": all(
            item["probability"] == row["probabilities"][item["key"]]
            and item["baseline_probability"]
            == row["baseline_probabilities"][item["key"]]
            for row in rows for item in row["user_market_view"]),
        "all_statuses_experimental": all(
            item["status"] == "experimental_shadow_not_promoted"
            for row in rows for item in row["user_market_view"]),
        "official_fields_identical": official_equal,
        "replay_identical": replay,
    }
    checks["all_gates_pass"] = all(checks.values())
    return checks


def _publish(
    rows: list[dict[str, Any]], audit: dict[str, bool], snapshot: Path,
) -> dict[str, Any]:
    """Publica artefactos completos de integración."""

    classification = (
        "ready_for_next_phase" if audit["all_gates_pass"]
        else "rejected_for_revision")
    _write("config.json", {
        "version": "user_market_view_v1",
        "fields": [
            "key", "metric", "team_side", "period", "line",
            "probability", "baseline_probability", "source_model", "status"]})
    _write("coverage.json", {
        "fixtures": len(rows), "markets_per_fixture": 9})
    _write("audit.json", {**audit, "classification": classification})
    _write("sample_user_markets.json", rows)
    _write("input_manifest.json", {"snapshot_sha256": _sha(snapshot)})
    report = (
        "# Fase 93 — contrato de mercados para usuario\n\n"
        f"**Clasificación:** `{classification}`\n\n"
        "- fixtures de replay: `10`\n"
        "- mercados por fixture: `9`\n"
        f"- vista equivalente: `{audit['view_probabilities_equal']}`\n"
        f"- salida oficial idéntica: `{audit['official_fields_identical']}`\n"
        f"- replay idéntico: `{audit['replay_identical']}`\n")
    for name in ("validation_report.md", "final_report.md"):
        (OUTPUT / name).write_text(report, encoding="utf-8")
    _write("hashes.json", {
        path.name: _sha(path) for path in sorted(OUTPUT.iterdir())
        if path.name != "hashes.json"})
    return {"classification": classification, "audit": audit}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run()
    assert result["audit"]["all_gates_pass"]
    LOGGER.info("Fase 93: %s", result["classification"])


# Version: 1.0.0
# Created: 2026-07-29
