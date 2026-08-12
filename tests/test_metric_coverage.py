"""Pruebas del guard de cobertura de métricas.

Incidente real que motiva este módulo: el proveedor no publica córners para
varias competiciones servidas y el pipeline los almacenó como cero, de modo
que el modelo aprendió ~0.18 córners esperados en `esp.2`/`eng.3-5` y la
Mini App llegaba a declarar "Menos de 4.5 córners: 99.99%" en Segunda
División sobre un evento que ronda el 50%.

La trampa que estas pruebas vigilan es la simétrica: una regla ingenua de
"muchos ceros = dato ausente" habría suprimido el mercado de tarjetas rojas,
que es cero el 89% de las veces por razones legítimas del fútbol.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.metric_coverage import (
    MetricCoverage,
    build_coverage_map,
    observation_is_absent,
)


def _rows(league: str, actuals: list[dict[str, float]]) -> list[dict[str, object]]:
    return [{"league_slug": league, "actual": row} for row in actuals]


def _healthy(n: int) -> list[dict[str, float]]:
    """Liga con cobertura real: córners y tiros siempre presentes."""

    return [
        {"corners": 5.0, "corners_first_half": 2.0, "shots": 11.0,
         "shots_on_target": 4.0, "yellow_cards": 2.0, "red_cards": 0.0}
        for _ in range(n)
    ]


def _corner_blind(n: int) -> list[dict[str, float]]:
    """Liga sin córners publicados, con el resto del bloque presente."""

    return [
        {"corners": 0.0, "corners_first_half": 0.0, "shots": 11.0,
         "shots_on_target": 4.0, "yellow_cards": 2.0, "red_cards": 0.0}
        for _ in range(n)
    ]


def test_absent_corners_are_detected_as_missing_coverage() -> None:
    coverage = build_coverage_map(_rows("esp.2", _corner_blind(50)))

    metrics = coverage["leagues"]["esp.2"]["metrics"]
    assert metrics["corners"]["status"] == "absent"
    assert metrics["corners_first_half"]["status"] == "absent"


def test_healthy_league_keeps_every_metric_covered() -> None:
    coverage = build_coverage_map(_rows("esp.1", _healthy(50)))

    metrics = coverage["leagues"]["esp.1"]["metrics"]
    assert metrics["corners"]["status"] == "covered"
    assert metrics["shots"]["status"] == "covered"


def test_red_cards_are_never_suppressed_despite_being_mostly_zero() -> None:
    """El cero de una tarjeta roja es una observación válida, no un hueco."""

    rows = _healthy(50)
    for row in rows:
        row["red_cards"] = 0.0

    coverage = build_coverage_map(_rows("esp.1", rows))

    entry = coverage["leagues"]["esp.1"]["metrics"]["red_cards"]
    assert entry["zero_rate"] == 1.0
    assert entry["status"] == "covered"


def test_yellow_cards_are_never_suppressed_despite_being_mostly_zero() -> None:
    rows = _healthy(50)
    for row in rows:
        row["yellow_cards"] = 0.0

    coverage = build_coverage_map(_rows("esp.1", rows))

    assert coverage["leagues"]["esp.1"]["metrics"]["yellow_cards"][
        "status"] == "covered"


def test_small_sample_never_asserts_absence() -> None:
    """Sin muestra no se afirma ausencia: el guard exige evidencia positiva."""

    coverage = build_coverage_map(_rows("esp.super_cup", _corner_blind(6)))

    assert coverage["leagues"]["esp.super_cup"]["metrics"]["corners"][
        "status"] == "insufficient_evidence"


def test_observation_absence_follows_the_statistics_block() -> None:
    """`shots == 0` delata que el bloque del proveedor no llegó."""

    missing = {"shots": 0.0, "corners": 0.0, "shots_on_target": 0.0,
               "yellow_cards": 2.0, "red_cards": 1.0}
    present = {"shots": 11.0, "corners": 5.0, "shots_on_target": 4.0,
               "yellow_cards": 2.0, "red_cards": 0.0}

    assert observation_is_absent(missing, "corners") is True
    assert observation_is_absent(missing, "shots_on_target") is True
    assert observation_is_absent(present, "corners") is False
    # Las tarjetas no dependen de ese bloque y se conservan siempre.
    assert observation_is_absent(missing, "yellow_cards") is False
    assert observation_is_absent(missing, "red_cards") is False


def test_lookup_reports_absent_metrics_for_a_league(tmp_path: Path) -> None:
    artifact = tmp_path / "coverage_map.json"
    artifact.write_text(json.dumps(build_coverage_map(
        _rows("esp.2", _corner_blind(50)) + _rows("esp.1", _healthy(50)))),
        encoding="utf-8")
    coverage = MetricCoverage(artifact)

    assert coverage.is_absent("esp.2", "corners") is True
    assert coverage.is_absent("esp.1", "corners") is False
    assert coverage.absent_metrics("esp.2") == frozenset(
        {"corners", "corners_first_half"})
    assert coverage.absent_metrics("esp.1") == frozenset()


def test_missing_artifact_never_suppresses_a_working_market(
    tmp_path: Path,
) -> None:
    """Ausencia de evidencia no es evidencia de ausencia.

    Si el artefacto falta o está corrupto, el guard debe dejar pasar el
    mercado: suprimir exige demostrar que el dato no existe, no lo contrario.
    """

    coverage = MetricCoverage(tmp_path / "no_existe.json")

    assert coverage.is_absent("esp.2", "corners") is False
    assert coverage.absent_metrics("esp.2") == frozenset()


def test_unknown_league_is_not_suppressed(tmp_path: Path) -> None:
    artifact = tmp_path / "coverage_map.json"
    artifact.write_text(
        json.dumps(build_coverage_map(_rows("esp.1", _healthy(50)))),
        encoding="utf-8")
    coverage = MetricCoverage(artifact)

    assert coverage.is_absent("liga.desconocida", "corners") is False


# Version: 1.0.0
# Created: 2026-08-12
