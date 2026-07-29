"""Pruebas del descubrimiento multi-liga."""

from scripts.run_multileague_discovery import _dates


def test_dates_are_inclusive() -> None:
    """La fecha final se incluye en el recorrido."""

    assert _dates("20251201", "20251203") == ["20251201", "20251202", "20251203"]


def test_date_window_is_bounded() -> None:
    """El discovery evita rangos excesivos por corrida."""

    try:
        _dates("20240101", "20260101")
    except ValueError as error:
        assert str(error) == "date_range_exceeds_366_days"
    else:
        raise AssertionError("expected bounded date range")

# Version: 1.0.0
# Created: 2026-07-26
