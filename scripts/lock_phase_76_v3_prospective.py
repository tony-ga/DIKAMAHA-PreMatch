"""Sella el candidato v3 antes de iniciar su cohorte prospectiva.

Version: 1.0.0
Created: 2026-07-28
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "artifacts/phase_76_domain_robust_reaudit/model_parameters.json"
OUTPUT = ROOT / "artifacts/phase_76_v3_prospective_lock"
LOCK = OUTPUT / "lock.json"
LOGGER = logging.getLogger(__name__)


def _hash(path: Path) -> str:
    """Calcula SHA-256 de un archivo."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(name: str, value: Any) -> None:
    """Publica JSON estable."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / name).write_text(
        json.dumps(value, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def run() -> dict[str, Any]:
    """Crea una vez el lock o verifica su inmutabilidad."""

    model_hash = _hash(MODEL)
    if LOCK.exists():
        existing = json.loads(LOCK.read_text(encoding="utf-8"))
        if existing["model_parameters_sha256"] != model_hash:
            raise RuntimeError("prospective_lock_model_hash_mismatch")
        return existing
    lock = {
        "model": "predictive_latent_state_v3",
        "model_parameters_sha256": model_hash,
        "cutoff_utc": datetime.now(timezone.utc).isoformat(),
        "minimum_matches": 200,
        "minimum_leagues": 10,
        "semantic_spread": 0.05,
        "minimum_occupancy": 0.05,
        "minimum_league_stability": 0.75,
        "minimum_duration_improvement": 0.0,
        "maximum_score_mismatch_rate": 0.02,
        "features_frozen": True,
        "thresholds_frozen": True,
    }
    _write("lock.json", lock)
    _write("input_manifest.json", {
        "model_parameters": str(MODEL.relative_to(ROOT)),
        "model_parameters_sha256": model_hash,
    })
    _write("hashes.json", {
        "lock.json": _hash(LOCK),
        "input_manifest.json": _hash(OUTPUT / "input_manifest.json"),
    })
    LOGGER.info("Lock prospectivo v3 creado en %s", lock["cutoff_utc"])
    return lock


def main() -> int:
    """Ejecuta el sellado idempotente."""

    run()
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0 - 2026-07-28
