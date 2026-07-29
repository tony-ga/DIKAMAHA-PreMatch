"""Pruebas de alineación causal de inputs pre-match."""

from __future__ import annotations

from src.phase_32_prematch_candidate_preparation import _prepare


def _row(match_id: int, cutoff: str = "2026-01-01T12:00:00+00:00") -> dict:
    """Construye una fila pre-match mínima."""

    return {"match_id": match_id, "cutoff_ts": cutoff, "target_match_data_used": False, "target_match_statistics_used": False}


def test_missing_or_misaligned_inputs_are_rejected() -> None:
    """No prepara una fila sin feature o con cutoff distinto."""

    prepared, rejected = _prepare([{"match_id": 1}], {1: _row(1)}, {1: _row(1, "2026-01-01T13:00:00+00:00")})
    assert prepared == []
    assert "cutoff_mismatch" in rejected[0]["reasons"]


def test_aligned_causal_inputs_are_prepared() -> None:
    """Una fila causal alineada queda lista sin generar predicción."""

    prepared, rejected = _prepare([{"match_id": 1}], {1: _row(1)}, {1: _row(1)})
    assert len(prepared) == 1
    assert rejected == []

# Version: 1.0.0
# Created: 2026-07-26
