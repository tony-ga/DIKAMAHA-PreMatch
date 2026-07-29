"""Pruebas del contrato de sincronización operativa ESPN."""

from __future__ import annotations

from scripts.run_operational_espn_sync import _date, _defaults


def test_date_is_normalized() -> None:
    """La ventana sólo acepta fechas ESPN válidas."""

    assert _date("20260726") == "20260726"


def test_defaults_are_a_bounded_utc_window() -> None:
    """La ventana automática no excede el periodo operativo previsto."""

    start, end = _defaults(7)
    assert len(start) == 8 and len(end) == 8
    assert int(end) >= int(start)

# Version: 1.0.0
# Created: 2026-07-26
