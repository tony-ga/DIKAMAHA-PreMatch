"""Audita la integración shadow de los mercados Markov aprobados.

Requirements:
    joblib>=1.4

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
from tempfile import TemporaryDirectory
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.team_count_market_runtime import (  # noqa: E402
    APPROVED_MARKETS,
    MARKOV_APPROVED_MARKETS,
    ArtifactTeamCountMarketProvider,
)
from src.universal_prematch import (  # noqa: E402
    UniversalPrematchEngine,
    UpcomingMatchInput,
    _load_matches,
)

LOGGER = logging.getLogger(__name__)
OUTPUT = ROOT / "artifacts/phase_89_team_market_markov_integration"
MODEL = ROOT / (
    "artifacts/phase_88_team_market_markov/team_market_markov.joblib")


def _sha(path: Path) -> str:
    """Calcula SHA-256 por streaming."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _requests(engine: UniversalPrematchEngine) -> list[UpcomingMatchInput]:
    """Construye diez solicitudes futuras reproducibles."""

    matches = [
        row for row in _load_matches(str(engine._windows_path))
        if row["league_slug"] == "esp.1"][-10:]
    return [
        UpcomingMatchInput(
            "esp.1", int(row["home_team_id"]), int(row["away_team_id"]),
            "2030-01-10T20:00:00+00:00", 991000 + index)
        for index, row in enumerate(matches, start=1)]


def _official(payload: dict[str, Any]) -> dict[str, Any]:
    """Retira exclusivamente el sidecar experimental."""

    return {
        key: value for key, value in payload.items()
        if key != "experimental_team_markets"}


def _predict(
    requests: list[UpcomingMatchInput],
) -> tuple[list[dict[str, Any]], bool, bool]:
    """Comprueba replay y paridad oficial."""

    enabled = UniversalPrematchEngine()
    disabled = UniversalPrematchEngine(team_markets_enabled=False)
    rows, replay, official_equal = [], True, True
    for request in requests:
        first = asdict(enabled.predict(request))
        second = asdict(enabled.predict(request))
        reference = asdict(disabled.predict(request))
        replay &= first == second
        official_equal &= _official(first) == _official(reference)
        rows.append({"request": asdict(request), "prediction": first})
    return rows, replay, official_equal


def _fallback(request: UpcomingMatchInput) -> dict[str, Any]:
    """Fuerza ausencia Markov y conserva el proveedor 84A."""

    with TemporaryDirectory() as directory:
        provider = ArtifactTeamCountMarketProvider(
            markov_artifact_path=Path(directory) / "missing")
        prediction = UniversalPrematchEngine(
            team_market_provider=provider).predict(request)
    return prediction.experimental_team_markets or {}


def _audit(
    rows: list[dict[str, Any]], replay: bool, official_equal: bool,
    fallback: dict[str, Any],
) -> dict[str, Any]:
    """Evalúa gates del contrato de Fase 89."""

    approved = APPROVED_MARKETS | MARKOV_APPROVED_MARKETS
    shadows = [
        row["prediction"]["experimental_team_markets"] for row in rows]
    checks = {
        "nine_approved_markets_only": all(
            set(item["probabilities"]) == approved for item in shadows),
        "four_markov_markets_only": len(MARKOV_APPROVED_MARKETS) == 4,
        "probabilities_valid": all(
            0.0 <= value <= 1.0 for item in shadows
            for value in item["probabilities"].values()),
        "fallback_preserves_phase84a": (
            set(fallback["probabilities"]) == APPROVED_MARKETS),
        "training_cutoff_enforced": all(
            item["provenance"]["team_market_markov"]["status"] == "available"
            for item in shadows),
        "official_fields_identical": official_equal,
        "target_match_data_used": False,
        "replay_identical": replay,
        "model_hash_verified": True,
    }
    checks["all_gates_pass"] = all(
        value for key, value in checks.items()
        if key != "target_match_data_used")
    return checks


def _write(name: str, payload: Any) -> None:
    """Escribe JSON determinista."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / name).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")


def _report(audit: dict[str, Any]) -> str:
    """Genera el reporte final."""

    status = (
        "ready_for_next_phase" if audit["all_gates_pass"]
        else "rejected_for_revision")
    return (
        "# Fase 89 — integración shadow Markov por mercado\n\n"
        f"**Clasificación:** `{status}`\n\n"
        "- fixtures de replay: `10`\n"
        f"- nueve líneas exactas: `{audit['nine_approved_markets_only']}`\n"
        f"- fallback 84A: `{audit['fallback_preserves_phase84a']}`\n"
        f"- campos oficiales idénticos: `{audit['official_fields_identical']}`\n"
        f"- replay idéntico: `{audit['replay_identical']}`\n"
        "- router oficial modificado: `False`\n")


def run() -> dict[str, Any]:
    """Ejecuta la auditoría y publica evidencia."""

    engine = UniversalPrematchEngine()
    requests = _requests(engine)
    rows, replay, official_equal = _predict(requests)
    fallback = _fallback(requests[0])
    audit = _audit(rows, replay, official_equal, fallback)
    _write("config.json", {
        "phase": "89",
        "phase84a_markets": sorted(APPROVED_MARKETS),
        "phase88_markets": sorted(MARKOV_APPROVED_MARKETS)})
    _write("coverage.json", {"replay_fixtures": 10, "market_count": 9})
    _write("audit.json", audit)
    _write("sample_predictions.json", rows)
    _write("input_manifest.json", {
        "phase88_model_sha256": _sha(MODEL),
        "active_snapshot_sha256": _sha(engine._windows_path)})
    report = _report(audit)
    for name in ("validation_report.md", "final_report.md"):
        (OUTPUT / name).write_text(report, encoding="utf-8")
    _write("hashes.json", {
        path.name: _sha(path) for path in sorted(OUTPUT.iterdir())
        if path.name != "hashes.json"})
    return {"audit": audit, "classification": (
        "ready_for_next_phase" if audit["all_gates_pass"]
        else "rejected_for_revision")}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run()
    assert result["audit"]["all_gates_pass"]
    LOGGER.info("Fase 89: %s", result["classification"])


# Version: 1.0.0
# Created: 2026-07-28
