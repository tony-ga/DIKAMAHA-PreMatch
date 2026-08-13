"""Pruebas de la selección de línea por grupo de la escalera auditada.

Cubre la banda de confianza, el fallback cuando ninguna línea cae en ella, y
que la elección respete exactamente un pick por grupo sin inventar datos.
"""

from __future__ import annotations

from src.ladder_pick_selection import (
    CONFIDENCE_CEILING, CONFIDENCE_FLOOR, select_ladder_picks,
)


def _line(line: float, over: float, reliability: str = "model_edge",
          observed: float | None = None, sample: int = 500) -> dict:
    return {
        "line": line, "over_probability": over,
        "under_probability": 1.0 - over, "reliability": reliability,
        "observed_rate_historical": observed if observed is not None else over,
        "sample_size": sample,
    }


def _group(key: str, metric: str, side: str, period: str, lines: list[dict]) -> dict:
    return {
        "key": key, "metric": metric, "team_side": side, "period": period,
        "expected_count": 5.0, "lines": lines,
    }


def test_empty_ladder_yields_no_picks() -> None:
    assert select_ladder_picks([]) == []


def test_group_without_lines_produces_no_pick() -> None:
    picks = select_ladder_picks([_group("home_corners", "corners", "home", "full_match", [])])
    assert picks == []


def test_picks_the_least_extreme_line_within_the_band() -> None:
    """Entre varias líneas dentro de la banda, gana la más cercana al piso."""

    group = _group("home_corners", "corners", "home", "full_match", [
        _line(3.5, 0.93),   # fuera de banda (obvia)
        _line(4.5, 0.82),   # dentro de banda, la más extrema
        _line(5.5, 0.64),   # dentro de banda, la menos extrema -debe ganar-
        _line(6.5, 0.41),   # confianza real = 0.59 (under), fuera de banda
    ])
    picks = select_ladder_picks([group])
    assert len(picks) == 1
    pick = picks[0]
    assert pick["line"] == 5.5
    assert pick["direction"] == "over"
    assert pick["model_probability"] == 0.64
    assert pick["selection"] == "target_band"


def test_band_bounds_are_inclusive() -> None:
    group = _group("home_shots", "shots", "home", "full_match", [
        _line(9.5, CONFIDENCE_FLOOR), _line(10.5, CONFIDENCE_CEILING)])
    picks = select_ladder_picks([group])
    assert picks[0]["line"] == 9.5
    assert picks[0]["selection"] == "target_band"


def test_fallback_when_every_line_is_below_the_floor() -> None:
    """Ningún pick nunca queda vacío: se toma la más cercana al piso."""

    group = _group("away_corners", "corners", "away", "full_match", [
        _line(4.5, 0.55), _line(5.5, 0.52), _line(6.5, 0.51)])
    picks = select_ladder_picks([group])
    assert len(picks) == 1
    assert picks[0]["line"] == 4.5
    assert picks[0]["model_probability"] == 0.55
    assert picks[0]["selection"] == "fallback_outside_band"


def test_fallback_when_every_line_is_above_the_ceiling() -> None:
    """Un mercado sin ninguna línea informativa igual expone la mejor disponible."""

    group = _group("home_corners", "corners", "home", "full_match", [
        _line(0.5, 0.99), _line(1.5, 0.97), _line(2.5, 0.90)])
    picks = select_ladder_picks([group])
    assert len(picks) == 1
    assert picks[0]["line"] == 2.5
    assert picks[0]["model_probability"] == 0.90
    assert picks[0]["selection"] == "fallback_outside_band"


def test_under_direction_uses_complementary_confidence() -> None:
    group = _group("away_shots", "shots", "away", "full_match", [
        _line(12.5, 0.30, observed=0.32)])
    picks = select_ladder_picks([group])
    assert picks[0]["direction"] == "under"
    assert picks[0]["model_probability"] == 0.70
    assert picks[0]["observed_rate"] == 0.32


def test_single_line_group_is_returned_even_outside_band() -> None:
    group = _group("home_corners", "corners", "home", "first_half", [_line(1.5, 0.95)])
    picks = select_ladder_picks([group])
    assert len(picks) == 1
    assert picks[0]["selection"] == "fallback_outside_band"


def test_reliability_and_sample_travel_unaltered() -> None:
    group = _group("total_shots_on_target", "shots_on_target", "total", "full_match", [
        _line(7.5, 0.68, reliability="base_rate_driven", observed=0.71, sample=1204)])
    picks = select_ladder_picks([group])
    pick = picks[0]
    assert pick["edge_source"] == "base_rate_driven"
    assert pick["sample_size"] == 1204
    assert pick["observed_rate"] == 0.71
    assert pick["market"] == "total_shots_on_target"
    assert pick["metric"] == "shots_on_target"
    assert pick["team_side"] == "total"
    assert pick["period"] == "full_match"


def test_bucket_reflects_target_band_selection() -> None:
    group = _group("home_corners", "corners", "home", "full_match", [_line(4.5, 0.70)])
    pick = select_ladder_picks([group])[0]
    assert pick["bucket"] == [CONFIDENCE_FLOOR, CONFIDENCE_CEILING]


def test_bucket_reflects_fallback_below_floor() -> None:
    group = _group("home_corners", "corners", "home", "full_match", [_line(4.5, 0.55)])
    pick = select_ladder_picks([group])[0]
    assert pick["bucket"] == [0.0, CONFIDENCE_FLOOR]


def test_bucket_reflects_fallback_above_ceiling() -> None:
    group = _group("home_corners", "corners", "home", "full_match", [_line(0.5, 0.99)])
    pick = select_ladder_picks([group])[0]
    assert pick["bucket"] == [CONFIDENCE_CEILING, 1.0]


def test_observed_ci95_is_a_real_wilson_interval() -> None:
    group = _group("home_corners", "corners", "home", "full_match", [
        _line(4.5, 0.70, observed=0.72, sample=1000)])
    pick = select_ladder_picks([group])[0]
    low, high = pick["observed_ci95"]
    assert 0.0 <= low < 0.72 < high <= 1.0
    assert high - low < 0.1  # muestra grande -> intervalo angosto


def test_one_pick_per_group_across_multiple_groups() -> None:
    groups = [
        _group("home_corners", "corners", "home", "full_match", [_line(4.5, 0.70)]),
        _group("away_corners", "corners", "away", "full_match", [_line(4.5, 0.65)]),
        _group("home_corners", "corners", "home", "first_half", [_line(2.5, 0.60)]),
    ]
    picks = select_ladder_picks(groups)
    assert len(picks) == 3
    assert {pick["period"] for pick in picks} == {"full_match", "first_half"}
