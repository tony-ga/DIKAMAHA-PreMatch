"""Pruebas de la consulta de fiabilidad de la escalera auditada.

La asimetría deliberada con `MetricCoverage` es lo que estas pruebas
protegen: un mercado que funciona no debe caer por falta de un artefacto
(`MetricCoverage` degrada abierto), pero una línea no auditada tampoco debe
mostrarse como si lo estuviera (`LadderReliabilityView` degrada cerrado).
"""

from __future__ import annotations

import json
from pathlib import Path

from src.ladder_reliability_view import LadderReliabilityView


def _cell(**overrides: object) -> dict[str, object]:
    base = {
        "metric": "shots", "team_side": "home", "line": 10.5,
        "verdict": "model_edge", "observed_rate": 0.62, "sample": 1768,
    }
    base.update(overrides)
    return base


def _view(tmp_path: Path, cells: list[dict[str, object]]) -> LadderReliabilityView:
    path = tmp_path / "ladder_reliability.json"
    path.write_text(json.dumps({
        "version": "ladder_reliability_v1",
        "source_artifact": "phase_84a_team_count_markets",
        "summary": {}, "cells": cells,
    }), encoding="utf-8")
    return LadderReliabilityView(path)


def test_publishable_verdicts_are_returned(tmp_path: Path) -> None:
    view = _view(tmp_path, [_cell(verdict="model_edge")])

    result = view.verdict("shots", "home", 10.5)

    assert result is not None
    assert result["verdict"] == "model_edge"


def test_base_rate_driven_is_also_publishable(tmp_path: Path) -> None:
    view = _view(tmp_path, [_cell(verdict="base_rate_driven")])

    assert view.verdict("shots", "home", 10.5) is not None


def test_miscalibrated_lines_are_never_returned(tmp_path: Path) -> None:
    view = _view(tmp_path, [_cell(verdict="miscalibrated")])

    assert view.verdict("shots", "home", 10.5) is None


def test_insufficient_sample_lines_are_never_returned(tmp_path: Path) -> None:
    view = _view(tmp_path, [_cell(verdict="insufficient_sample")])

    assert view.verdict("shots", "home", 10.5) is None


def test_unaudited_line_returns_none_not_an_assumed_default(
    tmp_path: Path,
) -> None:
    view = _view(tmp_path, [_cell(line=10.5)])

    assert view.verdict("shots", "home", 99.5) is None


def test_missing_artifact_degrades_closed(tmp_path: Path) -> None:
    """Al revés que `MetricCoverage`: sin evidencia, no se publica nada."""

    view = LadderReliabilityView(tmp_path / "no_existe.json")

    assert view.verdict("shots", "home", 10.5) is None
    assert view.available() is False


def test_wrong_version_degrades_closed(tmp_path: Path) -> None:
    path = tmp_path / "ladder_reliability.json"
    path.write_text(json.dumps({
        "version": "ladder_reliability_v0_stale", "cells": [_cell()]}),
        encoding="utf-8")

    view = LadderReliabilityView(path)

    assert view.verdict("shots", "home", 10.5) is None


def test_malformed_cells_degrade_closed(tmp_path: Path) -> None:
    path = tmp_path / "ladder_reliability.json"
    path.write_text(json.dumps({
        "version": "ladder_reliability_v1", "cells": "not-a-list"}),
        encoding="utf-8")

    view = LadderReliabilityView(path)

    assert view.verdict("shots", "home", 10.5) is None


def test_available_reflects_whether_any_line_survived(tmp_path: Path) -> None:
    # Sólo hay una celda y no es publicable: el índice queda vacío.
    view = _view(tmp_path, [_cell(verdict="miscalibrated")])

    assert view.available() is False


def test_loads_only_once(tmp_path: Path) -> None:
    """El artefacto se lee una sola vez, igual que `MetricCoverage`."""

    view = _view(tmp_path, [_cell()])
    assert view.verdict("shots", "home", 10.5) is not None

    (tmp_path / "ladder_reliability.json").unlink()

    # Ya está indexado: borrar el archivo después no debe cambiar el resultado.
    assert view.verdict("shots", "home", 10.5) is not None


# Version: 1.0.0
# Created: 2026-08-12
