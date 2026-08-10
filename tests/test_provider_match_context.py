"""Pruebas del benchmark analítico externo y su aislamiento financiero."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.espn_prospective_connector import (
    EspnConnectorConfig,
    EspnFetchResult,
    EspnProspectiveConnector,
)
from src.provider_match_context import normalize_provider_match_context


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


def test_pickcenter_never_becomes_a_predictor() -> None:
    result = normalize({
        "pickcenter": [{
            "provider": {"name": "Sportsbook"},
            "homeTeamOdds": {"moneyLine": -120},
            "awayTeamOdds": {"moneyLine": 220},
        }],
    })

    assert result["status"] == "not_published"
    assert result["probabilities"] is None
    assert result["market_context"] == {
        "status": "financial_isolated_available",
        "provider_count": 1,
        "consumed_by_models": False,
        "odds_exposed": False,
    }
    assert "moneyLine" not in str(result)


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
