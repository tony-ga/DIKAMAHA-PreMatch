"""Integración PostgreSQL staging explícita y read-only."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/run_phase_7_5_postgres_readonly.py"
pytestmark = pytest.mark.postgres


def _execute(output: str) -> dict[str, object]:
    """Ejecuta el runner con DATABASE_URL heredada sin imprimirla."""

    result = subprocess.run(
        [sys.executable, str(RUNNER), "--out-dir", output],
        cwd=ROOT, env=os.environ, text=True, capture_output=True, check=True,
    )
    assert "postgresql://" not in result.stdout + result.stderr
    return json.loads(Path(output, "manifest.json").read_text())


def test_postgres_readonly_staging_contract() -> None:
    """Comprueba read-only, conteos, cierre y determinismo."""

    with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
        manifest_first = _execute(first)
        manifest_second = _execute(second)
        counts = json.loads(Path(first, "counts_before_after.json").read_text())
        readonly = json.loads(Path(first, "readonly_audit.json").read_text())
        capabilities = json.loads(Path(first, "database_capabilities.json").read_text())
        first_hashes = json.loads(Path(first, "hashes.json").read_text())["artifacts"]
        second_hashes = json.loads(Path(second, "hashes.json").read_text())["artifacts"]
    assert manifest_first["decision"] == manifest_second["decision"] == "postgres_readonly_verified"
    assert counts["before"] == counts["after"]
    assert capabilities["transaction_read_only"] == "on"
    assert readonly["sql_writes"] == readonly["ddl_statements"] == 0
    assert readonly["connections_closed"] is True
    assert readonly["deterministic_replay"] is True
    assert first_hashes == second_hashes


# Version: 1.0.0
# Created: 2026-07-16
