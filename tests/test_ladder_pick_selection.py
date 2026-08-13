"""Pruebas de la selección de línea por grupo de la escalera auditada.

Cubre la banda de confianza aplicada a las dos cifras (modelo e histórico),
la supresión del mercado cuando ninguna línea califica, y la corrección de
dirección del histórico -el defecto que llevó a producción "menos de 0.5
córners: 96%", ver DEC-182-.
"""

from __future__ import annotations

from src.ladder_pick_selection import (
    CONFIDENCE_CEILING, CONFIDENCE_FLOOR, select_ladder_picks,
)


def _line(line: float, over: float, reliability: str = "model_edge",
          observed: float | None = None, sample: int = 500) -> dict:
    """Construye una línea cruda; `observed` es SIEMPRE la tasa del over."""

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
        _line(3.5, 0.93),   # obviedad, fuera de banda
        _line(4.5, 0.82),   # dentro de banda, la más extrema
        _line(5.5, 0.64),   # dentro de banda, la menos extrema -debe ganar-
        _line(6.5, 0.41),   # confianza real 0.59 (under), por debajo del piso
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


# --------------------------------------------------------------------------
# Regla principal: antes vacío que obvio (DEC-182), con cobertura por
# mercado recuperada bajo cota dura (DEC-187)
# --------------------------------------------------------------------------

def test_market_is_dropped_when_every_line_is_below_the_hard_floor() -> None:
    """Un mercado que sólo ofrece volados no se publica.

    Ajustado a la cota dura: lo que ancla es la regla, no los números. Con
    `HARD_FLOOR = 0.55`, una línea de 52-54% sigue siendo un volado y el
    grupo sigue sin publicarse ni en el nivel 2.
    """

    group = _group("away_corners", "corners", "away", "full_match", [
        _line(4.5, 0.54), _line(5.5, 0.52), _line(6.5, 0.51)])
    assert select_ladder_picks([group]) == []


def test_market_is_dropped_when_every_line_is_an_obviousness() -> None:
    """Líneas 0.5/1.5 con probabilidad cercana a la certeza no se publican.

    Es exactamente el caso que el usuario reportó: preferimos que falte el
    mercado a mostrar una línea que acierta casi siempre sin informar nada.
    Ni siquiera el nivel 2 las rescata: la cota dura las excluye a las tres.
    """

    group = _group("home_corners", "corners", "home", "full_match", [
        _line(0.5, 0.99), _line(1.5, 0.97), _line(2.5, 0.95)])
    assert select_ladder_picks([group]) == []


def test_the_reported_impossible_line_stays_rejected_by_the_hard_ceiling() -> None:
    """Regresión directa del caso Tobol-Partizan (DEC-182).

    "Primer tiempo · córners de ambos equipos · menos de 0.5" tenía tasa
    histórica real 0.9617 en la dirección publicada. El nivel 2 sólo existe
    para recuperar el margen 0.85-0.90; 0.9617 sigue fuera.
    """

    group = _group("total_corners_first_half", "corners", "total", "first_half", [
        _line(0.5, 0.9617, observed=0.9617)])

    assert select_ladder_picks([group]) == []


def test_level_two_publishes_the_closest_line_and_labels_it() -> None:
    """Recupera el mercado que la banda estricta dejaba vacío.

    Medido contra el artefacto vigente: `yellow_cards` de primera mitad sólo
    conseguía pick en el 32.4% de los partidos con la banda estricta y llega
    al 96.5% con el nivel 2, sin publicar nada fuera de la cota.
    """

    group = _group("home_yellow_cards_first_half", "yellow_cards", "home",
                   "first_half", [
                       _line(0.5, 0.88), _line(1.5, 0.58), _line(2.5, 0.45)])

    picks = select_ladder_picks([group])

    assert len(picks) == 1
    # 0.88 se aleja 0.03 del techo, 0.58 se aleja 0.02 del piso y la de 2.5
    # publica su `under` a 0.55, que se aleja 0.05. Gana la de 0.58.
    assert picks[0]["line"] == 1.5
    assert picks[0]["selection"] == "outside_band"
    assert picks[0]["bucket"] == [0.55, 0.90]


def test_level_one_always_wins_over_level_two() -> None:
    """Con una línea en banda, el nivel 2 no se consulta siquiera."""

    group = _group("home_shots", "shots", "home", "full_match", [
        _line(9.5, 0.88), _line(10.5, 0.72), _line(11.5, 0.55)])

    picks = select_ladder_picks([group])

    assert picks[0]["line"] == 10.5
    assert picks[0]["selection"] == "target_band"
    assert picks[0]["bucket"] == [0.60, 0.85]


def test_level_two_checks_both_figures_not_just_the_model() -> None:
    """Un modelo dentro de la cota con histórico obvio sigue sin publicarse.

    Es la misma doble comprobación del nivel 1: la cifra que el usuario ve es
    la histórica, así que un 0.96 observado descalifica la línea aunque el
    modelo declare 0.58.
    """

    group = _group("home_corners", "corners", "home", "full_match", [
        _line(4.5, 0.58, observed=0.96)])

    assert select_ladder_picks([group]) == []


def test_single_obvious_line_group_is_dropped_not_published() -> None:
    group = _group("home_corners", "corners", "home", "first_half", [_line(1.5, 0.95)])
    assert select_ladder_picks([group]) == []


def test_a_line_whose_published_rate_is_obvious_is_rejected() -> None:
    """La banda se aplica también al histórico, no sólo a la confianza.

    El modelo declara 0.62 -dentro de banda- pero la tasa histórica de esa
    línea es 0.96: 96% es lo que vería el usuario, así que es una obviedad
    aunque el modelo no lo parezca.
    """

    group = _group("total_corners", "corners", "total", "first_half", [
        _line(0.5, 0.62, observed=0.96)])
    assert select_ladder_picks([group]) == []


# --------------------------------------------------------------------------
# Dirección del histórico publicado (el defecto de producción)
# --------------------------------------------------------------------------

def test_under_pick_publishes_the_complement_of_the_historical_rate() -> None:
    """Para un pick `under`, la tasa publicada es `1 - tasa_over`.

    Reproduce el caso real de Tobol-Partizan: el artefacto declara
    `observed_rate_historical = 0.9617` para "más de 0.5 córners 1T", y el
    modelo elegía `under`. Publicar 0.9617 afirmaba que "menos de 0.5"
    acierta el 96% de las veces, cuando la cifra real es 3.83%.
    """

    group = _group("total_corners", "corners", "total", "first_half", [
        _line(0.5, 0.35, observed=0.9617, sample=1306)])
    picks = select_ladder_picks([group])
    # 1 - 0.9617 = 0.0383, muy por debajo del piso: además de corregida, la
    # cifra ya no califica para publicarse.
    assert picks == []


def test_under_direction_uses_complementary_rate_and_interval() -> None:
    """Un `under` publicable invierte tasa e intervalo de forma coherente."""

    group = _group("away_shots", "shots", "away", "full_match", [
        _line(12.5, 0.30, observed=0.32, sample=1000)])
    picks = select_ladder_picks([group])
    assert len(picks) == 1
    pick = picks[0]
    assert pick["direction"] == "under"
    assert pick["model_probability"] == 0.70
    assert abs(pick["observed_rate"] - 0.68) < 1e-9
    low, high = pick["observed_ci95"]
    assert low < 0.68 < high
    assert high - low < 0.1


def test_over_direction_keeps_the_historical_rate_untouched() -> None:
    group = _group("home_shots", "shots", "home", "full_match", [
        _line(10.5, 0.66, observed=0.64, sample=1000)])
    pick = select_ladder_picks([group])[0]
    assert pick["direction"] == "over"
    assert abs(pick["observed_rate"] - 0.64) < 1e-9


def test_reliability_and_sample_travel_unaltered() -> None:
    """El veredicto de fiabilidad es invariante a la dirección y no se altera."""

    group = _group("total_shots_on_target", "shots_on_target", "total", "full_match", [
        _line(7.5, 0.68, reliability="base_rate_driven", observed=0.71, sample=1204)])
    pick = select_ladder_picks([group])[0]
    assert pick["edge_source"] == "base_rate_driven"
    assert pick["sample_size"] == 1204
    assert pick["observed_rate"] == 0.71
    assert pick["market"] == "total_shots_on_target"
    assert pick["metric"] == "shots_on_target"
    assert pick["team_side"] == "total"
    assert pick["period"] == "full_match"


def test_bucket_declares_the_target_band() -> None:
    group = _group("home_corners", "corners", "home", "full_match", [_line(4.5, 0.70)])
    pick = select_ladder_picks([group])[0]
    assert pick["bucket"] == [CONFIDENCE_FLOOR, CONFIDENCE_CEILING]


def test_one_pick_per_group_across_multiple_groups() -> None:
    groups = [
        _group("home_corners", "corners", "home", "full_match", [_line(4.5, 0.70)]),
        _group("away_corners", "corners", "away", "full_match", [_line(4.5, 0.65)]),
        _group("home_corners", "corners", "home", "first_half", [_line(2.5, 0.60)]),
    ]
    picks = select_ladder_picks(groups)
    assert len(picks) == 3
    assert {pick["period"] for pick in picks} == {"full_match", "first_half"}
