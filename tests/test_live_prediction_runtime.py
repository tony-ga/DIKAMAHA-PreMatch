"""Regresiones del runtime de producto Markov Live + Hawkes."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import threading
import time

import src.live_prediction_runtime as live_runtime
from src.dikamaha_inference import DikamahaInferenceEngine
from src.live_prediction_runtime import (
    LivePredictionRuntime,
    LiveScanProgress,
    _candidate_live_dates,
    _match_dynamics,
    _observed_live_presentation,
    predict_shadow_snapshot,
)
from src.universal_prematch import UniversalPrematchEngine, UpcomingMatchInput


def _historical_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index in range(10):
        match_id = 1000 + index
        date = f"2025-{index + 1:02d}-01T12:00:00+00:00"
        rows.extend([
            {"match_id": match_id, "match_date": date,
             "league_slug": "esp.1", "is_home": True,
             "team_id": 1 if index % 2 == 0 else 3, "goals": index % 3},
            {"match_id": match_id, "match_date": date,
             "league_slug": "esp.1", "is_home": False,
             "team_id": 2 if index % 2 == 0 else 4, "goals": (index + 1) % 2},
        ])
    return rows


def test_live_prior_reconstruction_is_strict_causal_and_reproducible(tmp_path) -> None:
    """Permite kickoff pasado sin usar datos del partido objetivo."""

    windows = tmp_path / "event_windows.json"
    windows.write_text(json.dumps(_historical_rows()), encoding="utf-8")
    engine = UniversalPrematchEngine(
        windows, team_markets_enabled=False,
        official_goal_chain_enabled=False,
    )
    request = UpcomingMatchInput(
        "esp.1", 1, 2, "2026-01-15T20:00:00+00:00", 9999,
    )

    first = engine.reconstruct_live_prior(request)
    second = engine.reconstruct_live_prior(request)

    assert first == second
    assert first["status"] == "reconstructed_causal_prematch_prior"
    assert datetime.fromisoformat(first["cutoff_ts"]) < datetime.fromisoformat(
        first["kickoff_ts"])
    assert first["audit"]["target_match_data_used"] is False
    assert first["audit"]["cutoff_strictly_before_kickoff"] is True
    assert len(first["source_hash"]) == 64


def _snapshot() -> dict[str, object]:
    return {
        "contract_version": "live_event_stream_v1",
        "provider_event_id": "900001", "home_team_id": 1,
        "away_team_id": 2, "kickoff_ts": "2026-08-08T20:00:00+00:00",
        "source_fetched_at": "2026-08-08T20:10:00+00:00",
        "league_slug": "esp.1", "competition_id": "900001",
        "period": 1, "match_clock_seconds": 600.0,
        "score_home": 0, "score_away": 0, "events": [],
        "source_hash": "live-source",
    }


def _prior() -> dict[str, object]:
    return {
        "provider_event_id": "900001", "home_team_id": 1,
        "away_team_id": 2, "league_slug": "esp.1",
        "cutoff_ts": "2026-08-01T20:00:00+00:00",
        "lambda_base_home": 1.5, "lambda_base_away": 1.1,
        "source_hash": "prior-source",
    }


def test_non_admitted_hawkes_is_exact_markov_complement() -> None:
    """Fuera de allowlist Hawkes no compite ni altera mercados Markov."""

    policy = {
        "allowed_leagues": ["eng.1"], "rho_goal": 1.0,
        "rho_next_event": 0.0,
    }
    result = predict_shadow_snapshot(
        DikamahaInferenceEngine(), _snapshot(), _prior(), policy)

    assert result["hawkes_league_admission"]["admitted"] is False
    assert result["hawkes_league_admission"][
        "fallback_exact_markov_live"] is True
    assert result["experimental_combined_live"]["markets"] == (
        result["experimental_markov_live"]["markets"])


class _BrokenProbabilityEngine:
    def predict(self, *_args: object, **_kwargs: object) -> dict[str, object]:
        """Simula un fallo matemático interno sin filtrar payloads."""

        raise FloatingPointError("synthetic_engine_failure")


def test_official_live_engine_falls_back_to_markov_on_internal_error() -> None:
    """Conserva una salida oficial usable si falla el motor compuesto."""

    result = predict_shadow_snapshot(
        DikamahaInferenceEngine(), _snapshot(), _prior(),
        {
            "allowed_leagues": ["esp.1"], "rho_goal": 1.0,
            "rho_next_event": 0.0,
        },
        probability_engine=_BrokenProbabilityEngine(),
    )

    assert result["official_source"] == "markov_live_v1_fallback"
    assert result["official_live_prediction"]["fallback"]["applied"] is True
    assert result["official_live_prediction"]["markets"] == (
        result["experimental_markov_live"]["markets"]
    )
    team_markets = result["experimental_live_team_markets"]
    assert team_markets["status"] == "unavailable_fallback_active"
    assert team_markets["bounded_market_grid_view"] == []
    assert team_markets["next_goal"] == {}


def test_official_live_engine_publishes_team_markets_on_success() -> None:
    """La rejilla restante viaja junto a la salida oficial sin alterarla."""

    result = predict_shadow_snapshot(
        DikamahaInferenceEngine(), _snapshot(), _prior(),
        {
            "allowed_leagues": ["esp.1"], "rho_goal": 1.0,
            "rho_next_event": 0.0,
        },
    )
    team_markets = result["experimental_live_team_markets"]
    next_goal = team_markets["next_goal"]

    assert team_markets["status"] == "experimental_shadow_not_promoted"
    assert team_markets["bounded_market_grid_view"]
    assert set(team_markets["remaining_intensities"]) == {
        "corners", "shots_commercial",
    }
    assert abs(
        next_goal["probability_home_next_goal"]
        + next_goal["probability_away_next_goal"]
        + next_goal["probability_no_more_goals"] - 1.0
    ) <= 1e-10


class _ScoreboardConnector:
    def scoreboard(self, date: str) -> dict[str, object]:
        assert date == "20260808"
        return {"events": [{
            "id": "900001", "date": "2026-08-08T20:00:00Z",
            "competitions": [{
                "id": "900001",
                "status": {"period": 1, "displayClock": "32'",
                           "type": {"state": "in", "detail": "32'"}},
                "competitors": [
                    {"homeAway": "home", "score": "1",
                     "team": {"id": "1", "displayName": "Equipo A"}},
                    {"homeAway": "away", "score": "0",
                     "team": {"id": "2", "displayName": "Equipo B"}},
                ],
            }],
        }]}


def test_live_catalog_uses_espn_state_score_and_orientation(tmp_path) -> None:
    """Lista sólo estado in/live y conserva local, visitante y marcador."""

    windows = tmp_path / "event_windows.json"
    windows.write_text(json.dumps(_historical_rows()), encoding="utf-8")
    prematch = UniversalPrematchEngine(
        windows, team_markets_enabled=False,
        official_goal_chain_enabled=False,
    )
    runtime = LivePredictionRuntime(
        prematch, DikamahaInferenceEngine(),
        connector_factory=lambda _: _ScoreboardConnector(),
    )

    catalog = runtime.list_active("esp.1", 12, "20260808")

    fixture = catalog["fixtures"][0]
    assert fixture["provider_status"] == "in"
    assert fixture["home_team_name"] == "Equipo A"
    assert fixture["away_team_name"] == "Equipo B"
    assert fixture["home_score"] == 1 and fixture["away_score"] == 0
    assert fixture["display_clock"] == "32'"


class _BoundaryConnector:
    def scoreboard(self, date: str) -> dict[str, object]:
        if date != "20260809":
            return {"events": []}
        return _ScoreboardConnector().scoreboard("20260808")


def test_automatic_live_window_includes_previous_espn_day(
    tmp_path, monkeypatch,
) -> None:
    """Evita catálogo vacío cerca de medianoche UTC."""

    assert _candidate_live_dates(
        None, now=datetime(2026, 8, 10, 1, tzinfo=timezone.utc),
    ) == ("20260809", "20260810", "20260811")
    assert _candidate_live_dates("20260808") == ("20260808",)
    windows = tmp_path / "event_windows.json"
    windows.write_text(json.dumps(_historical_rows()), encoding="utf-8")
    runtime = LivePredictionRuntime(
        UniversalPrematchEngine(
            windows, team_markets_enabled=False,
            official_goal_chain_enabled=False,
        ),
        DikamahaInferenceEngine(),
        connector_factory=lambda _: _BoundaryConnector(),
    )
    monkeypatch.setattr(
        live_runtime, "_candidate_live_dates",
        lambda value: ("20260809", "20260810", "20260811"),
    )

    catalog = runtime.list_active("esp.1", 12)

    assert catalog["count"] == 1
    assert catalog["date_count"] == 3
    assert catalog["dates"] == ["20260809", "20260810", "20260811"]
    assert catalog["fixtures"][0]["match_id"] == 900001


def test_observed_live_presentation_aggregates_teams_and_ignores_annulled() -> None:
    """Separa estadísticas visuales por orientación sin alterar el snapshot."""

    snapshot = {
        **_snapshot(),
        "home_team_name": "Local real", "away_team_name": "Visitante real",
        "score_home": 2, "score_away": 1,
        "events": [
            {"event_id": "1", "event_type": "corner", "event_type_raw": "corner", "team_id": 1, "period": 1, "match_clock_seconds": 60, "text": "Corner", "annulled": False},
            {"event_id": "2", "event_type": "shot_on_target", "event_type_raw": "shot_on_target", "team_id": 1, "period": 1, "match_clock_seconds": 120, "text": "Shot", "annulled": False},
            {"event_id": "3", "event_type": "auxiliary", "event_type_raw": "save", "team_id": 2, "period": 1, "match_clock_seconds": 121, "text": "Save", "annulled": False},
            {"event_id": "4", "event_type": "yellow", "event_type_raw": "yellow_card", "team_id": 2, "period": 1, "match_clock_seconds": 180, "text": "Card", "annulled": False},
            {"event_id": "5", "event_type": "corner", "event_type_raw": "corner", "team_id": 2, "period": 1, "match_clock_seconds": 200, "text": "Deleted", "annulled": True},
        ],
    }

    result = _observed_live_presentation(snapshot)
    statistics = result["observed_live_statistics"]

    assert statistics["home"]["goals"] == 2
    assert statistics["away"]["goals"] == 1
    assert statistics["home"]["corners"] == 1
    assert statistics["home"]["shots"] == 1
    assert statistics["away"]["yellow_cards"] == 1
    assert statistics["away"]["saves"] == 1
    assert len(result["recent_actions"]) == 4
    assert result["recent_actions"][0]["event_id"] == "4"
    assert result["automatic_refresh_recommended_seconds"] == 15


def test_observed_live_presentation_prefers_boxscore_over_sparse_events() -> None:
    """Reporte real: 1-1 con 0 tiros porque el Core API sólo listaba goles.

    `summary.boxscore` trae conteos oficiales por equipo incluso cuando
    `plays` sólo publica los eventos más notables (goles, tarjetas, cambios).
    """

    snapshot = {
        **_snapshot(),
        "score_home": 2, "score_away": 1,
        "events": [
            {"event_id": "1", "event_type": "goal", "event_type_raw": "goal", "team_id": 1, "period": 1, "match_clock_seconds": 60, "text": "Goal", "annulled": False},
        ],
        "boxscore_aggregate": {
            "home": {"shots": 7, "shots_on_target": 2, "shots_blocked": 1, "shots_off_target": 4, "corners": 0, "fouls": 7, "yellow_cards": 0, "red_cards": 0, "offsides": 1, "saves": 2, "penalties": 0},
            "away": {"shots": 11, "shots_on_target": 3, "shots_blocked": 2, "shots_off_target": 6, "corners": 2, "fouls": 9, "yellow_cards": 1, "red_cards": 0, "offsides": 0, "saves": 2, "penalties": 0},
        },
    }

    result = _observed_live_presentation(snapshot)
    statistics = result["observed_live_statistics"]

    assert statistics["source"] == "provider_boxscore_aggregate"
    assert statistics["home"]["goals"] == 2, "el marcador sigue viniendo del scoreboard, no del boxscore"
    assert statistics["home"]["shots"] == 7
    assert statistics["home"]["shots_on_target"] == 2
    assert statistics["away"]["shots"] == 11
    assert statistics["away"]["corners"] == 2
    assert result["recent_actions"][0]["event_id"] == "1", "la cronología sigue viniendo de events, no del boxscore"


def test_observed_live_presentation_ignores_incomplete_boxscore() -> None:
    """Un boxscore sin ambos equipos no debe reemplazar conteos parciales pero reales."""

    snapshot = {
        **_snapshot(),
        "events": [
            {"event_id": "1", "event_type": "corner", "event_type_raw": "corner", "team_id": 1, "period": 1, "match_clock_seconds": 60, "text": "Corner", "annulled": False},
        ],
        "boxscore_aggregate": {"home": {"shots": 7}},
    }

    result = _observed_live_presentation(snapshot)
    statistics = result["observed_live_statistics"]

    assert statistics["source"] == "provider_play_by_play"
    assert statistics["home"]["corners"] == 1
    assert statistics["home"]["shots"] == 0


def test_boxscore_total_shots_survive_a_missing_on_target_breakdown() -> None:
    """El total comercial del proveedor manda sobre la suma de componentes.

    `_boxscore_aggregate` sólo deriva `shots_off_target` cuando ESPN publica
    también `shotsOnTarget`. Recalcular `shots` incondicionalmente destruía
    el único dato real disponible cuando faltaba ese desglose: 9 tiros
    publicados caían a 0 en la interfaz.
    """

    snapshot = {
        **_snapshot(),
        "events": [],
        "boxscore_aggregate": {
            "home": {"shots": 9, "corners": 4, "fouls": 8},
            "away": {"shots": 12, "corners": 6, "fouls": 11},
        },
    }

    statistics = _observed_live_presentation(
        snapshot)["observed_live_statistics"]

    assert statistics["home"]["shots"] == 9
    assert statistics["away"]["shots"] == 12
    assert "shots" not in statistics["unavailable_metrics"]
    assert "shots_on_target" in statistics["unavailable_metrics"]


def test_shots_are_still_derived_when_the_provider_omits_the_total() -> None:
    """Sin `totalShots`, la suma de componentes sigue siendo la mejor lectura."""

    snapshot = {
        **_snapshot(),
        "events": [],
        "boxscore_aggregate": {
            "home": {"shots_on_target": 3, "shots_blocked": 1},
            "away": {"shots_on_target": 2, "shots_blocked": 0},
        },
    }

    statistics = _observed_live_presentation(
        snapshot)["observed_live_statistics"]

    assert statistics["home"]["shots"] == 4
    assert statistics["away"]["shots"] == 2


def test_unavailable_metrics_stay_empty_without_a_boxscore() -> None:
    """Sin la fuente autoritativa no se puede afirmar que falte un dato."""

    snapshot = {
        **_snapshot(),
        "events": [
            {"event_id": "1", "event_type": "corner", "event_type_raw": "corner", "team_id": 1, "period": 1, "match_clock_seconds": 60, "text": "Corner", "annulled": False},
        ],
    }

    statistics = _observed_live_presentation(
        snapshot)["observed_live_statistics"]

    assert statistics["source"] == "provider_play_by_play"
    assert statistics["unavailable_metrics"] == []


def test_pressure_granularity_separates_no_data_from_no_action_yet() -> None:
    """El caso DEC-176: conteos agregados reales, cero jugadas con minuto.

    Una curva plana en esa situación no significa "partido tranquilo", sino
    que el proveedor no publica cronología por jugada para esa competición y
    la curva no se va a llenar nunca. La interfaz necesita distinguirlo de un
    partido que apenas arranca.
    """

    aggregate_only = _match_dynamics(
        {**_snapshot(), "match_clock_seconds": 2700, "events": []},
        {"home": {"shots": 9, "corners": 4, "fouls": 8},
         "away": {"shots": 12, "corners": 6, "fouls": 11}},
    )
    too_early = _match_dynamics(
        {**_snapshot(), "match_clock_seconds": 120, "events": []},
        {"home": {"shots": 0, "corners": 0, "fouls": 0},
         "away": {"shots": 0, "corners": 0, "fouls": 0}},
    )
    with_plays = _match_dynamics(
        {**_snapshot(), "match_clock_seconds": 900,
         "events": [{"event_type": "shot_on_target", "team_id": 1,
                     "match_clock_seconds": 600}]},
        {"home": {"shots": 1, "corners": 0, "fouls": 0},
         "away": {"shots": 0, "corners": 0, "fouls": 0}},
    )

    assert aggregate_only["pressure_granularity"] == "aggregate_only"
    assert aggregate_only["weighted_event_count"] == 0
    assert too_early["pressure_granularity"] == "insufficient_events"
    assert with_plays["pressure_granularity"] == "play_by_play"
    assert with_plays["weighted_event_count"] == 1


def test_match_dynamics_applies_signed_weights_and_centered_smoothing() -> None:
    """Orienta local/visitante y suaviza cinco minutos sin usar el futuro."""

    snapshot = {
        **_snapshot(),
        "home_team_name": "Equipo A", "away_team_name": "Equipo B",
        "match_clock_seconds": 900,
        "events": [
            {"event_type": "shot_on_target", "team_id": 1, "match_clock_seconds": 600},
            {"event_type": "corner", "team_id": 2, "match_clock_seconds": 660},
            {"event_type": "goal", "team_id": 1, "match_clock_seconds": 720},
            {"event_type": "goal", "team_id": 2, "match_clock_seconds": 780, "annulled": True},
        ],
    }

    result = _match_dynamics(snapshot)
    points = result["points"]

    assert len(points) == 90
    assert points[10]["raw_score"] == 8
    assert points[11]["raw_score"] == -3
    assert points[12]["raw_score"] == 25
    assert points[10]["smoothed_score"] == 6
    assert result["goal_markers"] == [{
        "minute": 13, "team_side": "home", "team_name": "Equipo A",
    }]
    assert result["not_model_feature"] is True
    assert result["smoothing"]["window_minutes"] == 5


# --------------------------------------------------------------------------
# Progreso real del barrido live
#
# Un barrido en frío mide ~30s reales contra ESPN (63 ligas x 3 días, ver
# DEC-181): estas pruebas cubren que el avance publicado durante ese tiempo
# es real -cuenta objetivos de verdad completados, no un número inventado-.
# --------------------------------------------------------------------------

def test_scan_progress_defaults_to_idle_for_an_unknown_key() -> None:
    """Una clave nunca iniciada reporta `idle`, no un error ni datos viejos."""

    progress = LiveScanProgress()
    assert progress.snapshot("nunca-visto") == {
        "status": "idle", "scanned": 0, "total": 0}


def test_scan_progress_tracks_start_increment_and_finish() -> None:
    """El ciclo completo queda reflejado en el snapshot en cada paso."""

    progress = LiveScanProgress()
    key = "esp.1:12:None"

    progress.start(key, total=3)
    assert progress.snapshot(key) == {"status": "scanning", "scanned": 0, "total": 3}

    progress.increment(key)
    progress.increment(key)
    assert progress.snapshot(key) == {"status": "scanning", "scanned": 2, "total": 3}

    progress.increment(key)
    progress.finish(key)
    assert progress.snapshot(key) == {"status": "done", "scanned": 3, "total": 3}


def test_scan_progress_keys_are_independent() -> None:
    """Un barrido con otro filtro de liga no pisa el progreso de éste."""

    progress = LiveScanProgress()
    progress.start("esp.1", total=5)
    progress.increment("esp.1")

    progress.start("eng.1", total=2)
    progress.increment("eng.1")
    progress.increment("eng.1")
    progress.finish("eng.1")

    assert progress.snapshot("esp.1") == {"status": "scanning", "scanned": 1, "total": 5}
    assert progress.snapshot("eng.1") == {"status": "done", "scanned": 2, "total": 2}


def test_list_active_publishes_real_progress_when_given_a_key(tmp_path) -> None:
    """`list_active` con `progress_key` deja el avance final correcto."""

    windows = tmp_path / "event_windows.json"
    windows.write_text(json.dumps(_historical_rows()), encoding="utf-8")
    runtime = LivePredictionRuntime(
        UniversalPrematchEngine(
            windows, team_markets_enabled=False,
            official_goal_chain_enabled=False,
        ),
        DikamahaInferenceEngine(),
        connector_factory=lambda _: _ScoreboardConnector(),
    )

    runtime.list_active("esp.1", 12, "20260808", progress_key="test-key")

    assert runtime.scan_progress.snapshot("test-key") == {
        "status": "done", "scanned": 1, "total": 1}


def test_list_active_without_a_key_never_touches_scan_progress(tmp_path) -> None:
    """Sin `progress_key` el barrido funciona igual y no publica avance."""

    windows = tmp_path / "event_windows.json"
    windows.write_text(json.dumps(_historical_rows()), encoding="utf-8")
    runtime = LivePredictionRuntime(
        UniversalPrematchEngine(
            windows, team_markets_enabled=False,
            official_goal_chain_enabled=False,
        ),
        DikamahaInferenceEngine(),
        connector_factory=lambda _: _ScoreboardConnector(),
    )

    catalog = runtime.list_active("esp.1", 12, "20260808")

    assert catalog["count"] == 1
    assert runtime.scan_progress.snapshot("esp.1") == {
        "status": "idle", "scanned": 0, "total": 0}


class _SlowConnector:
    """Conector sintético con latencia real para observar progreso a mitad de barrido."""

    def scoreboard(self, date: str) -> dict[str, object]:
        time.sleep(0.2)
        return {"events": []}


def test_scan_progress_advances_while_the_scan_is_still_running(tmp_path) -> None:
    """El avance es observable desde otro hilo mientras el barrido corre.

    20 ligas x 1 día con sólo 12 trabajadores concurrentes (el mismo tope que
    usa `list_active` en producción) fuerza dos tandas: las primeras 12
    terminan ~0.2s, las 8 restantes esperan turno y terminan después. Al
    muestrear a mitad de camino el conteo debe ser mayor que cero y menor
    que el total -ni estancado, ni ya terminado-, la prueba de que el número
    refleja trabajo real en curso y no una animación inventada.
    """

    windows = tmp_path / "event_windows.json"
    windows.write_text(json.dumps(_historical_rows()), encoding="utf-8")
    runtime = LivePredictionRuntime(
        UniversalPrematchEngine(
            windows, team_markets_enabled=False,
            official_goal_chain_enabled=False,
        ),
        DikamahaInferenceEngine(),
        connector_factory=lambda _: _SlowConnector(),
    )
    leagues = ",".join(f"league{i}" for i in range(20))
    observed: list[dict[str, object]] = []

    def scan() -> None:
        runtime.list_active(leagues, 12, "20260808", progress_key="slow-key")

    thread = threading.Thread(target=scan)
    thread.start()
    time.sleep(0.3)
    observed.append(runtime.scan_progress.snapshot("slow-key"))
    thread.join(timeout=5)

    assert observed[0]["total"] == 20
    assert 0 < observed[0]["scanned"] < 20, (
        "a mitad del barrido el conteo no debe ser ni cero ni el total")
    assert runtime.scan_progress.snapshot("slow-key") == {
        "status": "done", "scanned": 20, "total": 20}
