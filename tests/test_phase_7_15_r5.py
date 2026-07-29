"""Pruebas unitarias del contrato de rango R5 sin red ni escrituras."""
from datetime import date

import pytest

from src.espn_phase_7_15_r5 import _cutoff_date, _dates, _state, validate_range


def test_range_is_inclusive_and_compatibility_shape() -> None:
    """Una fecha y un rango producen las fechas esperadas."""
    first, last = validate_range("20251026", "20251028")
    assert _dates(first, last) == ["20251026", "20251027", "20251028"]
    assert validate_range("20251026", "20251026") == (date(2025, 10, 26), date(2025, 10, 26))


def test_range_validation_rejects_inverted_and_malformed() -> None:
    """El runner rechaza rangos inválidos antes de usar red."""
    with pytest.raises(ValueError, match="start_date_after"):
        validate_range("20251029", "20251028")
    with pytest.raises(ValueError, match="invalid_date"):
        validate_range("2025-10-26", "20251026")


def test_lifecycle_states_are_explicit() -> None:
    """Los estados ESPN se separan sin inferencias sobre goles."""
    assert _state({"complete": True, "provider_status": "post"}) == "completed"
    assert _state({"complete": False, "provider_status": "in_progress"}) == "live"
    assert _state({"complete": False, "provider_status": "pre"}) == "scheduled"
    assert _state({"complete": False, "provider_status": "unknown"}) == "incomplete"


def test_cutoff_is_explicit_and_independent_of_system_date() -> None:
    """El corte congelado acepta ISO y rechaza fechas ambiguas."""
    assert _cutoff_date("2025-10-26").isoformat() == "2025-10-26"
    with pytest.raises(ValueError, match="invalid_prospective_cutoff_date"):
        _cutoff_date("26/10/2025")
