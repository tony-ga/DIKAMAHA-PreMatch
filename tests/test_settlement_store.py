"""Pruebas de las reglas de liquidación compartidas entre el canal y Fase 123."""

from __future__ import annotations

from src.settlement_store import observed_team_count, team_market_hit


def test_team_market_hit_compares_direction_against_the_line() -> None:
    assert team_market_hit("over", 4.5, 6.0) is True
    assert team_market_hit("over", 4.5, 4.0) is False
    assert team_market_hit("under", 4.5, 4.0) is True
    assert team_market_hit("under", 4.5, 6.0) is False


def test_observed_team_count_translates_full_match_to_total() -> None:
    """DEC-190: `explorer_statistics` nunca trae la clave `"full_match"`.

    `_period_statistics` (`src/espn_user_explorer.py`) sólo expone
    `first_half`/`second_half`/`total` por lado. Los picks de partido
    completo declaran su periodo público como `"full_match"` -mismo
    vocabulario que el resto del sistema-, así que sin esta traducción la
    búsqueda nunca encontraba nada: era el defecto real detrás de que
    ningún pick de partido completo -el 70% del universo, medido en
    producción 2026-08-13- se liquidara jamás.
    """

    periods = {"home": {
        "first_half": {"corners": 3}, "second_half": {"corners": 4},
        "total": {"corners": 7},
    }}

    assert observed_team_count(periods, "home", "full_match", "corners") == 7


def test_observed_team_count_reads_half_periods_without_translation() -> None:
    """`first_half`/`second_half` son la misma clave en ambos lados."""

    periods = {"home": {
        "first_half": {"corners": 3}, "second_half": {"corners": 4},
        "total": {"corners": 7},
    }}

    assert observed_team_count(periods, "home", "first_half", "corners") == 3
    assert observed_team_count(periods, "home", "second_half", "corners") == 4


def test_observed_team_count_returns_none_for_a_missing_side() -> None:
    periods = {"home": {"total": {"corners": 7}}}

    assert observed_team_count(periods, "away", "full_match", "corners") is None


def test_observed_team_count_returns_none_for_a_missing_metric() -> None:
    periods = {"home": {"total": {"corners": 7}}}

    assert observed_team_count(periods, "home", "full_match", "shots") is None


def test_observed_team_count_rejects_a_non_numeric_value() -> None:
    periods = {"home": {"total": {"corners": None}}}

    assert observed_team_count(periods, "home", "full_match", "corners") is None
