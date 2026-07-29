"""Integración explícita de solo lectura del generador histórico.

No se ejecuta sin ``--run-postgres`` y escribe solo en un directorio temporal.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "src" / "generate_match_features_dry_run.py"
pytestmark = pytest.mark.postgres


def test_generator_read_only_contract() -> None:
    """Verifica el contrato del generador con PostgreSQL autorizado explícitamente."""

    with tempfile.TemporaryDirectory() as temporary:
        result = subprocess.run(
            [sys.executable, str(GENERATOR), "--out-dir", temporary],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    payload = json.loads(result.stdout)
    assert payload["postgres_before"] == payload["postgres_after"]
    assert payload["eligible_for_training"] == 331
