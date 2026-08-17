"""Pruebas del arnés de evaluación de candidatos.

El corpus a nivel partido y la recomposición del blend son la base de toda la
evidencia de `arquitectura_matematica_v1`. Un defecto aquí no produce un error
visible: produce números plausibles y equivocados, que es peor.
"""

from __future__ import annotations

import json
import math

import pytest

from scripts.build_match_level_corpus import build
from scripts.evaluate_candidates import _blend


def _window(
    match_id: int, team_id: int, is_home: bool, goals: int, index: int,
    split: str = "confirmation",
) -> dict[str, object]:
    """Construye una micro-ventana mínima del formato de Fase 74."""

    return {
        "match_id": match_id,
        "team_id": team_id,
        "is_home": is_home,
        "goals": goals,
        "window_index": index,
        "match_date": "2026-01-01 12:00:00+00:00",
        "league_slug": "esp.1",
        "season": "2025-26",
        "split": split,
    }


def _write(path, rows) -> None:
    """Escribe ventanas en el formato JSONL del corpus."""

    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_goals_are_summed_across_windows_per_side(tmp_path) -> None:
    """El marcador del partido es la suma de sus ventanas, por lado."""

    source = tmp_path / "windows.jsonl"
    _write(source, [
        _window(1, 10, True, 1, 0),
        _window(1, 10, True, 2, 1),
        _window(1, 20, False, 0, 0),
        _window(1, 20, False, 1, 1),
    ])

    summary = build(source, tmp_path / "matches.csv")
    row = (tmp_path / "matches.csv").read_text(encoding="utf-8").splitlines()[1]

    assert summary["matches"] == 1
    assert row.split(",")[-2:] == ["3", "1"]


def test_a_match_missing_one_side_is_rejected(tmp_path) -> None:
    """Sin los dos lados el marcador estaría incompleto y no se publica."""

    source = tmp_path / "windows.jsonl"
    _write(source, [_window(1, 10, True, 2, 0), _window(1, 10, True, 1, 1)])

    summary = build(source, tmp_path / "matches.csv")

    assert summary["matches"] == 0
    assert summary["rejected"]["missing_side"] == 1


def test_unbalanced_windows_are_rejected_instead_of_undercounted(tmp_path) -> None:
    """Un lado con menos ventanas produciría un marcador bajo sin avisar.

    Es el modo de fallo silencioso que importa: no lanza excepción, simplemente
    devuelve un marcador que parece razonable y no lo es.
    """

    source = tmp_path / "windows.jsonl"
    _write(source, [
        _window(1, 10, True, 1, 0),
        _window(1, 10, True, 1, 1),
        _window(1, 20, False, 0, 0),
    ])

    summary = build(source, tmp_path / "matches.csv")

    assert summary["matches"] == 0
    assert summary["rejected"]["window_mismatch"] == 1


def test_two_teams_on_the_same_side_is_an_error(tmp_path) -> None:
    """Dos equipos distintos como local significa datos corruptos."""

    source = tmp_path / "windows.jsonl"
    _write(source, [
        _window(1, 10, True, 1, 0),
        _window(1, 11, True, 1, 1),
    ])

    with pytest.raises(ValueError, match="dos equipos distintos"):
        build(source, tmp_path / "matches.csv")


def test_frozen_split_is_preserved_not_recomputed(tmp_path) -> None:
    """El `split` viene del corpus, no lo decide este script.

    R2 de `model_composition v1`: si el arnés pudiera reasignar la partición,
    cualquier candidato podría elegir dónde se mide su propio resultado.
    """

    source = tmp_path / "windows.jsonl"
    _write(source, [
        _window(1, 10, True, 1, 0, split="fit"),
        _window(1, 20, False, 0, 0, split="fit"),
        _window(2, 30, True, 2, 0, split="confirmation"),
        _window(2, 40, False, 1, 0, split="confirmation"),
    ])

    summary = build(source, tmp_path / "matches.csv")

    assert summary["splits"] == {"confirmation": 1, "fit": 1}


def test_blend_weight_one_recovers_dixon_coles_exactly() -> None:
    """Peso 1 devuelve el prior estructural puro."""

    row = {
        "lambda_dixon_coles": [1.60, 0.90],
        "lambda_kalman": [1.10, 1.30],
        "tau_dc": -0.05,
    }

    from src.kalman_v2 import poisson_matrix
    import numpy as np

    matrix = poisson_matrix(1.60, 0.90, 12, -0.05, True)
    expected_home = float(np.tril(matrix, -1).sum())

    assert _blend(row, 1.0)["1"] == pytest.approx(expected_home)


def test_blend_is_geometric_and_monotone_between_its_components() -> None:
    """El blend interpola en escala log entre los dos componentes.

    Con Dixon-Coles más favorable al local que Kalman, subir el peso del prior
    debe aumentar la probabilidad de victoria local de forma monótona. Si no lo
    hiciera, la reestimación del peso estaría optimizando sobre una superficie
    que no corresponde al modelo servido.
    """

    row = {
        "lambda_dixon_coles": [1.80, 0.80],
        "lambda_kalman": [1.00, 1.40],
        "tau_dc": -0.04,
    }

    probabilities = [_blend(row, weight)["1"] for weight in (0.0, 0.25, 0.5, 0.75, 1.0)]

    assert all(
        later > earlier
        for earlier, later in zip(probabilities, probabilities[1:]))


def test_blend_produces_normalized_probabilities() -> None:
    """Cualquier peso produce un 1X2 normalizado."""

    row = {
        "lambda_dixon_coles": [1.40, 1.10],
        "lambda_kalman": [0.95, 1.25],
        "tau_dc": -0.06,
    }

    for weight in (0.0, 0.3, 0.8, 1.0):
        markets = _blend(row, weight)
        assert math.isclose(sum(markets.values()), 1.0, abs_tol=1e-9)
        assert all(0.0 <= value <= 1.0 for value in markets.values())
