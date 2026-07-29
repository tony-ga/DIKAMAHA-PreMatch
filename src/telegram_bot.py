"""Adaptador Telegram privado para la API pre-match DIKAMAHA.

# Requirements:
# requests>=2.31
# tenacity>=8.2

Version: 2.0.0
Created: 2026-07-29
"""
from __future__ import annotations

import html
import logging
import os
import threading
import time
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

LOGGER = logging.getLogger(__name__)
TELEGRAM_API = "https://api.telegram.org"
MAX_MESSAGE_LENGTH = 3900


class TelegramTransportError(RuntimeError):
    """Indica un fallo sanitizado del transporte Telegram."""


class PredictionGatewayError(RuntimeError):
    """Indica un fallo sanitizado del servicio DIKAMAHA."""


class TelegramTransport(ABC):
    """Puerto para recibir y enviar mensajes Telegram."""

    @abstractmethod
    def get_updates(
        self, offset: int | None, timeout_seconds: int,
    ) -> list[dict[str, Any]]:
        """Obtiene actualizaciones posteriores al offset."""

    @abstractmethod
    def send_message(self, chat_id: int, text: str, reply_markup: dict[str, Any] | None = None) -> None:
        """Envía un mensaje HTML con teclado opcional."""

    def answer_callback_query(self, callback_id: str) -> None:
        """Confirma una pulsación de botón."""

        return None


class PredictionGateway(ABC):
    """Puerto para consultar la API DIKAMAHA."""

    @abstractmethod
    def predict_fixture(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Resuelve y predice un fixture por nombre o identidad."""

    @abstractmethod
    def predict_upcoming(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Predice un fixture normalizado por IDs."""

    @abstractmethod
    def readiness(self) -> dict[str, Any]:
        """Consulta disponibilidad del servicio."""

    def list_upcoming(
        self, limit: int = 8, leagues: str | None = None,
        date: str | None = None,
    ) -> dict[str, Any]:
        """Lista partidos futuros navegables."""

        return {"fixtures": []}

    def explorer_leagues(self) -> dict[str, Any]:
        """Lista ligas del explorador."""

        return {"leagues": []}

    def explorer_dates(self, mode: str = "past") -> dict[str, Any]:
        """Lista fechas navegables."""

        return {"dates": [], "mode": mode}

    def explorer_fixtures(self, league: str, date: str) -> dict[str, Any]:
        """Lista partidos por liga y fecha."""

        return {"fixtures": []}

    def explorer_fixture_context(
        self, league: str, event_id: str,
    ) -> dict[str, Any]:
        """Consulta contexto visual raw-first de un fixture."""

        return {"status": "unavailable", "reason": "gateway_not_implemented"}

    def explorer_plays(
        self, league: str, match_id: str, competition_id: str,
        scope: str = "key",
    ) -> dict[str, Any]:
        """Consulta play-by-play."""

        return {"plays": [], "scope": scope}

    def explorer_statistics(
        self, league: str, match_id: str, competition_id: str,
    ) -> dict[str, Any]:
        """Consulta estadísticas por periodo."""

        return {"periods": {}, "boxscore": []}

    def explorer_teams(
        self, league: str, query: str = "",
    ) -> dict[str, Any]:
        """Lista o busca equipos."""

        return {"teams": []}

    def explorer_roster(self, league: str, team_id: str) -> dict[str, Any]:
        """Consulta plantilla."""

        return {"players": []}

    def explorer_player(
        self, league: str, team_id: str, player_id: str,
    ) -> dict[str, Any]:
        """Consulta perfil individual."""

        return {}


class GatewayConfig(Protocol):
    """Contrato mínimo de configuración para clientes DIKAMAHA."""

    dikamaha_base_url: str
    dikamaha_api_key: str | None
    request_timeout_seconds: float


@dataclass(frozen=True, slots=True)
class TelegramBotConfig:
    """Configuración inmutable y segura del bot."""

    token: str = field(repr=False)
    allowed_user_ids: frozenset[int]
    dikamaha_base_url: str = "http://127.0.0.1:8000"
    dikamaha_api_key: str | None = field(default=None, repr=False)
    poll_timeout_seconds: int = 25
    request_timeout_seconds: float = 15.0
    rate_limit_requests: int = 10
    rate_limit_window_seconds: int = 60

    def __post_init__(self) -> None:
        """Valida límites sin inspeccionar o exponer secretos."""

        if not self.token:
            raise ValueError("telegram_bot_token_missing")
        if self.poll_timeout_seconds < 1 or self.request_timeout_seconds <= 0:
            raise ValueError("telegram_timeout_invalid")
        if self.rate_limit_requests < 1 or self.rate_limit_window_seconds < 1:
            raise ValueError("telegram_rate_limit_invalid")


def telegram_config_from_env() -> TelegramBotConfig:
    """Construye configuración desde variables de entorno."""

    allowed = _allowed_ids(os.getenv("TELEGRAM_ALLOWED_USER_IDS", ""))
    return TelegramBotConfig(
        token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        allowed_user_ids=allowed,
        dikamaha_base_url=os.getenv(
            "DIKAMAHA_BOT_API_URL", "http://127.0.0.1:8000"),
        dikamaha_api_key=os.getenv("DIKAMAHA_API_KEY") or None,
        poll_timeout_seconds=int(os.getenv("TELEGRAM_POLL_TIMEOUT", "25")),
        request_timeout_seconds=float(
            os.getenv("TELEGRAM_REQUEST_TIMEOUT", "15")),
        rate_limit_requests=int(os.getenv("TELEGRAM_RATE_LIMIT", "10")),
        rate_limit_window_seconds=int(
            os.getenv("TELEGRAM_RATE_WINDOW_SECONDS", "60")),
    )


def _allowed_ids(value: str) -> frozenset[int]:
    """Parsea IDs de usuario separados por coma."""

    if not value.strip():
        return frozenset()
    try:
        return frozenset(
            int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as error:
        raise ValueError("telegram_allowed_user_ids_invalid") from error


class TelegramHttpTransport(TelegramTransport):
    """Cliente HTTP oficial sin filtrar el token en errores."""

    def __init__(
        self, config: TelegramBotConfig,
        session: requests.Session | None = None,
    ) -> None:
        """Inicializa una sesión reutilizable."""

        self._config = config
        self._session = session or requests.Session()
        self._base = f"{TELEGRAM_API}/bot{config.token}"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        retry=retry_if_exception_type(TelegramTransportError),
        reraise=True,
    )
    def get_updates(
        self, offset: int | None, timeout_seconds: int,
    ) -> list[dict[str, Any]]:
        """Consulta long polling con retry exponencial."""

        payload: dict[str, Any] = {
            "timeout": timeout_seconds,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset
        result = self._post("getUpdates", payload, timeout_seconds + 5)
        return [row for row in result if isinstance(row, dict)]

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        retry=retry_if_exception_type(TelegramTransportError),
        reraise=True,
    )
    def send_message(self, chat_id: int, text: str, reply_markup: dict[str, Any] | None = None) -> None:
        """Envía HTML sin previews externos."""

        payload: dict[str, Any] = {
            "chat_id": chat_id, "text": text, "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        self._post("sendMessage", payload, int(self._config.request_timeout_seconds))

    def answer_callback_query(self, callback_id: str) -> None:
        """Confirma callback sin mostrar el identificador sensible."""

        self._post("answerCallbackQuery", {"callback_query_id": callback_id}, 10)

    def _post(
        self, method: str, payload: dict[str, Any], timeout: int,
    ) -> Any:
        """Ejecuta una llamada y sanitiza toda excepción."""

        try:
            response = self._session.post(
                f"{self._base}/{method}", json=payload,
                timeout=(5, timeout))
            data = response.json()
        except (requests.RequestException, ValueError) as error:
            raise TelegramTransportError("telegram_api_unavailable") from error
        if response.status_code >= 400 or not bool(data.get("ok")):
            raise TelegramTransportError("telegram_api_rejected_request")
        return data.get("result", [])


class DikamahaHttpGateway(PredictionGateway):
    """Cliente HTTP de la única fuente de verdad de inferencia."""

    def __init__(
        self, config: GatewayConfig,
        session: requests.Session | None = None,
    ) -> None:
        """Configura URL, autenticación y timeout."""

        self._config = config
        self._session = session or requests.Session()

    def predict_fixture(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Delega en el resolver oficial."""

        return self._post("/v1/predict/fixture", payload)

    def predict_upcoming(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Delega en el endpoint compacto."""

        return self._post("/v1/predict/upcoming", payload)

    def readiness(self) -> dict[str, Any]:
        """Consulta readiness sin autenticación sensible en URL."""

        return self._get("/v1/readiness")

    def list_upcoming(
        self, limit: int = 8, leagues: str | None = None,
        date: str | None = None,
    ) -> dict[str, Any]:
        """Obtiene el catálogo compacto de próximos partidos."""

        params: dict[str, Any] = {"limit": max(1, min(limit, 20))}
        if leagues:
            params["leagues"] = leagues
        if date:
            params["date"] = date
        return self._get("/v1/upcoming", params)

    def explorer_leagues(self) -> dict[str, Any]:
        """Obtiene ligas navegables."""

        return self._get("/v1/explorer/leagues")

    def explorer_dates(self, mode: str = "past") -> dict[str, Any]:
        """Obtiene fechas compactas."""

        return self._get("/v1/explorer/dates", {"mode": mode, "days": 8})

    def explorer_fixtures(self, league: str, date: str) -> dict[str, Any]:
        """Obtiene partidos por liga y fecha."""

        return self._get(
            "/v1/explorer/fixtures", {"league": league, "date": date})

    def explorer_fixture_context(
        self, league: str, event_id: str,
    ) -> dict[str, Any]:
        """Obtiene ficha contextual sin llamar ESPN desde el bot."""

        return self._get("/v1/explorer/fixture/context", {
            "league": league, "event_id": event_id})

    def explorer_plays(
        self, league: str, match_id: str, competition_id: str,
        scope: str = "key",
    ) -> dict[str, Any]:
        """Obtiene plays completos o clave."""

        return self._get("/v1/explorer/match/plays", {
            "league": league, "match_id": match_id,
            "competition_id": competition_id, "scope": scope,
        })

    def explorer_statistics(
        self, league: str, match_id: str, competition_id: str,
    ) -> dict[str, Any]:
        """Obtiene estadísticas de partido."""

        return self._get("/v1/explorer/match/statistics", {
            "league": league, "match_id": match_id,
            "competition_id": competition_id,
        })

    def explorer_teams(
        self, league: str, query: str = "",
    ) -> dict[str, Any]:
        """Lista equipos con filtro opcional."""

        return self._get(
            "/v1/explorer/teams", {"league": league, "query": query})

    def explorer_roster(self, league: str, team_id: str) -> dict[str, Any]:
        """Obtiene plantilla del equipo."""

        return self._get(
            "/v1/explorer/team/roster",
            {"league": league, "team_id": team_id})

    def explorer_player(
        self, league: str, team_id: str, player_id: str,
    ) -> dict[str, Any]:
        """Obtiene perfil y estadísticas individuales."""

        return self._get("/v1/explorer/player", {
            "league": league, "team_id": team_id,
            "player_id": player_id,
        })

    def _headers(self) -> dict[str, str]:
        """Construye headers sin incluir claves ausentes."""

        headers = {"X-Request-ID": f"telegram-{time.time_ns()}"}
        if self._config.dikamaha_api_key:
            headers["X-Dikamaha-Key"] = self._config.dikamaha_api_key
        return headers

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Ejecuta POST y normaliza errores."""

        try:
            response = self._session.post(
                self._config.dikamaha_base_url.rstrip("/") + path,
                json=payload, headers=self._headers(),
                timeout=self._config.request_timeout_seconds)
            return _gateway_payload(response)
        except requests.RequestException as error:
            raise PredictionGatewayError(
                "dikamaha_service_unavailable") from error

    def _get(
        self, path: str, params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Ejecuta GET y normaliza errores."""

        try:
            response = self._session.get(
                self._config.dikamaha_base_url.rstrip("/") + path,
                params=params, headers=self._headers(),
                timeout=self._config.request_timeout_seconds)
            return _gateway_payload(response)
        except requests.RequestException as error:
            raise PredictionGatewayError(
                "dikamaha_service_unavailable") from error


def _gateway_payload(response: requests.Response) -> dict[str, Any]:
    """Valida un payload DIKAMAHA sin devolver cuerpos en errores."""

    try:
        payload = response.json()
    except ValueError as error:
        raise PredictionGatewayError("dikamaha_invalid_response") from error
    if response.status_code >= 400 or not isinstance(payload, dict):
        raise PredictionGatewayError("dikamaha_prediction_rejected")
    return payload


class UserRateLimiter:
    """Rate limit thread-safe por usuario Telegram."""

    def __init__(self, limit: int, window_seconds: int) -> None:
        """Inicializa ventanas acotadas."""

        self._limit = limit
        self._window = window_seconds
        self._lock = threading.Lock()
        self._requests: dict[int, deque[float]] = defaultdict(deque)

    def allow(self, user_id: int, now: float) -> bool:
        """Registra una solicitud cuando existe capacidad."""

        with self._lock:
            values = self._requests[user_id]
            while values and values[0] < now - self._window:
                values.popleft()
            if len(values) >= self._limit:
                return False
            values.append(now)
            return True


class TelegramPredictionBot:
    """Procesa comandos Telegram y delega toda inferencia."""

    def __init__(
        self, config: TelegramBotConfig, transport: TelegramTransport,
        gateway: PredictionGateway,
    ) -> None:
        """Inicializa dependencias y límites."""

        self._config = config
        self._transport = transport
        self._gateway = gateway
        self._rate = UserRateLimiter(
            config.rate_limit_requests, config.rate_limit_window_seconds)
        self._menus: dict[int, dict[str, dict[str, Any]]] = defaultdict(dict)
        self._team_search: dict[int, str] = {}

    def process_update(self, update: dict[str, Any]) -> None:
        """Procesa mensajes y botones únicamente en chats privados."""

        callback = update.get("callback_query")
        if isinstance(callback, dict):
            self._process_callback(callback)
            return

        message = update.get("message")
        if not isinstance(message, dict):
            return
        chat, sender = message.get("chat", {}), message.get("from", {})
        if chat.get("type") != "private" or not isinstance(sender, dict):
            return
        text = message.get("text")
        if not isinstance(text, str):
            return
        chat_id, user_id = int(chat["id"]), int(sender["id"])
        try:
            replies = self._reply(user_id, text.strip())
        except (PredictionGatewayError, ValueError):
            LOGGER.warning("Telegram: mensaje no pudo resolverse.")
            replies = [("No pude cargar esos datos ahora.", _main_keyboard())]
        for part, keyboard in replies:
            _send(self._transport, chat_id, part, keyboard)

    def _process_callback(self, callback: dict[str, Any]) -> None:
        """Resuelve una pulsación usando el menú efímero del usuario."""

        message = callback.get("message", {})
        sender = callback.get("from", {})
        if message.get("chat", {}).get("type") != "private":
            return
        user_id, chat_id = int(sender.get("id", 0)), int(message["chat"]["id"])
        self._transport.answer_callback_query(str(callback.get("id", "")))
        data = str(callback.get("data", ""))
        if user_id not in self._config.allowed_user_ids:
            _send(self._transport, chat_id, _unauthorized_text(), None)
            return
        try:
            replies = self._callback_reply(user_id, data)
        except (PredictionGatewayError, ValueError):
            LOGGER.warning("Telegram: recurso de exploración no disponible.")
            replies = [("No pude cargar esos datos ahora. Intenta otra vez.",
                        _main_keyboard())]
        for part, keyboard in replies:
            _send(self._transport, chat_id, part, keyboard)

    def _reply(self, user_id: int, text: str) -> list[tuple[str, dict[str, Any] | None]]:
        """Autoriza, limita y enruta un comando."""

        command = text.split(maxsplit=1)[0].split("@", 1)[0].lower()
        if command == "/whoami":
            return [(f"Tu ID de Telegram es <code>{user_id}</code>.", None)]
        if command in {"/start", "/help"}:
            return [(_help_text(), _main_keyboard())]
        if user_id not in self._config.allowed_user_ids:
            return [(_unauthorized_text(), None)]
        if user_id in self._team_search and not text.startswith("/"):
            league = self._team_search.pop(user_id)
            return self._team_search_reply(user_id, league, text)
        if not self._rate.allow(user_id, time.monotonic()):
            return [("Demasiadas solicitudes. Intenta de nuevo en un minuto.", None)]
        return self._authorized_reply(command, text)

    def _authorized_reply(self, command: str, text: str) -> list[tuple[str, dict[str, Any] | None]]:
        """Ejecuta comandos autorizados con errores sanitizados."""

        try:
            if command == "/partido":
                payload = _fixture_payload(text)
                return [(part, None) for part in _split_message(_format_prediction(self._gateway.predict_fixture(payload)))]
            if command == "/predict":
                payload = _upcoming_payload(text)
                return [(part, None) for part in _split_message(_format_prediction(self._gateway.predict_upcoming(payload)))]
            if command == "/estado":
                return [(_format_readiness(self._gateway.readiness()), _main_keyboard())]
            if command in {"/partidos", "/menu"}:
                return self._upcoming_root()
            if command == "/buscar_equipo":
                pieces = text.split(maxsplit=2)
                if len(pieces) != 3:
                    raise ValueError("telegram_team_search_usage")
                return self._team_search_reply(user_id, pieces[1], pieces[2])
            return [("Comando no reconocido.\n\n" + _help_text(), _main_keyboard())]
        except ValueError:
            return [(_usage_text(command), _main_keyboard())]
        except PredictionGatewayError:
            LOGGER.warning("Telegram: solicitud DIKAMAHA rechazada.")
            return [("No pude generar la predicción. El servicio no está disponible ahora.", _main_keyboard())]

    def _upcoming_root(self) -> list[tuple[str, dict[str, Any] | None]]:
        """Muestra las tres rutas de descubrimiento de fixtures."""

        keyboard = {"inline_keyboard": [
            [{"text": "🌍 Todos los próximos",
              "callback_data": "upcoming:all"}],
            [{"text": "🏆 Buscar por liga",
              "callback_data": "upcoming:leagues"}],
            [{"text": "📅 Buscar por fecha",
              "callback_data": "upcoming:dates"}],
            [{"text": "🏠 Inicio", "callback_data": "menu:status"}],
        ]}
        return [(
            "🔮 <b>PRÓXIMOS Y PREDICCIONES</b>\n"
            "<i>Elige cómo quieres encontrar el partido</i>",
            keyboard,
        )]

    def _upcoming_reply(
        self, user_id: int, leagues: str | None = None,
        date: str | None = None, title: str = "Todos los próximos",
    ) -> list[tuple[str, dict[str, Any] | None]]:
        """Carga próximos partidos y crea botones de selección."""

        catalog = self._gateway.list_upcoming(
            limit=20, leagues=leagues, date=date)
        fixtures = [row for row in catalog.get("fixtures", []) if isinstance(row, dict)]
        self._menus[user_id] = {f"m{index}": row for index, row in enumerate(fixtures)}
        if not fixtures:
            return [("📭 <b>SIN PARTIDOS PRÓXIMOS</b>\n"
                     "No hay encuentros publicados para ese filtro.",
                     _upcoming_keyboard())]
        buttons = [[{"text": _fixture_button(row), "callback_data": f"match:m{index}"}] for index, row in enumerate(fixtures)]
        buttons.append([{"text": "⬅ Cambiar búsqueda",
                         "callback_data": "menu:upcoming"}])
        return [(
            f"🔮 <b>{html.escape(title.upper())}</b>\n"
            "<i>Ordenados por hora de inicio · toca uno para continuar</i>",
            {"inline_keyboard": buttons},
        )]

    def _upcoming_dates(
        self,
    ) -> list[tuple[str, dict[str, Any] | None]]:
        """Muestra fechas futuras como calendario compacto."""

        rows = self._gateway.explorer_dates("future").get("dates", [])
        buttons = _button_grid([
            {"text": str(row.get("label") or row.get("date")),
             "callback_data": f"update:{row.get('date')}"}
            for row in rows if isinstance(row, dict)
        ], 4)
        return [("📅 <b>BUSCAR POR FECHA</b>\n"
                 "<i>Selecciona una fecha disponible</i>", {
                     "inline_keyboard": buttons + [[
                         {"text": "⬅ Volver",
                          "callback_data": "menu:upcoming"}]],
                 })]

    def _callback_reply(self, user_id: int, data: str) -> list[tuple[str, dict[str, Any] | None]]:
        """Construye la siguiente pantalla del menú."""

        if data == "menu:upcoming":
            return self._upcoming_root()
        if data == "upcoming:all":
            leagues = ",".join(
                str(row.get("slug")) for row in
                self._gateway.explorer_leagues().get("leagues", [])
                if isinstance(row, dict))
            return self._upcoming_reply(user_id, leagues=leagues)
        if data == "upcoming:leagues":
            return self._league_menu("upcoming")
        if data == "upcoming:dates":
            return self._upcoming_dates()
        if data.startswith("update:"):
            date = data.split(":", 1)[1]
            leagues = ",".join(
                str(row.get("slug")) for row in
                self._gateway.explorer_leagues().get("leagues", [])
                if isinstance(row, dict))
            return self._upcoming_reply(
                user_id, leagues=leagues, date=date,
                title=f"Próximos · {date[6:8]}/{date[4:6]}")
        if data == "menu:status":
            return [(_format_readiness(self._gateway.readiness()), _main_keyboard())]
        if data in {"menu:plays", "menu:stats", "menu:players"}:
            return self._league_menu(data.split(":", 1)[1])
        if data.startswith("match:"):
            return self._match_menu(user_id, data.split(":", 1)[1])
        if data.startswith("context:"):
            return self._context_reply(user_id, data.split(":", 1)[1])
        if data.startswith("predict:"):
            return self._predict_menu(user_id, data.split(":", 1)[1])
        if data.startswith("markets:"):
            _, period, key = data.split(":", 2)
            return self._market_menu(user_id, key, period)
        if data.startswith("league:"):
            _, mode, league = data.split(":", 2)
            return self._league_selected(user_id, mode, league)
        if data.startswith("date:"):
            _, mode, league, date = data.split(":", 3)
            return self._date_selected(user_id, mode, league, date)
        if data.startswith("game:"):
            _, mode, key = data.split(":", 2)
            return self._game_selected(user_id, mode, key)
        if data.startswith("plays:"):
            _, scope, key, page = data.split(":", 3)
            return self._plays_reply(user_id, key, scope, int(page))
        if data.startswith("stats:"):
            _, period, key = data.split(":", 2)
            return self._statistics_reply(user_id, key, period)
        if data.startswith("teamsearch:"):
            return self._activate_team_search(user_id, data.split(":", 1)[1])
        if data.startswith("team:"):
            return self._roster_reply(user_id, data.split(":", 1)[1])
        if data.startswith("player:"):
            return self._player_reply(user_id, data.split(":", 1)[1])
        return [(_help_text(), _main_keyboard())]

    def _match_menu(self, user_id: int, key: str) -> list[tuple[str, dict[str, Any] | None]]:
        """Muestra un partido y una sola acción de predicción."""

        fixture = self._menus.get(user_id, {}).get(key)
        if not fixture:
            return [("El menú expiró. Pulsa Próximos partidos.", _main_keyboard())]
        title = _fixture_title({}, fixture)
        text = (
            "⚽ <b>PARTIDO SELECCIONADO</b>\n"
            f"<b>{html.escape(title)}</b>\n"
            f"🕒 <code>{html.escape(_display_kickoff(fixture.get('kickoff_ts')))}</code>\n\n"
            "<i>Genera el resumen y después explora cada periodo.</i>"
        )
        keyboard = {"inline_keyboard": [[
            {"text": "🏟 Contexto", "callback_data": f"context:{key}"},
            {"text": "🔮 Ver predicción", "callback_data": f"predict:{key}"},
        ], [{"text": "⬅ Próximos partidos", "callback_data": "menu:upcoming"}]]}
        return [(text, keyboard)]

    def _context_reply(self, user_id: int, key: str) -> list[tuple[str, dict[str, Any] | None]]:
        """Muestra contexto visible sin afectar el contrato predictivo."""

        fixture = self._menus.get(user_id, {}).get(key)
        if not fixture:
            return [("El menú expiró. Pulsa Próximos partidos.", _main_keyboard())]
        try:
            context = self._gateway.explorer_fixture_context(
                str(fixture["league_slug"]), str(fixture["match_id"]))
        except PredictionGatewayError:
            return [("No pude consultar el contexto del partido.", _main_keyboard())]
        return [(_format_fixture_context(context), _match_keyboard(key))]

    def _predict_menu(self, user_id: int, key: str) -> list[tuple[str, dict[str, Any] | None]]:
        """Ejecuta la predicción del fixture seleccionado."""

        fixture = self._menus.get(user_id, {}).get(key)
        if not fixture:
            return [("El menú expiró. Pulsa Próximos partidos.", _main_keyboard())]
        if not self._rate.allow(user_id, time.monotonic()):
            return [("Demasiadas solicitudes. Intenta de nuevo en un minuto.", None)]
        try:
            result = self._gateway.predict_upcoming(_prediction_payload(fixture))
            result.setdefault("fixture", {
                "home_team_name": fixture.get("home_team_name", "Equipo 1"),
                "away_team_name": fixture.get("away_team_name", "Equipo 2"),
            })
            fixture["_prediction"] = result
            keyboard = _prediction_keyboard(key)
            return [(_format_prediction_summary(result), keyboard)]
        except PredictionGatewayError:
            return [("No pude generar la predicción ahora. Intenta de nuevo.", _main_keyboard())]

    def _market_menu(
        self, user_id: int, key: str, period: str,
    ) -> list[tuple[str, dict[str, Any] | None]]:
        """Muestra mercados del periodo seleccionado."""

        fixture = self._menus.get(user_id, {}).get(key)
        prediction = fixture.get("_prediction") if fixture else None
        if not isinstance(prediction, dict):
            return [("Primero pulsa Ver predicción.", _main_keyboard())]
        return [(_format_market_period(prediction, period), _prediction_keyboard(key))]

    def _league_menu(
        self, mode: str,
    ) -> list[tuple[str, dict[str, Any] | None]]:
        """Muestra ligas en dos columnas."""

        rows = self._gateway.explorer_leagues().get("leagues", [])
        buttons = _button_grid([
            {"text": str(row.get("name") or row.get("slug")),
             "callback_data": f"league:{mode}:{row.get('slug')}"}
            for row in rows if isinstance(row, dict)
        ], 2)
        title = {"plays": "Play-by-play", "stats": "Estadísticas",
                 "players": "Jugadores", "upcoming": "Próximos por liga"
                 }.get(mode, "Explorador")
        return [(f"{_module_icon(mode)} <b>{title.upper()}</b>\n"
                 "<i>1 de 3 · Selecciona una liga</i>", {
            "inline_keyboard": buttons + _back_row(),
        })]

    def _league_selected(
        self, user_id: int, mode: str, league: str,
    ) -> list[tuple[str, dict[str, Any] | None]]:
        """Continúa a fechas o equipos según el módulo."""

        if mode == "upcoming":
            return self._upcoming_reply(
                user_id, leagues=league,
                title=f"Próximos · {league}")
        if mode == "players":
            return self._teams_reply(user_id, league, "")
        rows = self._gateway.explorer_dates("past").get("dates", [])
        buttons = _button_grid([
            {"text": str(row.get("label") or row.get("date")),
             "callback_data": f"date:{mode}:{league}:{row.get('date')}"}
            for row in rows if isinstance(row, dict)
        ], 4)
        return [("📅 <b>SELECCIONA FECHA</b>\n"
                 "<i>2 de 3 · Calendario de los últimos 8 días</i>", {
            "inline_keyboard": buttons + _back_row(),
        })]

    def _date_selected(
        self, user_id: int, mode: str, league: str, date: str,
    ) -> list[tuple[str, dict[str, Any] | None]]:
        """Carga partidos de una fecha y guarda referencias compactas."""

        rows = self._gateway.explorer_fixtures(league, date).get("fixtures", [])
        fixtures = [row for row in rows if isinstance(row, dict)]
        if not fixtures:
            return [("📭 <b>SIN PARTIDOS</b>\nNo hay encuentros publicados "
                     "para esa fecha.", _main_keyboard())]
        buttons = []
        for index, row in enumerate(fixtures[:20]):
            key = f"g{index}"
            self._menus[user_id][key] = {**row, "_league": league}
            buttons.append([{
                "text": _historical_fixture_button(row),
                "callback_data": f"game:{mode}:{key}",
            }])
        return [("⚽ <b>SELECCIONA PARTIDO</b>\n"
                 "<i>3 de 3 · Marcador incluido cuando está disponible</i>", {
            "inline_keyboard": buttons + _back_row(),
        })]

    def _game_selected(
        self, user_id: int, mode: str, key: str,
    ) -> list[tuple[str, dict[str, Any] | None]]:
        """Abre acciones de play-by-play o estadísticas."""

        fixture = self._menus.get(user_id, {}).get(key)
        if not fixture:
            return [("El menú expiró. Vuelve al inicio.", _main_keyboard())]
        if mode == "stats":
            return self._statistics_reply(user_id, key, "total")
        keyboard = {"inline_keyboard": [[
            {"text": "⭐ Eventos clave", "callback_data": f"plays:key:{key}:0"},
            {"text": "📋 Todos", "callback_data": f"plays:all:{key}:0"},
        ], *_back_row()]}
        return [("▶️ <b>PLAY-BY-PLAY</b>\n"
                 f"<b>{html.escape(_fixture_title({}, fixture))}</b>\n\n"
                 "⭐ <b>Eventos clave</b>: goles, tiros, tarjetas y cambios.\n"
                 "📋 <b>Todos</b>: secuencia completa paginada.", keyboard)]

    def _plays_reply(
        self, user_id: int, key: str, scope: str, page: int,
    ) -> list[tuple[str, dict[str, Any] | None]]:
        """Presenta plays en páginas compactas de ocho filas."""

        fixture = self._menus.get(user_id, {}).get(key)
        if not fixture:
            return [("El menú expiró. Vuelve al inicio.", _main_keyboard())]
        cache_key = f"_plays_{scope}"
        if cache_key not in fixture:
            fixture[cache_key] = self._gateway.explorer_plays(
                str(fixture["_league"]), str(fixture["match_id"]),
                str(fixture["competition_id"]), scope)
        rows = fixture[cache_key].get("plays", [])
        text, keyboard = _plays_page(fixture, rows, scope, key, page)
        return [(text, keyboard)]

    def _statistics_reply(
        self, user_id: int, key: str, period: str,
    ) -> list[tuple[str, dict[str, Any] | None]]:
        """Presenta estadísticas separadas por periodo."""

        fixture = self._menus.get(user_id, {}).get(key)
        if not fixture:
            return [("El menú expiró. Vuelve al inicio.", _main_keyboard())]
        if "_statistics" not in fixture:
            fixture["_statistics"] = self._gateway.explorer_statistics(
                str(fixture["_league"]), str(fixture["match_id"]),
                str(fixture["competition_id"]))
        text = _format_statistics(fixture, fixture["_statistics"], period)
        return [(text, _statistics_keyboard(key))]

    def _teams_reply(
        self, user_id: int, league: str, query: str,
    ) -> list[tuple[str, dict[str, Any] | None]]:
        """Lista equipos o coincidencias de búsqueda."""

        rows = self._gateway.explorer_teams(league, query).get("teams", [])
        teams = [row for row in rows if isinstance(row, dict)][:24]
        buttons = []
        for index, row in enumerate(teams):
            key = f"t{index}"
            self._menus[user_id][key] = {**row, "_league": league}
            buttons.append([{
                "text": str(row.get("name") or row.get("id")),
                "callback_data": f"team:{key}",
            }])
        buttons.append([{
            "text": "🔎 Buscar equipo", "callback_data": f"teamsearch:{league}",
        }])
        title = "Coincidencias" if query else "Selecciona equipo"
        subtitle = (f"<i>Filtro: {html.escape(_compact(query, 30))}</i>"
                    if query else "<i>Selecciona uno o usa la búsqueda</i>")
        return [(f"🛡️ <b>{title}</b>\n{subtitle}", {
            "inline_keyboard": buttons + _back_row(),
        })]

    def _activate_team_search(
        self, user_id: int, league: str,
    ) -> list[tuple[str, dict[str, Any] | None]]:
        """Activa una única captura de texto para filtrar equipos."""

        self._team_search[user_id] = league
        return [("🔎 <b>BUSCAR EQUIPO</b>\n"
                 "Escribe al menos dos caracteres.\n"
                 "Ejemplo: <code>Cruz A</code>", None)]

    def _team_search_reply(
        self, user_id: int, league: str, query: str,
    ) -> list[tuple[str, dict[str, Any] | None]]:
        """Busca equipos después de recibir el texto del usuario."""

        cleaned = " ".join(query.split())[:60]
        if len(cleaned) < 2:
            return [("⚠️ <b>BÚSQUEDA MUY CORTA</b>\n"
                     "Escribe al menos dos caracteres.", _main_keyboard())]
        return self._teams_reply(user_id, league, cleaned)

    def _roster_reply(
        self, user_id: int, key: str,
    ) -> list[tuple[str, dict[str, Any] | None]]:
        """Lista jugadores del equipo seleccionado."""

        team = self._menus.get(user_id, {}).get(key)
        if not team:
            return [("El menú expiró. Vuelve al inicio.", _main_keyboard())]
        payload = self._gateway.explorer_roster(
            str(team["_league"]), str(team["id"]))
        players = [row for row in payload.get("players", []) if isinstance(row, dict)]
        buttons = []
        for index, row in enumerate(players[:30]):
            player_key = f"p{index}"
            self._menus[user_id][player_key] = {
                **row, "_league": team["_league"], "_team_id": team["id"],
            }
            label = f"{row.get('jersey') or '—'} · {row.get('name')}"
            buttons.append([{"text": label[:55],
                             "callback_data": f"player:{player_key}"}])
        return [("👥 <b>PLANTILLA</b>\n"
                 f"<b>{html.escape(str(team.get('name', 'Equipo')))}</b>\n"
                 f"<i>{len(players)} jugadores · dorsal y nombre</i>", {
                     "inline_keyboard": buttons + _back_row(),
                 })]

    def _player_reply(
        self, user_id: int, key: str,
    ) -> list[tuple[str, dict[str, Any] | None]]:
        """Muestra perfil individual y estadísticas acumuladas."""

        player = self._menus.get(user_id, {}).get(key)
        if not player:
            return [("El menú expiró. Vuelve al inicio.", _main_keyboard())]
        payload = self._gateway.explorer_player(
            str(player["_league"]), str(player["_team_id"]),
            str(player["id"]))
        return [(_format_player(payload), _main_keyboard())]


def _fixture_payload(text: str) -> dict[str, Any]:
    """Parsea búsqueda amigable de fixture por nombres."""

    pieces = text.split(maxsplit=3)
    if len(pieces) != 4 or "|" not in pieces[3]:
        raise ValueError("telegram_fixture_usage")
    home, away = (item.strip() for item in pieces[3].split("|", 1))
    if not home or not away:
        raise ValueError("telegram_fixture_teams_missing")
    return {
        "league_slug": pieces[1], "kickoff_date": pieces[2],
        "home_team_name": home, "away_team_name": away,
    }


def _upcoming_payload(text: str) -> dict[str, Any]:
    """Parsea predicción avanzada por IDs ESPN."""

    pieces = text.split()
    if len(pieces) not in {5, 6}:
        raise ValueError("telegram_predict_usage")
    payload: dict[str, Any] = {
        "league_slug": pieces[1], "home_team_id": int(pieces[2]),
        "away_team_id": int(pieces[3]), "kickoff_ts": pieces[4],
    }
    if len(pieces) == 6:
        payload["match_id"] = int(pieces[5])
    return payload


def _format_prediction(payload: dict[str, Any]) -> str:
    """Renderiza la predicción pública sin terminología interna."""

    title = _fixture_title(payload, payload.get("fixture", {}))
    lines = [
        "🔮 <b>PREDICCIÓN PRE-MATCH</b>",
        f"<b>{html.escape(title)}</b>",
        f"🏆 {html.escape(str(payload.get('league_slug', '')))} · "
        f"🕒 {_display_kickoff(payload.get('kickoff_ts'))}",
        "", "<b>Mercados principales</b>",
        _prediction_table(payload),
    ]
    lines.extend(_shadow_lines(payload))
    lines.extend(["", "ℹ️ <i>Probabilidades calculadas antes del inicio.</i>"])
    return "\n".join(lines)


def _fixture_title(
    payload: dict[str, Any], fixture: dict[str, Any],
) -> str:
    """Obtiene equipos legibles o usa IDs."""

    home = fixture.get("home_team_name", payload.get("home_team_id", "Equipo 1"))
    away = fixture.get("away_team_name", payload.get("away_team_id", "Equipo 2"))
    return f"{home} vs {away}"


def _send(transport: TelegramTransport, chat_id: int, text: str, keyboard: dict[str, Any] | None) -> None:
    """Envía con teclado cuando el transporte lo admite."""

    if keyboard is None:
        transport.send_message(chat_id, text)
        return
    try:
        transport.send_message(chat_id, text, keyboard)
    except TypeError:
        transport.send_message(chat_id, text)


def _main_keyboard() -> dict[str, Any]:
    """Devuelve el menú principal de navegación."""

    return {"inline_keyboard": [
        [{"text": "🔮 Próximos y predicciones",
          "callback_data": "menu:upcoming"}],
        [{"text": "▶️ Play-by-play", "callback_data": "menu:plays"},
         {"text": "📊 Estadísticas", "callback_data": "menu:stats"}],
        [{"text": "👤 Equipos y jugadores",
          "callback_data": "menu:players"}],
        [{"text": "✅ Estado", "callback_data": "menu:status"}],
    ]}


def _upcoming_keyboard() -> dict[str, Any]:
    """Devuelve acceso directo a los filtros de próximos."""

    return {"inline_keyboard": [[
        {"text": "🌍 Todos", "callback_data": "upcoming:all"},
        {"text": "🏆 Liga", "callback_data": "upcoming:leagues"},
        {"text": "📅 Fecha", "callback_data": "upcoming:dates"},
    ], [{"text": "🏠 Inicio", "callback_data": "menu:status"}]]}


def _fixture_button(fixture: dict[str, Any]) -> str:
    """Construye texto corto para un botón de partido."""

    title = _fixture_title({}, fixture)
    kickoff = str(fixture.get("kickoff_ts", ""))[:16].replace("T", " ")
    return f"{title[:40]} · {kickoff}"


def _match_keyboard(key: str) -> dict[str, Any]:
    """Construye acciones compactas y reutilizables de un fixture."""

    return {"inline_keyboard": [[
        {"text": "🏟 Contexto", "callback_data": f"context:{key}"},
        {"text": "🔮 Ver predicción", "callback_data": f"predict:{key}"},
    ], [{"text": "⬅ Próximos partidos", "callback_data": "menu:upcoming"}]]}


def _format_fixture_context(context: dict[str, Any]) -> str:
    """Renderiza una ficha HTML corta con ausencias explícitas de ESPN."""

    if context.get("status") != "available":
        return "🏟 <b>CONTEXTO DEL PARTIDO</b>\n<i>Snapshot no disponible todavía.</i>"
    fixture = context.get("fixture", {}); competition = context.get("competition", {})
    venue = context.get("venue", {}); teams = context.get("teams", {})
    home = teams.get("home", {}).get("name", "Local"); away = teams.get("away", {}).get("name", "Visitante")
    fields = [
        f"🏆 {competition.get('name') or 'Competición no publicada'}",
        f"📅 {html.escape(_display_kickoff(fixture.get('kickoff_ts')))}",
        f"🏟 {html.escape(_venue_label(venue))}",
        f"🧭 {html.escape(str(competition.get('phase') or 'Fase no publicada'))}",
    ]
    officials = context.get("officials", []); broadcasts = context.get("broadcasts", [])
    if officials:
        fields.append("👤 " + html.escape(_names(officials, "name")))
    if broadcasts:
        fields.append("📺 " + html.escape(_names(broadcasts, "name")))
    team_context = context.get("team_context", {})
    standings = _standing_line(team_context, "home", str(home), team_context, "away", str(away))
    if standings:
        fields.append("📊 " + html.escape(standings))
    availability = _availability_line(context.get("availability"), str(home), str(away))
    if availability:
        fields.append("🩺 " + html.escape(availability))
    editorial = _editorial_line(context.get("editorial"))
    if editorial:
        fields.append("📰 " + html.escape(editorial))
    return "\n".join(["🏟 <b>CONTEXTO DEL PARTIDO</b>", f"<b>{html.escape(str(home))} vs {html.escape(str(away))}</b>", *fields, "", "<i>Contexto informativo · no modifica la predicción.</i>"])


def _venue_label(venue: Any) -> str:
    """Formatea sede sin inventar ciudad o capacidad ausentes."""

    row = venue if isinstance(venue, dict) else {}
    values = [row.get("name"), row.get("city"), row.get("country")]
    return " · ".join(str(value) for value in values if value) or "Sede no publicada"


def _names(rows: Any, key: str) -> str:
    """Lista hasta tres valores disponibles y evita mensajes demasiado largos."""

    values = [str(row.get(key)) for row in rows if isinstance(row, dict) and row.get(key)]
    return ", ".join(values[:3]) + ("…" if len(values) > 3 else "")


def _standing_line(
    context: Any, first_side: str, first_name: str,
    second_context: Any, second_side: str, second_name: str,
) -> str:
    """Muestra posición y puntos sin calcular ni completar una tabla propia."""

    left = _standing_label(context, first_side, first_name)
    right = _standing_label(second_context, second_side, second_name)
    return " · ".join(value for value in (left, right) if value)


def _standing_label(context: Any, side: str, name: str) -> str:
    """Formatea una fila publicada por ESPN o devuelve ausencia compacta."""

    row = context.get(side, {}) if isinstance(context, dict) else {}
    standing = row.get("standing") if isinstance(row, dict) else None
    if not isinstance(standing, dict) or not standing.get("rank"):
        return ""
    return f"{name}: #{standing['rank']} · {standing.get('points') or '–'} pts"


def _availability_line(availability: Any, home: str, away: str) -> str:
    """Muestra disponibilidad publicada sin afirmar que no existan lesiones."""

    rows = availability if isinstance(availability, dict) else {}
    values = [_availability_label(rows.get("home"), home), _availability_label(rows.get("away"), away)]
    return " · ".join(value for value in values if value)


def _availability_label(row: Any, name: str) -> str:
    """Resume roster y reporte de lesiones con un fallback explícito."""

    data = row if isinstance(row, dict) else {}
    count = data.get("roster_count")
    status = data.get("injury_report_status")
    if status == "not_published":
        return f"{name}: reporte no publicado"
    injuries = data.get("published_injuries") if isinstance(data.get("published_injuries"), list) else []
    return f"{name}: {len(injuries)} incidencias publicadas · roster {count or 'N/D'}"


def _editorial_line(editorial: Any) -> str:
    """Añade un titular editorial corto, sin implicar impacto predictivo."""

    data = editorial if isinstance(editorial, dict) else {}
    rows = data.get("articles") if isinstance(data.get("articles"), list) else []
    headline = next((row.get("headline") for row in rows if isinstance(row, dict) and row.get("headline")), None)
    return str(headline)[:140] if headline else ""


def _prediction_payload(fixture: dict[str, Any]) -> dict[str, Any]:
    """Convierte un fixture de catálogo al contrato compacto."""

    return {
        "league_slug": str(fixture["league_slug"]),
        "home_team_id": int(fixture["home_team_id"]),
        "away_team_id": int(fixture["away_team_id"]),
        "kickoff_ts": str(fixture["kickoff_ts"]),
        "match_id": int(fixture["match_id"]),
    }


def _prediction_keyboard(key: str) -> dict[str, Any]:
    """Construye submenú de mercados por periodo."""

    return {"inline_keyboard": [
        [{"text": "⏱ 1T", "callback_data": f"markets:first_half:{key}"},
         {"text": "⏱ 2T", "callback_data": f"markets:second_half:{key}"}],
        [{"text": "📊 Totales", "callback_data": f"markets:full_match:{key}"}],
        [{"text": "⬅ Partidos", "callback_data": "menu:upcoming"},
         {"text": "🏠 Inicio", "callback_data": "menu:status"}],
    ]}


def _format_prediction_summary(payload: dict[str, Any]) -> str:
    """Resume resultado y mercados de goles sin saturar la pantalla."""

    title = html.escape(_fixture_title(payload, payload.get("fixture", {})))
    return "\n".join([
        "🔮 <b>PREDICCIÓN PRE-MATCH</b>",
        f"<b>{title}</b>",
        f"🕒 <code>{html.escape(_display_kickoff(payload.get('kickoff_ts')))}</code>",
        "",
        "<b>Mercados principales</b>",
        _prediction_table(payload),
        "",
        "👇 <i>Abre 1T, 2T o Totales para corners, tiros y tarjetas.</i>",
        "ℹ️ <i>Estimaciones pre-partido.</i>",
    ])


def _format_market_period(payload: dict[str, Any], period: str) -> str:
    """Agrupa probabilidades y conteos esperados por periodo."""

    market = payload.get("experimental_team_markets") or {}
    scenarios, selected = _period_market_rows(market, period)
    label = {"first_half": "Primer tiempo", "second_half": "Segundo tiempo",
             "full_match": "Totales"}.get(period, period)
    icon = {"first_half": "1️⃣", "second_half": "2️⃣",
            "full_match": "📊"}.get(period, "📊")
    team_names = _team_names(payload)
    lines = [f"{icon} <b>Mercados · {html.escape(label)}</b>"]
    if scenarios:
        lines.extend([
            "⭐ <b>Escenarios más probables</b>",
            *_recommended_market_lines(scenarios, team_names)])
    if selected:
        lines.extend([
            "<b>Probabilidades</b>", _market_table(selected, team_names)])
    lines.extend(_expected_period_lines(market, period, team_names))
    if len(lines) == 1:
        lines.append("Sin líneas probabilísticas disponibles.")
    lines.extend(["", "ℹ️ <i>Estimaciones pre-partido.</i>"])
    return "\n".join(lines)


def _period_market_rows(
    market: dict[str, Any], period: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Selecciona recomendaciones y líneas heredadas de un periodo."""

    recommended = market.get("recommended_market_view") or []
    rows = market.get("user_market_view") or []
    scenarios = [
        row for row in recommended
        if isinstance(row, dict) and row.get("period") == period]
    selected = [
        row for row in rows
        if isinstance(row, dict) and row.get("period") == period]
    return scenarios, selected


def _recommended_market_lines(
    rows: list[dict[str, Any]], team_names: tuple[str, str],
) -> list[str]:
    """Presenta la selección distribucional sin líneas triviales."""

    output = []
    for row in rows:
        team = {
            "home": team_names[0], "away": team_names[1], "total": "Total",
        }.get(str(row.get("team_side")), "Total")
        metric = {
            "corners": "Corners", "shots": "Tiros",
            "yellow_cards": "Tarjetas",
        }.get(str(row.get("metric")), str(row.get("metric") or "Mercado"))
        direction = "Más" if row.get("direction") == "over" else "Menos"
        label = f"{metric} · {team} · {direction} {row.get('line')}"
        output.append(
            f"• {html.escape(label)}: {_percent(row.get('probability'))}")
    return output


def _expected_period_lines(
    market: dict[str, Any], period: str, team_names: tuple[str, str],
) -> list[str]:
    """Renderiza conteos Markov esperados por equipo y periodo."""

    values = market.get("markov_expected_counts")
    if not isinstance(values, dict):
        return []
    suffix = {
        "first_half": "_first_half", "second_half": "_second_half",
    }.get(period)
    rows: list[list[str]] = []
    for side, label in zip(("home", "away"), team_names):
        row = values.get(side, {}) if isinstance(values.get(side), dict) else {}
        metrics = _expected_metric_values(row, suffix)
        rows.append([_compact(label, 16), *metrics])
    return ["", "<b>Conteos esperados</b>", _pre_table(
        ["Equipo", "COR", "TIR", "TA"], rows, [16, 5, 5, 5],
        numeric={1, 2, 3})]


def _expected_metrics(
    row: dict[str, Any], suffix: str | None,
) -> list[str]:
    """Selecciona corners, tiros y amarillas sin líneas extensas."""

    names = (("corners", "Córners"), ("shots", "Tiros"),
             ("yellow_cards", "TA"))
    output = []
    for key, label in names:
        if suffix:
            value = row.get(key + suffix)
        else:
            value = _sum_halves(row, key)
        if value is not None:
            output.append(f"{label} {_decimal(value)}")
    return output


def _expected_metric_values(
    row: dict[str, Any], suffix: str | None,
) -> list[str]:
    """Devuelve valores crudos para la tabla de conteos."""

    output = []
    for key in ("corners", "shots", "yellow_cards"):
        value = row.get(key + suffix) if suffix else _sum_halves(row, key)
        try:
            output.append(f"{float(value):.2f}")
        except (TypeError, ValueError):
            output.append("N/D")
    return output


def _sum_halves(row: dict[str, Any], metric: str) -> float | None:
    """Suma conteos esperados de 1T y 2T."""

    first, second = row.get(metric + "_first_half"), row.get(
        metric + "_second_half")
    try:
        return float(first) + float(second)
    except (TypeError, ValueError):
        return None


def _decimal(value: Any) -> str:
    """Formatea conteos esperados a dos decimales."""

    try:
        return f"<b>{float(value):.2f}</b>"
    except (TypeError, ValueError):
        return "N/D"


def _button_grid(
    buttons: list[dict[str, str]], columns: int,
) -> list[list[dict[str, str]]]:
    """Agrupa botones en filas de tamaño fijo."""

    return [
        buttons[index:index + columns]
        for index in range(0, len(buttons), columns)
    ]


def _back_row() -> list[list[dict[str, str]]]:
    """Devuelve navegación universal al inicio."""

    return [[{"text": "🏠 Inicio", "callback_data": "menu:status"}]]


def _historical_fixture_button(fixture: dict[str, Any]) -> str:
    """Resume equipos, marcador y estado en un botón."""

    title = _fixture_title({}, fixture)
    home, away = fixture.get("home_score"), fixture.get("away_score")
    score = f" {home}-{away}" if home is not None and away is not None else ""
    return (title + score)[:58]


def _plays_page(
    fixture: dict[str, Any], rows: list[Any], scope: str, key: str, page: int,
) -> tuple[str, dict[str, Any]]:
    """Construye una página compacta de play-by-play."""

    page_size = 8
    maximum = max(0, (len(rows) - 1) // page_size)
    current = min(max(page, 0), maximum)
    selected = rows[current * page_size:(current + 1) * page_size]
    detail = "CLAVE" if scope == "key" else "COMPLETO"
    lines = [
        "▶️ <b>PLAY-BY-PLAY</b>",
        f"<b>{html.escape(_fixture_title({}, fixture))}</b>",
        f"<code>{detail} · PÁGINA {current + 1}/{maximum + 1} · "
        f"{len(rows)} EVENTOS</code>", "",
    ]
    lines.extend(_play_card(row) for row in selected if isinstance(row, dict))
    return "\n".join(lines), _plays_keyboard(scope, key, current, maximum)


def _play_line(row: dict[str, Any]) -> str:
    """Compacta un evento en una línea legible."""

    period = "1T" if row.get("period") == 1 else "2T"
    clock = html.escape(str(row.get("clock") or "—"))
    label = html.escape(str(row.get("label") or "Evento"))
    text = html.escape(_compact(str(row.get("text") or ""), 135))
    return f"<code>{period} {clock:>6}</code> · <b>{label}</b> · {text}"


def _play_card(row: dict[str, Any]) -> str:
    """Presenta un evento como fila visual compacta."""

    period = "1T" if row.get("period") == 1 else "2T"
    clock = html.escape(str(row.get("clock") or "—"))
    label = html.escape(str(row.get("label") or "Evento"))
    text = html.escape(_compact(str(row.get("text") or ""), 110))
    icon = _event_icon(str(row.get("type") or ""))
    return f"{icon} <code>{period} {clock}</code> <b>{label}</b>\n└ {text}"


def _compact(value: str, limit: int) -> str:
    """Elimina saltos y recorta texto con elipsis."""

    clean = " ".join(value.split())
    return clean if len(clean) <= limit else clean[:limit - 1].rstrip() + "…"


def _pre_table(
    headers: list[str], rows: list[list[str]], widths: list[int],
    numeric: set[int] | None = None,
) -> str:
    """Construye una tabla HTML monoespaciada y acotada."""

    right = numeric or set()
    head = _table_row(headers, widths, set())
    separator = " ".join("-" * width for width in widths)
    body = [_table_row(row, widths, right) for row in rows]
    block = "\n".join([head, separator, *body])
    return f"<pre>{html.escape(block)}</pre>"


def _table_row(
    values: list[str], widths: list[int], right: set[int],
) -> str:
    """Alinea una fila sin permitir saltos internos."""

    cells = []
    for index, width in enumerate(widths):
        value = _compact(str(values[index]) if index < len(values) else "", width)
        cells.append(value.rjust(width) if index in right else value.ljust(width))
    return " ".join(cells)


def _display_kickoff(value: Any) -> str:
    """Convierte kickoff ISO a una fecha corta legible."""

    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.strftime("%d/%m/%Y · %H:%M UTC")
    except ValueError:
        return _compact(str(value or "N/D"), 30)


def _module_icon(mode: str) -> str:
    """Asigna un icono estable a cada módulo."""

    return {"plays": "▶️", "stats": "📊", "players": "👤",
            "upcoming": "🔮"}.get(mode, "📂")


def _event_icon(play_type: str) -> str:
    """Asigna símbolos visuales a tipos de play."""

    if play_type in {"goal", "penalty---scored", "own-goal"}:
        return "⚽"
    if play_type == "yellow-card":
        return "🟨"
    if play_type == "red-card":
        return "🟥"
    if play_type == "substitution":
        return "🔄"
    if play_type == "corner-awarded":
        return "🚩"
    if play_type.startswith("shot"):
        return "🎯"
    if play_type == "save":
        return "🧤"
    return "•"


def _plays_keyboard(
    scope: str, key: str, page: int, maximum: int,
) -> dict[str, Any]:
    """Crea navegación de páginas sin callbacks extensos."""

    row = []
    if page > 0:
        row.append({"text": "◀", "callback_data": f"plays:{scope}:{key}:{page - 1}"})
    if page < maximum:
        row.append({"text": "▶", "callback_data": f"plays:{scope}:{key}:{page + 1}"})
    rows = [row] if row else []
    rows.append([{"text": "🏠 Inicio", "callback_data": "menu:status"}])
    return {"inline_keyboard": rows}


def _statistics_keyboard(key: str) -> dict[str, Any]:
    """Crea selector de 1T, 2T y total."""

    return {"inline_keyboard": [[
        {"text": "1T", "callback_data": f"stats:first_half:{key}"},
        {"text": "2T", "callback_data": f"stats:second_half:{key}"},
        {"text": "Total", "callback_data": f"stats:total:{key}"},
    ], [{"text": "🏠 Inicio", "callback_data": "menu:status"}]]}


def _format_statistics(
    fixture: dict[str, Any], payload: dict[str, Any], period: str,
) -> str:
    """Formatea comparación por equipo y periodo."""

    teams = payload.get("teams", {})
    fallback_home, fallback_away = _team_names({"fixture": fixture})
    home = (teams.get("home") or {}).get("name", fallback_home)
    away = (teams.get("away") or {}).get("name", fallback_away)
    label = {"first_half": "1T", "second_half": "2T",
             "total": "Total"}.get(period, period)
    periods = payload.get("periods", {})
    home_stats = (periods.get("home") or {}).get(period, {})
    away_stats = (periods.get("away") or {}).get(period, {})
    rows = [[_stat_label(metric), str(home_stats.get(metric, 0)),
             str(away_stats.get(metric, 0))] for metric in (
                 "goals", "shots", "shots_on_target", "corners",
                 "yellow_cards", "red_cards", "fouls", "offsides", "saves",
                 "substitutions")]
    lines = [
        f"📊 <b>ESTADÍSTICAS · {label.upper()}</b>",
        f"<b>{html.escape(_fixture_title({}, fixture))}</b>",
        _pre_table(
            ["Evento", _compact(str(home), 14), _compact(str(away), 14)],
            rows, [16, 14, 14], numeric={1, 2}),
    ]
    if period == "total":
        lines.extend(_boxscore_lines(
            payload.get("boxscore", []), (str(home), str(away))))
    lines.extend(["", "✓ <i>1T + 2T reconciliado con el total.</i>"])
    return "\n".join(lines)


def _stat_line(
    metric: str, home: dict[str, Any], away: dict[str, Any],
) -> str:
    """Renderiza una métrica en formato local | visitante."""

    labels = {
        "goals": "Goles", "shots": "Tiros", "shots_on_target": "A puerta",
        "corners": "Córners", "yellow_cards": "Amarillas",
        "red_cards": "Rojas", "fouls": "Faltas", "offsides": "Fueras juego",
        "saves": "Atajadas", "substitutions": "Cambios",
    }
    return f"{labels[metric]}: <b>{home.get(metric, 0)}</b> | <b>{away.get(metric, 0)}</b>"


def _stat_label(metric: str) -> str:
    """Traduce métricas a etiquetas compactas."""

    return {
        "goals": "Goles", "shots": "Tiros", "shots_on_target": "A puerta",
        "corners": "Córners", "yellow_cards": "Amarillas",
        "red_cards": "Rojas", "fouls": "Faltas", "offsides": "Fueras juego",
        "saves": "Atajadas", "substitutions": "Cambios",
    }[metric]


def _boxscore_lines(value: Any, team_names: tuple[str, str]) -> list[str]:
    """Añade estadísticas totales no derivables por mitad."""

    rows = value if isinstance(value, list) else []
    indexed = {str(row.get("side")): row for row in rows if isinstance(row, dict)}
    home = (indexed.get("home") or {}).get("statistics", {})
    away = (indexed.get("away") or {}).get("statistics", {})
    table_rows = []
    for key, label in (("possessionPct", "Posesión %"),
                       ("totalPasses", "Pases"),
                       ("accuratePasses", "Pases precisos"),
                       ("totalTackles", "Entradas")):
        table_rows.append([
            label, str(home.get(key, "N/D")), str(away.get(key, "N/D"))])
    return ["", "<b>Boxscore ESPN · total</b>", _pre_table(
        ["Métrica", _compact(team_names[0], 14), _compact(team_names[1], 14)],
        table_rows, [16, 14, 14], numeric={1, 2})]


def _format_player(payload: dict[str, Any]) -> str:
    """Formatea perfil y acumulados relevantes en líneas compactas."""

    profile_rows = [
        ["Posición", str(payload.get("position") or "N/D")],
        ["Edad", str(payload.get("age") or "N/D")],
        ["Altura", str(payload.get("height") or "N/D")],
        ["Peso", str(payload.get("weight") or "N/D")],
        ["Nacionalidad", str(payload.get("citizenship") or "N/D")],
        ["Estado", "Activo" if payload.get("active") else "No confirmado"],
    ]
    lines = [
        "👤 <b>PERFIL DEL JUGADOR</b>",
        f"<b>{html.escape(str(payload.get('name') or 'Jugador'))}</b>", "",
        _pre_table(["Dato", "Valor"], profile_rows, [13, 22]),
    ]
    statistics = payload.get("statistics")
    if isinstance(statistics, list) and statistics:
        values = {str(row.get("name")): row.get("value") for row in statistics}
        lines.extend(["", "📈 <b>ESTADÍSTICAS DE TEMPORADA</b>",
                      _player_stats_table(values)])
    else:
        lines.extend(["", "Estadísticas acumuladas no publicadas por ESPN."])
    return "\n".join(lines)


def _player_stat_summary(values: dict[str, Any]) -> str:
    """Resume las estadísticas individuales más útiles."""

    pairs = (("appearances", "PJ"), ("totalGoals", "G"),
             ("goalAssists", "A"), ("totalShots", "Tiros"),
             ("shotsOnTarget", "A puerta"), ("yellowCards", "TA"),
             ("redCards", "TR"), ("saves", "Atajadas"))
    return " · ".join(
        f"{label} <b>{values.get(key, '0')}</b>" for key, label in pairs)


def _player_stats_table(values: dict[str, Any]) -> str:
    """Presenta acumulados del jugador en dos columnas."""

    pairs = (("appearances", "Partidos"), ("totalGoals", "Goles"),
             ("goalAssists", "Asistencias"), ("totalShots", "Tiros"),
             ("shotsOnTarget", "A puerta"), ("yellowCards", "Amarillas"),
             ("redCards", "Rojas"), ("saves", "Atajadas"))
    rows = [[label, str(values.get(key, "0"))] for key, label in pairs]
    return _pre_table(["Métrica", "Total"], rows, [16, 7], numeric={1})


def _shadow_lines(payload: dict[str, Any]) -> list[str]:
    """Renderiza la vista de mercados si está disponible."""

    value = payload.get("experimental_team_markets")
    if not isinstance(value, dict):
        return ["", "<b>Mercados adicionales</b>", "No disponibles."]
    rows = value.get("user_market_view")
    if not isinstance(rows, list):
        return ["", "<b>Mercados adicionales</b>", "No disponibles."]
    valid = [row for row in rows if isinstance(row, dict)]
    return ["", "📊 <b>Mercados adicionales</b>",
            _market_table(valid, _team_names(payload))]


def _prediction_table(payload: dict[str, Any]) -> str:
    """Crea tabla de probabilidades oficiales."""

    home, away = _team_names(payload)
    rows = [
        [f"1 · {_compact(home, 18)}",
         _percent_plain(payload.get("probability_home"))],
        ["X · Empate", _percent_plain(payload.get("probability_draw"))],
        [f"2 · {_compact(away, 18)}",
         _percent_plain(payload.get("probability_away"))],
        ["Más 2.5", _percent_plain(payload.get("probability_over_2_5"))],
        ["Ambos marcan", _percent_plain(payload.get("probability_btts"))],
    ]
    return _pre_table(["Mercado", "Prob."], rows, [22, 7], numeric={1})


def _market_table(
    rows: list[dict[str, Any]], team_names: tuple[str, str],
) -> str:
    """Crea tabla compacta de mercado y probabilidad."""

    values = [[
        _compact(_market_label(row, team_names), 26),
        _percent_plain(row.get("probability")),
    ] for row in rows]
    return _pre_table(
        ["Mercado", "Prob."], values, [26, 7], numeric={1})


def _market_line(
    row: dict[str, Any], team_names: tuple[str, str],
) -> str:
    """Formatea una línea pública sin procedencia interna."""

    label = _market_label(row, team_names)
    return f"• {html.escape(label)}: {_percent(row.get('probability'))}"


def _market_label(
    row: dict[str, Any], team_names: tuple[str, str],
) -> str:
    """Construye una etiqueta comercial compacta."""

    metric = {
        "corners": "corners", "shots": "tiros",
        "shots_on_target": "tiros a puerta",
    }.get(str(row.get("metric")), str(row.get("metric", "mercado")))
    side = {
        "home": team_names[0], "away": team_names[1], "total": "total",
    }.get(str(row.get("team_side")), str(row.get("team_side", "")))
    period = {
        "full_match": "partido", "first_half": "1T", "second_half": "2T",
    }.get(str(row.get("period")), str(row.get("period", "")))
    return f"{metric} {side} {period} +{row.get('line')}"


def _team_names(payload: dict[str, Any]) -> tuple[str, str]:
    """Obtiene nombres reales del fixture sin etiquetas de orientación."""

    fixture = payload.get("fixture")
    source = fixture if isinstance(fixture, dict) else payload
    home = source.get("home_team_name") or payload.get("home_team_name")
    away = source.get("away_team_name") or payload.get("away_team_name")
    home = home or payload.get("home_team_id") or "Equipo 1"
    away = away or payload.get("away_team_id") or "Equipo 2"
    return str(home), str(away)


def _percent(value: Any) -> str:
    """Formatea una probabilidad válida."""

    try:
        probability = float(value)
    except (TypeError, ValueError):
        return "N/D"
    return f"<b>{probability:.1%}</b>"


def _percent_plain(value: Any) -> str:
    """Formatea porcentaje sin HTML para tablas preformateadas."""

    try:
        return f"{float(value):.1%}"
    except (TypeError, ValueError):
        return "N/D"


def _format_readiness(payload: dict[str, Any]) -> str:
    """Resume readiness DIKAMAHA."""

    ready = bool(payload.get("ready"))
    state = "OPERATIVO" if ready else "NO DISPONIBLE"
    contract = str(payload.get("contract_version", "unknown"))
    icon = "🟢" if ready else "🔴"
    return (
        f"{icon} <b>ESTADO DEL SISTEMA</b>\n"
        + _pre_table(["Componente", "Estado"], [
            ["API DIKAMAHA", state],
            ["Predicción", "Lista" if ready else "Bloqueada"],
            ["Explorador", "Listo" if ready else "Bloqueado"],
            ["Contrato", contract],
        ], [16, 18])
    )


def _split_message(text: str) -> list[str]:
    """Divide respuestas largas respetando líneas."""

    if len(text) <= MAX_MESSAGE_LENGTH:
        return [text]
    parts, current = [], ""
    for line in text.splitlines(keepends=True):
        if current and len(current) + len(line) > MAX_MESSAGE_LENGTH:
            parts.append(current.rstrip())
            current = ""
        current += line
    if current:
        parts.append(current.rstrip())
    return parts


def _help_text() -> str:
    """Devuelve ayuda segura y reusable."""

    return (
        "⚽ <b>DIKAMAHA</b>\n"
        "<i>Centro de análisis pre-match</i>\n\n"
        "🔮 <b>Predicciones</b>\n"
        "Resultados, goles y mercados por 1T/2T/total.\n\n"
        "▶️ <b>Play-by-play</b>\n"
        "Liga → fecha → partido → eventos clave o completos.\n\n"
        "📊 <b>Estadísticas</b>\n"
        "Comparativas por mitad y boxscore total.\n\n"
        "👤 <b>Jugadores</b>\n"
        "Liga → equipo → plantilla → perfil.\n\n"
        "🧪 <i>Mercados secundarios continúan en observación.</i>")


def _unauthorized_text() -> str:
    """Informa cómo solicitar acceso sin revelar configuración."""

    return (
        "🔒 <b>ACCESO PRIVADO</b>\n"
        "Este bot está en prueba privada.\n"
        "Tu usuario no está autorizado.\n"
        "Usa /whoami para consultar tu identificador.")


def _usage_text(command: str) -> str:
    """Devuelve sintaxis sin detalles internos."""

    if command == "/partido":
        return (
            "Uso:\n<code>/partido liga YYYYMMDD Equipo A | Equipo B</code>")
    if command == "/predict":
        return (
            "Uso:\n<code>/predict liga home_id away_id kickoff_iso "
            "[match_id]</code>")
    return _help_text()


class LongPollingRunner:
    """Ejecutor bloqueante con offset monotónico."""

    def __init__(
        self, bot: TelegramPredictionBot, transport: TelegramTransport,
        timeout_seconds: int,
    ) -> None:
        """Inicializa el ciclo sin consumir actualizaciones."""

        self._bot = bot
        self._transport = transport
        self._timeout = timeout_seconds
        self._offset: int | None = None

    def poll_once(self) -> int:
        """Procesa un lote y confirma cada update por offset."""

        updates = self._transport.get_updates(self._offset, self._timeout)
        processed = 0
        for update in sorted(updates, key=lambda row: int(row["update_id"])):
            update_id = int(update["update_id"])
            if self._offset is not None and update_id < self._offset:
                continue
            self._bot.process_update(update)
            self._offset = update_id + 1
            processed += 1
        return processed

    def run_forever(self) -> None:
        """Mantiene long polling hasta interrupción del proceso."""

        LOGGER.info("Telegram bot iniciado en modo privado.")
        while True:
            try:
                self.poll_once()
            except TelegramTransportError:
                LOGGER.warning("Telegram no disponible; se reintentará.")
                time.sleep(2)


# Version: 2.0.0
# Created: 2026-07-29
