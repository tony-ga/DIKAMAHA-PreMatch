"""Pruebas de la reparación de sesgo por cobertura en mercados de equipo.

Incidente real que motivó `scripts/repair_team_count_coverage_bias.py`: la
dispersión global de córners (Fase 84A, `0.966`) se estimó mezclando ligas
reales con ligas cuyo proveedor no publica córners -almacenadas como cero-,
inflando la varianza y produciendo certezas falsas (`esp.2`: "menos de 4.5:
99.99%" sobre un evento que ronda el 50%).

Una de estas pruebas ancla un bug real encontrado durante el desarrollo: un
error de precedencia de operadores en Python (`A if C else X | Y` no agrupa
como parece) hacía que ningún mercado "total" -que exige córners tanto de
local como visitante- encontrara nunca sus dos orientaciones, dejando esas
líneas con muestra cero de forma silenciosa.
"""

from __future__ import annotations

import pytest

from scripts.repair_team_count_coverage_bias import (
    _clamp_to_approved,
    _contaminated,
    _market_rows_clean,
    _matrix_clean,
)
from src.metric_coverage import MetricCoverage, build_coverage_map
from src.team_count_market_runtime import APPROVED_MARKETS


def _coverage(tmp_path, rows) -> MetricCoverage:
    import json
    artifact = tmp_path / "coverage_map.json"
    artifact.write_text(
        json.dumps(build_coverage_map(rows)), encoding="utf-8")
    return MetricCoverage(artifact)


def _row(league: str, shots: float = 11.0, corners: float = 5.0) -> dict:
    return {
        "league_slug": league, "features": [1.0, 2.0],
        "targets": {
            "corners": corners, "corners_first_half": corners / 2,
            "shots": shots, "shots_on_target": shots / 2,
            "yellow_cards": 2.0, "red_cards": 0.0,
        },
    }


def test_contaminated_flags_absent_league_for_corners(tmp_path) -> None:
    healthy = [{"league_slug": "esp.1", "actual": _row("esp.1")["targets"]}
               for _ in range(50)]
    broken = [{"league_slug": "esp.2", "actual": _row("esp.2", corners=0.0)["targets"]}
              for _ in range(50)]
    coverage = _coverage(tmp_path, healthy + broken)

    assert _contaminated(_row("esp.2", corners=0.0), "corners", coverage) is True
    assert _contaminated(_row("esp.1"), "corners", coverage) is False


def test_contaminated_never_flags_cards_regardless_of_zero_rate(tmp_path) -> None:
    """Las tarjetas no dependen del bloque de tiros ni de cobertura por liga."""

    coverage = _coverage(tmp_path, [
        {"league_slug": "esp.1", "actual": _row("esp.1")["targets"]}])

    zero_cards_row = _row("esp.1")
    zero_cards_row["targets"]["red_cards"] = 0.0
    assert _contaminated(zero_cards_row, "red_cards", coverage) is False
    assert _contaminated(zero_cards_row, "yellow_cards", coverage) is False


def test_contaminated_flags_the_specific_row_when_stats_block_is_missing(
    tmp_path,
) -> None:
    """`shots == 0` en una fila puntual invalida córners de esa misma fila,
    aunque la liga en general tenga cobertura sana."""

    coverage = _coverage(tmp_path, [
        {"league_slug": "esp.1", "actual": _row("esp.1")["targets"]}
        for _ in range(50)])

    missing_block = _row("esp.1", shots=0.0, corners=0.0)
    assert _contaminated(missing_block, "corners", coverage) is True
    assert _contaminated(missing_block, "shots", coverage) is True
    assert _contaminated(missing_block, "yellow_cards", coverage) is False


def test_matrix_clean_drops_only_contaminated_rows(tmp_path) -> None:
    coverage = _coverage(tmp_path, [
        {"league_slug": "esp.1", "actual": _row("esp.1")["targets"]}
        for _ in range(50)])
    rows = [_row("esp.1"), _row("esp.1", shots=0.0, corners=0.0), _row("esp.1")]

    features, targets, dropped = _matrix_clean(rows, "corners", coverage)

    assert dropped == 1
    assert len(targets) == 2
    assert all(value == 5.0 for value in targets)


def test_market_rows_requires_both_sides_for_total_markets() -> None:
    """Regresión: un bug de precedencia de operadores dejaba `sides_available`
    sin combinar nunca ambos lados, vaciando todos los mercados 'total'.

    Con el bug, `{"home"} if metric in home["expected"] else set() | (...)`
    evaluaba como `{"home"} if cond else (set() | (...))` -el operador
    ternario captura todo el lado derecho del `|`-, así que un partido con
    córners en ambos equipos nunca producía `{"home", "away"}`.
    """

    home = {
        "match_id": 1, "match_date": "d", "league_slug": "esp.1",
        "team_id": 10, "is_home": True,
        "expected": {"corners": 5.0}, "baseline": {"corners": 4.0},
        "actual": {"corners": 6.0},
    }
    away = {
        "match_id": 1, "match_date": "d", "league_slug": "esp.1",
        "team_id": 20, "is_home": False,
        "expected": {"corners": 4.0}, "baseline": {"corners": 3.0},
        "actual": {"corners": 3.0},
    }

    rows = _market_rows_clean([home, away], {"corners": 0.5})

    assert len(rows) == 1
    assert "corners_total_over_9_5" in rows[0]["markets"]
    assert "home_corners_over_4_5" in rows[0]["markets"]
    assert "away_corners_over_4_5" in rows[0]["markets"]


def test_market_rows_skips_total_market_when_one_side_is_missing() -> None:
    """Si sólo un lado tiene la métrica, el mercado 'total' no debe construirse."""

    home = {
        "match_id": 1, "match_date": "d", "league_slug": "esp.2",
        "team_id": 10, "is_home": True,
        "expected": {}, "baseline": {}, "actual": {},
    }
    away = {
        "match_id": 1, "match_date": "d", "league_slug": "esp.2",
        "team_id": 20, "is_home": False,
        "expected": {"corners": 4.0}, "baseline": {"corners": 3.0},
        "actual": {"corners": 3.0},
    }

    rows = _market_rows_clean([home, away], {"corners": 0.5})

    if rows:
        assert "corners_total_over_9_5" not in rows[0]["markets"]
        assert "away_corners_over_4_5" in rows[0]["markets"]
        assert "home_corners_over_4_5" not in rows[0]["markets"]


def test_clamp_never_expands_beyond_the_already_approved_set() -> None:
    """No promueve mercados nuevos sólo porque el gate de punto los aprueba.

    Promover exige el criterio de esta auditoría -calibración + IC bootstrap
    sobre tasa base-, no un gate de punto sin intervalo. El runtime valida en
    duro contra `APPROVED_MARKETS`; publicar sin recortar rompería el bloque
    completo de mercados de equipo.
    """

    wide_gate = {
        "enabled_shadow_markets": sorted(
            APPROVED_MARKETS | {"corners_total_over_9_5"}),
        "any_market_ready": True, "all_gates_pass": False,
        "count_gates": {}, "market_gates": {},
    }

    clamped = _clamp_to_approved(wide_gate)

    assert set(clamped["enabled_shadow_markets"]) == APPROVED_MARKETS
    assert clamped["gate_passed_pending_bootstrap_audit"] == [
        "corners_total_over_9_5"]


def test_clamp_detects_a_regression_in_an_already_approved_market() -> None:
    """Si un mercado ya aprobado deja de pasar el gate, debe notarse: el
    conjunto publicado deja de coincidir con `APPROVED_MARKETS` y el runtime
    lo rechazaría -mejor detectarlo aquí que en producción."""

    narrower_gate = {
        "enabled_shadow_markets": sorted(APPROVED_MARKETS - {
            "shots_on_target_total_over_7_5"}),
        "any_market_ready": True, "all_gates_pass": False,
        "count_gates": {}, "market_gates": {},
    }

    clamped = _clamp_to_approved(narrower_gate)

    assert set(clamped["enabled_shadow_markets"]) != APPROVED_MARKETS


# Version: 1.0.0
# Created: 2026-08-12


def test_conditional_dispersion_is_lower_than_marginal_when_model_informs() -> None:
    """El nucleo de la correccion de calibracion.

    La dispersion marginal mezcla dos fuentes: la variacion alrededor de la
    media de cada partido y la variacion de esa media entre partidos. Para un
    modelo que ya predice una media por partido, contar la segunda infla phi,
    ensancha la NB y empuja las probabilidades hacia 0.5. Medido en el corpus:
    tiros daba 0.34 marginal frente a 0.12 condicional.
    """

    import numpy as np
    from scripts.repair_team_count_coverage_bias import _dispersion

    rng = np.random.default_rng(11)
    means = rng.uniform(4.0, 16.0, size=4000)
    targets = rng.poisson(means).astype(float)

    marginal = _dispersion(targets)
    conditional = _dispersion(targets, means)

    assert marginal > conditional
    # Los datos son Poisson puros condicionados a la media, asi que la
    # dispersion condicional debe quedar practicamente en el suelo.
    assert conditional < 0.02


def test_conditional_dispersion_floors_underdispersed_metrics() -> None:
    """Las tarjetas son infradispersas; la NB no puede representarlo.

    Un phi negativo romperia la distribucion, asi que el suelo la deja en
    Poisson en vez de propagar un parametro invalido.
    """

    import numpy as np
    from scripts.repair_team_count_coverage_bias import _dispersion

    means = np.full(500, 2.0)
    targets = np.full(500, 2.0)

    assert _dispersion(targets, means) > 0.0


def test_calibrated_specs_replace_a_misspecified_prior() -> None:
    """Corners usaba un prior de 4.5 con media real ~8.

    Ese prior no solo sesgaba el baseline hacia abajo: contaminaba los
    features de historial de cada equipo, que se suavizan contra el.
    """

    from scripts.repair_team_count_coverage_bias import _calibrated_specs

    class _Coverage:
        def absent_metrics(self, league):
            return frozenset()

    matches = [
        {
            "split": "fit", "league_slug": "esp.1",
            "home": {name: 8.0 for name in (
                "corners", "corners_first_half", "yellow_cards",
                "yellow_cards_first_half", "red_cards", "shots",
                "shots_on_target")},
            "away": {name: 8.0 for name in (
                "corners", "corners_first_half", "yellow_cards",
                "yellow_cards_first_half", "red_cards", "shots",
                "shots_on_target")},
        }
        for _ in range(40)
    ]

    specs = _calibrated_specs(matches, _Coverage())

    corners = next(spec for spec in specs if spec.name == "corners")
    assert corners.safe_default == pytest.approx(8.0)


def test_calibrated_specs_ignore_selection_and_confirmation() -> None:
    """El prior se estima solo con `fit`, el bloque mas antiguo."""

    from scripts.repair_team_count_coverage_bias import _calibrated_specs

    class _Coverage:
        def absent_metrics(self, league):
            return frozenset()

    names = ("corners", "corners_first_half", "yellow_cards",
             "yellow_cards_first_half", "red_cards", "shots",
             "shots_on_target")
    matches = [
        {"split": "fit", "league_slug": "esp.1",
         "home": {name: 5.0 for name in names},
         "away": {name: 5.0 for name in names}}
        for _ in range(30)
    ] + [
        {"split": "confirmation", "league_slug": "esp.1",
         "home": {name: 50.0 for name in names},
         "away": {name: 50.0 for name in names}}
        for _ in range(30)
    ]

    specs = _calibrated_specs(matches, _Coverage())

    corners = next(spec for spec in specs if spec.name == "corners")
    assert corners.safe_default == pytest.approx(5.0)
