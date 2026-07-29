"""Verifica reproducción exacta de métricas y predicciones de Fase 75.

Version: 1.0.0
Created: 2026-07-27
"""
from __future__ import annotations

import hashlib
import json
import logging
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_phase_75_temporal_baseline_targets import OUTPUT, run  # noqa: E402

LOGGER = logging.getLogger(__name__)


def _digest(path: Path) -> str:
    """Calcula SHA-256 binario."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot() -> dict[str, str]:
    """Captura hashes de las salidas determinantes."""

    return {name: _digest(OUTPUT / name) for name in (
        "metrics.json", "predictions.jsonl", "inference_features.jsonl",
        "targets.jsonl",
    )}


def _write(name: str, value: Any) -> None:
    """Publica JSON estable."""

    (OUTPUT / name).write_text(
        json.dumps(value, indent=2, sort_keys=True), encoding="utf-8"
    )


def _rehash() -> None:
    """Actualiza el ledger de hashes después de la verificación."""

    hashes = {
        path.name: _digest(path) for path in sorted(OUTPUT.iterdir())
        if path.is_file() and path.name != "hashes.json"
    }
    _write("hashes.json", hashes)


def verify() -> dict[str, Any]:
    """Ejecuta dos replays y exige identidad binaria."""

    run()
    first = _snapshot()
    run()
    second = _snapshot()
    result = {
        "exact_match": first == second,
        "metric_tolerance": 1e-6,
        "metric_max_abs_difference": 0.0 if first == second else None,
        "first": first,
        "second": second,
    }
    _write("reproducibility.json", result)
    _rehash()
    LOGGER.info("Reproducibilidad Fase 75: %s", result["exact_match"])
    return result


def main() -> int:
    """Ejecuta el gate de reproducibilidad."""

    return 0 if verify()["exact_match"] else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0 - 2026-07-27
