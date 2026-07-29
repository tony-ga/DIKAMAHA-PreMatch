"""Pruebas unitarias de reglas causales de `state_labeling v1`."""
from __future__ import annotations

from src.state_labeling_v1 import StateLabelingConfig, label


def _row(**changes: int | str) -> dict[str, int | str]:
    """Construye una ventana observada con métricas neutras."""
    row: dict[str, int | str] = {"event_coverage": "observed_timeline", "pressure": 0, "pressure_conceded": 0, "fouls": 0, "yellow_cards": 0, "red_cards": 0, "goal_difference_start": 0}
    row.update(changes)
    return row


def test_pressure_label_uses_current_window_only() -> None:
    """Presión requiere volumen propio y margen frente al rival."""
    state, values = label(_row(pressure=3, pressure_conceded=1), StateLabelingConfig())
    assert state == "presion"
    assert values["pressure_margin"] == 2


def test_retreat_requires_existing_advantage() -> None:
    """Repliegue no se infiere sin ventaja al inicio de ventana."""
    assert label(_row(pressure=1, pressure_conceded=2), StateLabelingConfig())[0] == "equilibrio"
    assert label(_row(goal_difference_start=1, pressure=1, pressure_conceded=2), StateLabelingConfig())[0] == "repliegue"


def test_disorganization_has_priority_over_other_states() -> None:
    """Una roja propia domina cualquier condición simultánea de presión."""
    state, _ = label(_row(pressure=5, pressure_conceded=1, red_cards=1), StateLabelingConfig())
    assert state == "desorganizacion"


def test_unknown_preserves_missing_coverage() -> None:
    """Cobertura no observable no recibe etiqueta por defecto."""
    assert label(_row(event_coverage="unknown"), StateLabelingConfig())[0] == "unknown"

