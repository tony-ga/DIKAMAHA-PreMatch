"""Pruebas del benchmark analítico externo y su aislamiento financiero."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.espn_prospective_connector import (
    EspnConnectorConfig,
    EspnFetchResult,
    EspnProspectiveConnector,
)
from src.provider_match_context import (
    normalize_provider_market_catalog,
    normalize_provider_match_context,
)


def normalize(summary: dict[str, object], scope: str = "live") -> dict[str, object]:
    return normalize_provider_match_context(
        summary,
        event_id="401000001",
        league="esp.1",
        scope=scope,
        source_fetched_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
    )


def test_normalizes_explicit_predictor_percentages() -> None:
    result = normalize({
        "predictor": {
            "homeWinPercentage": 48,
            "tiePercentage": 27,
            "awayWinPercentage": 25,
        },
    }, "pre_match")

    assert result["status"] == "available"
    assert result["probabilities"] == {"home": 0.48, "draw": 0.27, "away": 0.25}
    assert result["role"] == "external_benchmark_display_only"
    assert result["not_model_feature"] is True
    assert result["replaces_dikamaha_models"] is False


def test_normalizes_live_history_and_uses_last_published_cut() -> None:
    result = normalize({
        "winprobability": [
            {"clock": {"displayValue": "12'"}, "homeWinPercentage": .40, "tiePercentage": .35, "awayWinPercentage": .25},
            {"clock": {"displayValue": "42'"}, "homeWinPercentage": .61, "tiePercentage": .24, "awayWinPercentage": .15},
        ],
    })

    assert result["status"] == "available"
    assert [row["minute"] for row in result["history"]] == [12, 42]
    assert result["probabilities"] == {"home": .61, "draw": .24, "away": .15}
    assert result["coverage"] == {"predictor": True, "history": True}


def test_accepts_explicit_nested_team_projection_without_deriving_tie() -> None:
    result = normalize({"predictor": {
        "homeTeam": {"gameProjection": "51"},
        "tiePercentage": "28",
        "awayTeam": {"gameProjection": "21"},
    }})

    assert result["probabilities"] == {"home": .51, "draw": .28, "away": .21}


def test_pickcenter_never_becomes_a_predictor_but_exposes_isolated_tape() -> None:
    result = normalize({
        "pickcenter": [{
            "provider": {"name": "Sportsbook"},
            "moneyline": {
                "home": {"open": {"odds": "+130"}, "live": {"odds": "-210", "link": {"href": "https://example.test"}}},
                "draw": {"close": {"odds": "+205"}},
                "away": {"open": {"odds": "+195"}, "live": {"odds": "+850"}},
            },
        }],
    })

    assert result["status"] == "not_published"
    assert result["probabilities"] is None
    market = result["market_context"]
    assert market["status"] == "financial_isolated_available"
    assert market["provider_count"] == 1
    assert market["consumed_by_models"] is False
    assert market["odds_exposed"] is True
    assert market["derived_probabilities"] is False
    assert market["providers"][0]["markets"]["moneyline"]["home"] == {
        "open": {"odds": "+130"}, "live": {"odds": "-210"},
    }
    assert "example.test" not in str(result)


def test_active_odds_catalog_keeps_open_close_live_and_team_identity() -> None:
    result = normalize_provider_market_catalog({"events": [{
        "id": "401000002", "date": "2026-08-10T20:00Z",
        "competitions": [{
            "competitors": [
                {"homeAway": "home", "team": {"id": "1", "displayName": "Casa", "logos": [{"href": "https://a.espncdn.com/home.png"}]}},
                {"homeAway": "away", "team": {"id": "2", "displayName": "Fuera", "logos": [{"href": "https://a.espncdn.com/away.png"}]}},
            ],
            "status": {"type": {"state": "pre", "detail": "8:00 PM"}},
            "odds": [{
                "provider": {"id": "100", "name": "Provider"},
                "moneyline": {
                    "home": {"open": {"odds": "-110"}, "close": {"odds": "-105"}},
                    "draw": {"open": {"odds": "+200"}, "close": {"odds": "+210"}},
                    "away": {"open": {"odds": "+300"}, "close": {"odds": "+280"}},
                },
                "total": {"over": {"open": {"line": "o2.5", "odds": "+120"}}},
            }],
        }],
    }]}, league="col.1", date="20260810", source_fetched_at="2026-08-10T00:00:00Z")

    assert result["contract_version"] == "provider_market_tape_v1"
    assert result["count"] == 1
    assert result["fixtures"][0]["home_team"]["name"] == "Casa"
    provider = result["fixtures"][0]["market_context"]["providers"][0]
    assert provider["markets"]["moneyline"]["away"]["close"]["odds"] == "+280"
    assert provider["markets"]["total"]["over"]["open"]["line"] == "o2.5"
    assert result["not_model_feature"] is True


def test_rejects_incomplete_or_invalid_probability_triplets() -> None:
    incomplete = normalize({"predictor": {
        "homeWinPercentage": 60, "awayWinPercentage": 40,
    }})
    invalid_total = normalize({"predictor": {
        "homeWinPercentage": 80, "tiePercentage": 40,
        "awayWinPercentage": 30,
    }})

    assert incomplete["status"] == "not_published"
    assert invalid_total["status"] == "not_published"


def test_fresh_predictor_summary_requests_ocp_and_preserves_raw(
    tmp_path, monkeypatch,
) -> None:
    """Evita caché live obsoleta sin perder la captura raw-first."""

    connector = EspnProspectiveConnector(EspnConnectorConfig(
        league="esp.1", cache_dir=tmp_path,
    ))
    captured: dict[str, Any] = {}

    def fetch(url: str, params: dict[str, Any], *, use_cache: bool) -> EspnFetchResult:
        captured.update({"url": url, "params": params, "use_cache": use_cache})
        return EspnFetchResult(
            {"predictor": {}}, 200,
            datetime(2026, 8, 9, tzinfo=timezone.utc), False, url,
        )

    monkeypatch.setattr(connector, "_get_result", fetch)
    connector.summary_fetch_result(
        "401000001", use_cache=False, include_predictor=True,
        preserve_raw=True,
    )

    assert captured["params"] == {"event": "401000001", "ocp": 1}
    assert captured["use_cache"] is False
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_market_scoreboard_requests_active_odds_and_preserves_raw(
    tmp_path, monkeypatch,
) -> None:
    connector = EspnProspectiveConnector(EspnConnectorConfig(
        league="col.1", cache_dir=tmp_path,
    ))
    captured: dict[str, Any] = {}

    def fetch(url: str, params: dict[str, Any], *, use_cache: bool) -> EspnFetchResult:
        captured.update({"url": url, "params": params, "use_cache": use_cache})
        return EspnFetchResult({}, 200, datetime.now(timezone.utc), False, url)

    monkeypatch.setattr(connector, "_get_result", fetch)
    connector.scoreboard_fetch_result(
        "20260810", use_cache=False, active_odds=True, preserve_raw=True,
    )

    assert captured["params"] == {"dates": "20260810", "activeodds": "true"}
    assert captured["use_cache"] is False
    assert len(list(tmp_path.glob("*.json"))) == 1
