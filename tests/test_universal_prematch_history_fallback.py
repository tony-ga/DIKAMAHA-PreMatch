"""DEC-175: fallback de historial entre competiciones cuando la misma
competición no reúne el mínimo causal.

Reporte real: Paris Saint-Germain vs Aston Villa (Supercopa de Europa) fue
rechazado con `league_history_below_minimum` porque `uefa.super_cup` sólo
tenía un partido en el snapshot -aunque ambos equipos sí tenían decenas de
partidos en otras competiciones. DEC-056 exigía fallar cerrado sin mezclar
competiciones; DEC-175 lo sustituye: si la competición exacta no alcanza el
mínimo, se usa todo el historial disponible de ambos equipos en cualquier
competición antes de rechazar. `_lambdas` sigue exigiendo el mismo mínimo
sobre el resultado, así que un equipo genuinamente sin historia sigue
rechazando igual que antes.
"""

from __future__ import annotations

import json

import pytest

from src.universal_prematch import (
    UniversalPrematchEngine,
    UpcomingMatchInput,
    PrematchUnavailableError,
    _historical_pool,
    _team_matches,
)


def _row(match_id: int, date: str, league: str, home: int, away: int, home_goals: int = 1, away_goals: int = 1) -> list[dict[str, object]]:
    return [
        {"match_id": match_id, "match_date": date, "league_slug": league,
         "is_home": True, "team_id": home, "goals": home_goals},
        {"match_id": match_id, "match_date": date, "league_slug": league,
         "is_home": False, "team_id": away, "goals": away_goals},
    ]


def _psg_villa_style_rows() -> list[dict[str, object]]:
    """Un partido de Supercopa entre otros equipos + historial real disperso."""

    rows: list[dict[str, object]] = []
    # La única Supercopa previa en el snapshot: otros dos equipos, el año pasado.
    rows += _row(1, "2025-08-13T19:00:00+00:00", "uefa.super_cup", 501, 502)
    # PSG (160): cinco partidos de Ligue 1 contra rivales distintos.
    for index in range(5):
        rows += _row(100 + index, f"2025-{index + 1:02d}-05T20:00:00+00:00", "fra.1", 160, 600 + index)
    # Aston Villa (362): cinco partidos de Premier League contra rivales distintos.
    for index in range(5):
        rows += _row(200 + index, f"2025-{index + 1:02d}-10T15:00:00+00:00", "eng.1", 700 + index, 362)
    return rows


class TestTeamMatchesSelection:
    def test_includes_a_match_if_either_team_is_involved(self) -> None:
        matches = [
            {"match_id": 1, "match_date": "2025-01-01T00:00:00+00:00", "league_slug": "fra.1", "home_team_id": 160, "away_team_id": 999},
            {"match_id": 2, "match_date": "2025-01-02T00:00:00+00:00", "league_slug": "eng.1", "home_team_id": 999, "away_team_id": 362},
            {"match_id": 3, "match_date": "2025-01-03T00:00:00+00:00", "league_slug": "esp.1", "home_team_id": 111, "away_team_id": 222},
        ]
        request = UpcomingMatchInput("uefa.super_cup", 160, 362, "2026-01-01T00:00:00+00:00", 9999)

        selected = _team_matches(matches, request)

        assert {row["match_id"] for row in selected} == {1, 2}

    def test_excludes_the_target_match_and_anything_on_or_after_cutoff(self) -> None:
        matches = [
            {"match_id": 9999, "match_date": "2025-01-01T00:00:00+00:00", "league_slug": "fra.1", "home_team_id": 160, "away_team_id": 999},
            {"match_id": 2, "match_date": "2026-06-01T00:00:00+00:00", "league_slug": "fra.1", "home_team_id": 160, "away_team_id": 999},
        ]
        request = UpcomingMatchInput("uefa.super_cup", 160, 362, "2026-01-01T00:00:00+00:00", 9999)

        selected = _team_matches(matches, request)

        assert selected == []


class TestHistoricalPoolFallback:
    def test_uses_same_competition_when_it_already_meets_the_minimum(self) -> None:
        matches = [
            {"match_id": index, "match_date": f"2025-{index:02d}-01T00:00:00+00:00", "league_slug": "esp.1", "home_team_id": 1, "away_team_id": 2}
            for index in range(1, 9)
        ]
        request = UpcomingMatchInput("esp.1", 1, 2, "2026-01-01T00:00:00+00:00", 999)

        pool, label = _historical_pool(matches, request)

        assert label == "same_competition"
        assert len(pool) == 8

    def test_falls_back_to_cross_competition_team_history_when_thin(self) -> None:
        rows = _psg_villa_style_rows()
        grouped: dict[int, dict[str, object]] = {}
        for row in rows:
            item = grouped.setdefault(row["match_id"], {
                "match_id": row["match_id"], "match_date": row["match_date"],
                "league_slug": row["league_slug"],
            })
            side = "home_team_id" if row["is_home"] else "away_team_id"
            item[side] = row["team_id"]
        matches = list(grouped.values())
        request = UpcomingMatchInput(
            "uefa.super_cup", 160, 362, "2026-08-12T19:00:00+00:00", 401873624,
        )

        pool, label = _historical_pool(matches, request)

        assert label == "cross_competition_team_fallback"
        assert len(pool) == 10
        assert all(row["match_id"] != 1 for row in pool), "el super_cup de otros equipos no debe colarse"

    def test_does_not_report_a_usable_pool_for_a_team_with_no_history_anywhere(self) -> None:
        """Un equipo nuevo no debe parecer cubierto sólo porque su competición
        tiene partidos de otros equipos."""

        matches = [
            {"match_id": index, "match_date": f"2025-{index:02d}-01T00:00:00+00:00", "league_slug": "uru.1", "home_team_id": 900, "away_team_id": 901}
            for index in range(1, 6)
        ]
        request = UpcomingMatchInput("uru.1", 800, 801, "2026-01-01T00:00:00+00:00", 999)

        pool, label = _historical_pool(matches, request)

        assert label == "same_competition"
        assert len(pool) == 5


class TestEngineIntegration:
    def test_reconstructs_a_live_prior_for_a_sparse_annual_competition(self, tmp_path) -> None:
        windows = tmp_path / "event_windows.json"
        windows.write_text(json.dumps(_psg_villa_style_rows()), encoding="utf-8")
        engine = UniversalPrematchEngine(
            windows, team_markets_enabled=False, official_goal_chain_enabled=False,
        )
        request = UpcomingMatchInput(
            "uefa.super_cup", 160, 362, "2026-08-12T19:00:00+00:00", 401873624,
        )

        prior = engine.reconstruct_live_prior(request)

        assert prior["status"] == "reconstructed_causal_prematch_prior"
        assert prior["audit"]["history_pool"] == "cross_competition_team_fallback"
        assert prior["audit"]["target_match_data_used"] is False

    def test_still_rejects_when_even_the_team_pool_is_insufficient(self, tmp_path) -> None:
        windows = tmp_path / "event_windows.json"
        rows = [row for index in range(1, 4) for row in _row(
            index, f"2025-{index:02d}-01T00:00:00+00:00", "uru.1", 900, 901)]
        windows.write_text(json.dumps(rows), encoding="utf-8")
        engine = UniversalPrematchEngine(
            windows, team_markets_enabled=False, official_goal_chain_enabled=False,
        )
        request = UpcomingMatchInput(
            "uru.1", 800, 801, "2026-01-01T00:00:00+00:00", 999,
        )

        with pytest.raises(PrematchUnavailableError, match="league_history_below_minimum"):
            engine.reconstruct_live_prior(request)
