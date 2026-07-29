"""Verifica replay exacto de la reauditoría predictiva de Fase 76.

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

from scripts.run_phase_76_predictive_state_reaudit import OUTPUT, run  # noqa: E402

LOGGER = logging.getLogger(__name__)
FILES = ("metrics.json", "state_assignments.jsonl", "state_cards.json")


def _digest(path: Path) -> str:
    """Calcula SHA-256 binario."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot() -> dict[str, str]:
    """Captura hashes determinantes de la reauditoría."""

    return {name: _digest(OUTPUT / name) for name in FILES}


def _write(name: str, value: Any) -> None:
    """Publica JSON estable."""

    (OUTPUT / name).write_text(
        json.dumps(value, indent=2, sort_keys=True), encoding="utf-8"
    )


def _rehash() -> None:
    """Actualiza el ledger después del replay."""

    hashes = {path.name: _digest(path) for path in sorted(OUTPUT.iterdir())
              if path.is_file() and path.name != "hashes.json"}
    _write("hashes.json", hashes)


def verify() -> dict[str, Any]:
    """Compara una salida existente contra un replay completo."""

    before = _snapshot()
    run()
    after = _snapshot()
    result = {"exact_match": before == after, "before": before,
              "after": after, "metric_max_abs_difference": 0.0
              if before == after else None}
    _write("reproducibility.json", result)
    _rehash()
    LOGGER.info("Replay Fase 76 predictiva: %s", result["exact_match"])
    return result


def main() -> int:
    """Ejecuta el gate de replay."""

    return 0 if verify()["exact_match"] else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0 - 2026-07-27
