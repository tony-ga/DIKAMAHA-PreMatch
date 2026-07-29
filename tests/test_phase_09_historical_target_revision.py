"""Pruebas unitarias de targets temporales v2."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from src.phase_09_historical_target_revision import derive_targets

ROOT = Path(__file__).resolve().parents[1]


def _windows() -> list[dict[str, object]]:
    """Construye un partido sintético local que empata tras ir perdiendo."""

    rows = []
    for is_home in (True, False):
        for index in range(6):
            goals = 1 if (not is_home and index == 1) or (is_home and index == 3) else 0
            rows.append({"match_id": 9001, "is_home": is_home, "window_index": index, "goals": goals})
    return rows


def test_recovery_targets_use_half_time_opportunity() -> None:
    """Una reacción se cuenta sólo si el equipo perdía al descanso."""

    row = derive_targets(_windows(), "synthetic")[0]
    assert row["home_trailing_at_half"] is True
    assert row["home_recovery_draw_or_win"] is True
    assert row["home_reaches_level_after_half"] is True
    assert row["home_comeback_win"] is False
    assert row["away_recovery_draw_or_win"] is False


def test_phase_09_hashes_and_coverage_are_consistent() -> None:
    """Los artefactos publicados conservan hashes y conteos esperados."""

    output = ROOT / "artifacts/phase_09_historical_target_revision"
    hashes = json.loads((output / "hashes.json").read_text(encoding="utf-8"))
    assert all(hashlib.sha256((output / name).read_bytes()).hexdigest() == digest for name, digest in hashes.items())
    coverage = json.loads((output / "coverage.json").read_text(encoding="utf-8"))
    assert coverage["staging_extension_candidate"]["match_count"] == 44
    assert coverage["staging_extension_candidate"]["window_count"] == 528


# Version: 1.0.0
# Created: 2026-07-26
