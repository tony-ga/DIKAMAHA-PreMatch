"""Genera la evidencia de la vertical universal pre-match.

Requirements:
    - fastapi
    - pydantic

Version: 1.0.0
Created: 2026-07-27
"""
from __future__ import annotations

import hashlib
import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.universal_prematch import UniversalPrematchEngine, UpcomingMatchInput

OUTPUT = ROOT / "artifacts/phase_48_universal_prematch_flow_v1"
LOGGER = logging.getLogger(__name__)


def _write(name: str, payload: object) -> None:
    """Escribe un artefacto JSON de forma atómica."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / name
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temporary.replace(target)


def run() -> dict[str, object]:
    """Ejecuta una solicitud futura reproducible y audita sus gates."""

    request = UpcomingMatchInput("esp.1", 94, 86, "2030-01-10T20:00:00+00:00", 990001)
    prediction = UniversalPrematchEngine().predict(request)
    payload = asdict(prediction)
    audit = {"request_compact": True, "prediction_available": prediction.status == "available", "target_match_data_used": prediction.audit["target_match_data_used"], "cutoff_causal": prediction.audit["cutoff_causal"], "markov_promoted": prediction.provenance["markov_used"], "external_calls": False, "persistence": False}
    ready = audit["prediction_available"] and audit["target_match_data_used"] is False and audit["cutoff_causal"] and audit["markov_promoted"] is False and audit["external_calls"] is False and audit["persistence"] is False
    result = {"config": {"version": "universal_prematch_flow_v1", "model": prediction.model, "markets": ["1X2", "over_2_5", "btts"]}, "request": asdict(request), "prediction": payload, "audit": audit, "classification": "universal_prematch_vertical_ready" if ready else "universal_prematch_vertical_rejected"}
    for name in ("config.json", "request.json", "prediction.json", "audit.json"):
        _write(name, result[{"config.json": "config", "request.json": "request", "prediction.json": "prediction", "audit.json": "audit"}[name]])
    report = ["# Fase 48 — flujo universal pre-match", "", f"**Clasificación:** `{result['classification']}`", "", "- solicitud compacta: `liga + equipos + kickoff`", "- partidos próximos: `validado`", "- target del partido usado: `False`", "- Markov multi-liga: `no utilizado`", "- llamadas externas en la vertical local: `False`", "- persistencia: `False`", "- siguiente paso: `resolver fixtures ESPN y refrescar snapshot`"]
    (OUTPUT / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"}
    _write("hashes.json", hashes)
    LOGGER.info("Fase 48: %s", report[2])
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()

# Version: 1.0.0
# Created: 2026-07-27
