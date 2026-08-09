"""Gates estáticos y de integración del dashboard Telegram Phase 115."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.telegram_bot import (
    TelegramBotConfig,
    TelegramHttpTransport,
    _fixture_miniapp_link,
    _main_keyboard,
)

ROOT = Path(__file__).resolve().parents[1]


class _OkResponse:
    status_code = 200

    @staticmethod
    def json() -> dict[str, Any]:
        return {"ok": True, "result": {}}


class _RecordingSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def post(
        self, url: str, json: dict[str, Any], timeout: tuple[int, int],
    ) -> _OkResponse:
        del timeout
        self.calls.append((url, json))
        return _OkResponse()


def test_bot_menu_exposes_https_web_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El menú nativo conserva fallback y antepone el dashboard seguro."""

    monkeypatch.setenv("DIKAMAHA_MINIAPP_URL", "https://mini.example.test")
    rows = _main_keyboard()["inline_keyboard"]
    assert rows[0][0] == {
        "text": "📊 Abrir dashboard",
        "web_app": {"url": "https://mini.example.test"},
    }
    assert any(row[0].get("callback_data") == "menu:live" for row in rows)


def test_menu_button_uses_set_chat_menu_button() -> None:
    """Configura la Web App global sin iniciar un segundo long polling."""

    session = _RecordingSession()
    config = TelegramBotConfig(
        token="secret", allowed_user_ids=frozenset({7}),
        miniapp_url="https://mini.example.test",
    )
    TelegramHttpTransport(config, session=session).set_chat_menu_button(
        config.miniapp_url or "",
    )
    url, payload = session.calls[0]
    assert url.endswith("/setChatMenuButton")
    assert payload["menu_button"]["web_app"]["url"] == config.miniapp_url


def test_miniapp_url_fails_closed_without_https() -> None:
    with pytest.raises(ValueError, match="https_required"):
        TelegramBotConfig(
            token="secret", allowed_user_ids=frozenset({7}),
            miniapp_url="http://mini.example.test",
        )


def test_fixture_deep_link_encodes_startapp_context() -> None:
    config = TelegramBotConfig(
        token="secret", allowed_user_ids=frozenset({7}),
        bot_username="dikamaha_bot", miniapp_short_name="dikamaha",
    )
    link = _fixture_miniapp_link(
        config, {"match_id": 401880614, "league_slug": "eng.1"}, "fixture",
    )
    assert link == (
        "https://t.me/dikamaha_bot/dikamaha"
        "?startapp=fixture_401880614_ZW5nLjE"
    )
    prediction = _fixture_miniapp_link(config, {
        "match_id": 401880614, "league_slug": "eng.1",
        "home_team_id": 351, "away_team_id": 280,
        "kickoff_ts": "2030-01-10T20:00:00+00:00",
    }, "prediction")
    assert prediction is not None
    assert "?startapp=prediction_401880614_ZW5nLjE_351_280_" in prediction
    assert len(prediction.split("?startapp=", 1)[1]) <= 64


def test_miniapp_image_and_worker_are_bounded() -> None:
    """El runtime es no-root y el worker no compite con getUpdates."""

    dockerfile = (ROOT / "miniapp" / "Dockerfile").read_text(encoding="utf-8")
    worker = (ROOT / "miniapp" / "worker" / "alerts.ts").read_text(
        encoding="utf-8",
    )
    assert "USER node" in dockerfile
    assert "getUpdates" not in worker
    assert "sendMessage" in worker
    assert "pollSeconds" in worker


def test_browser_sources_do_not_read_server_secrets_or_espn() -> None:
    """La UI sólo conversa con el BFF y no conoce ESPN ni la API key."""

    browser_paths = [ROOT / "miniapp" / "app", ROOT / "miniapp" / "components"]
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for directory in browser_paths
        for path in directory.rglob("*.tsx")
    )
    assert "DIKAMAHA_API_KEY" not in source
    assert "espn" not in source.casefold()
    assert "initDataUnsafe" not in source


def test_miniapp_covers_every_bot_explorer_capability() -> None:
    """La paridad visual conserva una única puerta BFF hacia DIKAMAHA."""

    explorer = (ROOT / "miniapp" / "lib" / "explorer.ts").read_text(
        encoding="utf-8",
    )
    routes = {
        "leagues", "dates", "fixtures", "fixture/context", "match/plays",
        "match/statistics", "teams", "team/roster", "player",
    }
    assert all(f'["{route}",' in explorer for route in routes)
    pages = {
        "app/explore/page.tsx", "app/explore/matches/page.tsx",
        "app/explore/teams/page.tsx", "app/status/page.tsx",
        "app/help/page.tsx",
    }
    assert all((ROOT / "miniapp" / page).is_file() for page in pages)
    assert "site.api.espn.com" not in explorer
    assert "sports.core.api.espn.com" not in explorer


def test_catalog_recovery_and_media_proxy_are_present() -> None:
    """Próximos, búsqueda global e imágenes pasan por contratos BFF."""

    teams = (ROOT / "miniapp" / "components" / "team-explorer.tsx").read_text(encoding="utf-8")
    shell = (ROOT / "miniapp" / "components" / "app-shell.tsx").read_text(encoding="utf-8")
    media = (ROOT / "miniapp" / "app" / "api" / "media" / "route.ts").read_text(encoding="utf-8")
    assert "disabled={!league}" not in teams
    assert 'href: "/predictions"' in shell
    assert "/v1/media/image" in media
    assert "X-Dikamaha-Key" in media


def test_prediction_detail_preserves_fixture_identity_and_adds_analytics() -> None:
    """La vista predictiva usa nombres reales y recursos visuales sin tocar modelos."""

    card = (ROOT / "miniapp" / "components" / "ui.tsx").read_text(encoding="utf-8")
    detail = (ROOT / "miniapp" / "components" / "prediction-detail.tsx").read_text(encoding="utf-8")
    analytics = (ROOT / "miniapp" / "components" / "prediction-analytics.tsx").read_text(encoding="utf-8")
    assert "homeName=" in card and "awayName=" in card
    assert '"prediction-identity"' in detail
    assert "catalogFixture?.home_team_name" in detail
    assert "catalogFixture?.away_team_name" in detail
    assert "Local" not in detail and "Visitante" not in detail
    assert "PredictionAnalytics" in detail and "ProbabilityChart" in detail
    assert "Comparativa matemática del partido" in analytics
    assert "derivada de entropía, no confianza calibrada" in analytics


# Version: 1.0.0
# Created: 2026-08-08
