"""Adaptador Telegram privado para la API pre-match DIKAMAHA.

# Requirements:
# requests>=2.31
# tenacity>=8.2

Version: 2.5.0
Created: 2026-07-30
"""
from __future__ import annotations

import base64
import html
import logging
import os
import re
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

from src.telegram_billing import (
    BillingConfig,
    Entitlement,
    MiniappBillingClient,
    verify_billing_payload,
)

LOGGER = logging.getLogger(__name__)
TELEGRAM_API = "https://api.telegram.org"
MAX_MESSAGE_LENGTH = 3900


class TelegramTransportError(RuntimeError):
    """Indica un fallo sanitizado del transporte Telegram."""


class TelegramApiRejectedError(TelegramTransportError):
    """Indica que Telegram rechazó una petición válida a nivel HTTP/API."""


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

    def set_chat_menu_button(self, web_app_url: str) -> None:
        """Configura el acceso persistente a la Mini App cuando existe."""

        return None

    def answer_pre_checkout_query(
        self, query_id: str, ok: bool, error_message: str | None = None,
    ) -> None:
        """Acepta o rechaza un pago dentro de la ventana de 10 segundos."""

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

    def list_live(
        self, limit: int = 12, leagues: str | None = None,
    ) -> dict[str, Any]:
        """Lista partidos activos desde la API DIKAMAHA."""

        return {"fixtures": []}

    def high_probability(
        self, date: str | None = None, limit: int = 30,
        leagues: str | None = None,
    ) -> dict[str, Any]:
        """Consulta el menú de mayor probabilidad del día (Fase 122)."""

        return {"picks": [], "fixtures_scanned": 0, "fixtures_catalog_size": 0}

    def parlay_menu(
        self, date: str | None = None, limit: int = 30,
        leagues: str | None = None,
    ) -> dict[str, Any]:
        """Consulta las piernas elegibles del Constructor de Parlays (Fase 135)."""

        return {"status": "unavailable", "matches": [], "legs": 0}

    def predict_live_fixture(
        self, payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Ejecuta las capas live para un fixture activo."""

        raise PredictionGatewayError("live_prediction_not_implemented")

    def models(self) -> dict[str, Any]:
        """Lista modelos realmente operativos y su clasificación."""

        return {"status": "unavailable", "models": []}

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
    access_mode: str = "private"
    miniapp_url: str | None = None
    bot_username: str | None = None
    miniapp_short_name: str | None = None
    #: Cobro con Stars. Apagado reproduce exactamente el bot anterior a la
    #: Fase 125: nadie queda gateado y no se emite ninguna llamada interna.
    billing: BillingConfig = field(default_factory=BillingConfig)

    def __post_init__(self) -> None:
        """Valida límites sin inspeccionar o exponer secretos."""

        if not self.token:
            raise ValueError("telegram_bot_token_missing")
        if self.poll_timeout_seconds < 1 or self.request_timeout_seconds <= 0:
            raise ValueError("telegram_timeout_invalid")
        if self.rate_limit_requests < 1 or self.rate_limit_window_seconds < 1:
            raise ValueError("telegram_rate_limit_invalid")
        if self.access_mode not in {"private", "public"}:
            raise ValueError("telegram_access_mode_invalid")
        if self.miniapp_url and not self.miniapp_url.startswith("https://"):
            raise ValueError("telegram_miniapp_url_https_required")
        if self.bot_username and not re.fullmatch(
            r"[A-Za-z0-9_]{5,32}", self.bot_username.lstrip("@"),
        ):
            raise ValueError("telegram_bot_username_invalid")
        if self.miniapp_short_name and not re.fullmatch(
            r"[A-Za-z0-9_]{3,30}", self.miniapp_short_name,
        ):
            raise ValueError("telegram_miniapp_short_name_invalid")


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
        access_mode=os.getenv(
            "TELEGRAM_ACCESS_MODE", "private").strip().casefold(),
        miniapp_url=os.getenv("DIKAMAHA_MINIAPP_URL") or None,
        bot_username=os.getenv("TELEGRAM_BOT_USERNAME") or None,
        miniapp_short_name=os.getenv("TELEGRAM_MINIAPP_SHORT_NAME") or None,
        billing=BillingConfig(
            enabled=os.getenv(
                "TELEGRAM_BILLING_ENABLED", "false").strip().casefold() == "true",
            billing_secret=os.getenv("MINIAPP_BILLING_SECRET") or None,
            miniapp_internal_url=os.getenv("MINIAPP_INTERNAL_URL") or None,
            miniapp_internal_key=os.getenv("MINIAPP_INTERNAL_API_KEY") or None,
        ),
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
            # `pre_checkout_query` es obligatorio para cobrar: si no se pide
            # explícitamente, Telegram no lo entrega y **todo pago queda sin
            # confirmar**. `successful_payment` y `refunded_payment` viajan
            # dentro de `message`, así que no necesitan entrada propia.
            "timeout": timeout_seconds,
            "allowed_updates": ["message", "callback_query", "pre_checkout_query"],
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
        try:
            self._post(
                "sendMessage", payload,
                int(self._config.request_timeout_seconds))
        except TelegramApiRejectedError:
            LOGGER.warning(
                "Telegram rechazó HTML; se reintentará texto plano chat_id=%s",
                chat_id)
            fallback = {
                "chat_id": chat_id, "text": _plain_telegram_text(text),
            }
            self._post(
                "sendMessage", fallback,
                int(self._config.request_timeout_seconds))

    def answer_callback_query(self, callback_id: str) -> None:
        """Confirma callback sin mostrar el identificador sensible."""

        self._post("answerCallbackQuery", {"callback_query_id": callback_id}, 10)

    def set_chat_menu_button(self, web_app_url: str) -> None:
        """Abre el dashboard desde el botón de menú global del bot."""

        self._post("setChatMenuButton", {
            "menu_button": {
                "type": "web_app", "text": "Abrir DIKAMAHA",
                "web_app": {"url": web_app_url},
            },
        }, 10)

    def answer_pre_checkout_query(
        self, query_id: str, ok: bool, error_message: str | None = None,
    ) -> None:
        """Responde a Telegram dentro de la ventana de 10 segundos.

        Timeout corto y deliberado: pasados los 10 segundos Telegram cancela el
        pago, así que colgarse aquí es peor que fallar rápido.
        """

        payload: dict[str, Any] = {"pre_checkout_query_id": query_id, "ok": ok}
        if not ok and error_message:
            payload["error_message"] = error_message
        self._post("answerPreCheckoutQuery", payload, 5)

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
            LOGGER.warning(
                "Telegram API rechazó method=%s status=%s description=%s",
                method, response.status_code, data.get("description", "unknown"))
            raise TelegramApiRejectedError("telegram_api_rejected_request")
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

    def list_live(
        self, limit: int = 12, leagues: str | None = None,
    ) -> dict[str, Any]:
        """Obtiene el catálogo actual de fixtures en vivo."""

        params: dict[str, Any] = {"limit": max(1, min(limit, 20))}
        if leagues:
            params["leagues"] = leagues
        return self._get("/v1/live", params, timeout_multiplier=3.0)

    def predict_live_fixture(
        self, payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Solicita el motor probabilístico live oficial y sus componentes."""

        return self._post(
            "/v1/predict/live/fixture", payload, timeout_multiplier=3.0)

    def high_probability(
        self, date: str | None = None, limit: int = 30,
        leagues: str | None = None,
    ) -> dict[str, Any]:
        """Obtiene los picks vigentes del menú de mayor probabilidad."""

        params: dict[str, Any] = {"limit": max(1, min(limit, 30))}
        if date:
            params["date"] = date
        if leagues:
            params["leagues"] = leagues
        return self._get(
            "/v1/high-probability", params, timeout_multiplier=3.0)

    def parlay_menu(
        self, date: str | None = None, limit: int = 30,
        leagues: str | None = None,
    ) -> dict[str, Any]:
        """Obtiene las piernas del día que superan el gate de Fase 135.

        Mismo multiplicador de tiempo que `high_probability`: el costo dominante
        es idéntico -una inferencia completa por fixture- y el barrido comparte
        catálogo, concurrencia y presupuesto con aquel.
        """

        params: dict[str, Any] = {"limit": max(1, min(limit, 50))}
        if date:
            params["date"] = date
        if leagues:
            params["leagues"] = leagues
        return self._get("/v1/parlay/menu", params, timeout_multiplier=3.0)

    def models(self) -> dict[str, Any]:
        """Obtiene el inventario operativo de la API."""

        return self._get("/v1/models")

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

    def _post(
        self, path: str, payload: dict[str, Any],
        timeout_multiplier: float = 1.0,
    ) -> dict[str, Any]:
        """Ejecuta POST y normaliza errores."""

        try:
            response = self._session.post(
                self._config.dikamaha_base_url.rstrip("/") + path,
                json=payload, headers=self._headers(),
                timeout=self._config.request_timeout_seconds * timeout_multiplier)
            return _gateway_payload(response)
        except requests.RequestException as error:
            raise PredictionGatewayError(
                "dikamaha_service_unavailable") from error

    def _get(
        self, path: str, params: dict[str, Any] | None = None,
        timeout_multiplier: float = 1.0,
    ) -> dict[str, Any]:
        """Ejecuta GET y normaliza errores."""

        try:
            response = self._session.get(
                self._config.dikamaha_base_url.rstrip("/") + path,
                params=params, headers=self._headers(),
                timeout=self._config.request_timeout_seconds * timeout_multiplier)
            return _gateway_payload(response)
        except requests.RequestException as error:
            raise PredictionGatewayError(
                "dikamaha_service_unavailable") from error


def _gateway_payload(response: requests.Response) -> dict[str, Any]:
    """Valida un payload DIKAMAHA sin devolver cuerpos en errores."""

    try:
        payload = response.json()
    except ValueError as error:
        raise PredictionGatewayError(
            f"dikamaha_invalid_response:{response.status_code}") from error
    if response.status_code >= 400 or not isinstance(payload, dict):
        detail = (
            str(payload.get("detail")) if isinstance(payload, dict) else None)
        raise PredictionGatewayError(
            f"dikamaha_prediction_rejected:{response.status_code}:{detail}")
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
        """Inicializa dependencias y límites.

        `gateway` se conserva en la firma -y en desuso dentro de esta clase-
        sólo para no romper `scripts/run_phase_97_telegram_bot.py` y el resto
        de constructores existentes: la Fase 125 retiró del bot toda
        exploración y predicción manual -viven exclusivamente en la Mini
        App-, así que ninguna ruta que sobrevive vuelve a llamar a
        `self._gateway`.
        """

        self._config = config
        self._transport = transport
        self._gateway = gateway
        self._rate = UserRateLimiter(
            config.rate_limit_requests, config.rate_limit_window_seconds)
        self._billing = (
            MiniappBillingClient(
                config.billing.miniapp_internal_url or "",
                config.billing.miniapp_internal_key or "")
            if config.billing.enabled else None)

    def _entitlement(self, user_id: int) -> Entitlement:
        """Resuelve el nivel del usuario, o premium si el cobro está apagado."""

        if self._billing is None:
            return Entitlement(plan="premium")
        return self._billing.entitlement_for(user_id)

    def _process_pre_checkout(self, query: dict[str, Any]) -> None:
        """Acepta o rechaza un pago con verificación puramente local.

        Telegram concede 10 segundos y cancela el pago si se agotan. Una
        consulta a la Mini App dentro de esa ventana es una moneda al aire
        durante un arranque en frío o un despliegue; comprobar el HMAC en local
        es determinista y cuesta microsegundos.

        No pasa por `_is_authorized` ni por el rate limiter a propósito: esto es
        Telegram preguntando sí o no por un dinero que el usuario ya
        comprometió, no un comando.
        """

        query_id = str(query.get("id", ""))
        sender = query.get("from")
        user_id = int(sender.get("id", 0)) if isinstance(sender, dict) else 0
        payload = verify_billing_payload(
            str(query.get("invoice_payload", "")),
            self._config.billing.billing_secret,
            expected_user_id=user_id or None,
            max_age_seconds=3600,
        )
        self._transport.answer_pre_checkout_query(
            query_id, payload is not None,
            None if payload else
            "La factura caducó. Vuelve a abrir Premium en DIKAMAHA.")

    def _process_successful_payment(
        self, user_id: int, chat_id: int, message: dict[str, Any],
    ) -> None:
        """Reenvía el cobro a quien escribe en la base y avisa al usuario."""

        payment = message.get("successful_payment", {})
        body = {
            "user_id": user_id,
            "telegram_payment_charge_id": payment.get("telegram_payment_charge_id"),
            "invoice_payload": payment.get("invoice_payload"),
            "total_amount": payment.get("total_amount"),
            "currency": payment.get("currency"),
            "is_recurring": bool(payment.get("is_recurring")),
            "is_first_recurring": bool(payment.get("is_first_recurring")),
            "subscription_expiration_date": payment.get("subscription_expiration_date"),
        }
        delivered = bool(self._billing and self._billing.forward_payment(body))
        if delivered:
            self._billing.invalidate(user_id)  # type: ignore[union-attr]
            _send(self._transport, chat_id, _premium_active_text(), _main_keyboard())
            return
        # El pago existe en Telegram aunque no haya llegado a la base. Decirle
        # que falló sería falso, y decirle que ya está activo sería prematuro:
        # la reconciliación por `getStarTransactions` lo recogerá en minutos.
        LOGGER.error("star_payment_forward_failed user_id=%s", user_id)
        _send(self._transport, chat_id, _premium_pending_text(), _main_keyboard())

    def _process_refunded_payment(
        self, user_id: int, message: dict[str, Any],
    ) -> None:
        """Reenvía un reembolso, venga de donde venga.

        Telegram entrega este update también cuando el reembolso se inició
        desde su propio panel. Sin manejarlo, alguien a quien se le devolvió el
        dinero conservaría premium hasta el fin del periodo.
        """

        refund = message.get("refunded_payment", {})
        if self._billing is None:
            return
        self._billing.forward_refund({
            "user_id": user_id,
            "telegram_payment_charge_id": refund.get("telegram_payment_charge_id"),
            "total_amount": refund.get("total_amount", 0),
        })
        self._billing.invalidate(user_id)

    def process_update(self, update: dict[str, Any]) -> None:
        """Procesa mensajes y pagos en chats privados."""

        pre_checkout = update.get("pre_checkout_query")
        if isinstance(pre_checkout, dict):
            self._process_pre_checkout(pre_checkout)
            return

        # El bot ya no genera ningún botón con `callback_data` -sólo el botón
        # `web_app` de abrir la Mini App, que Telegram resuelve sin avisarnos-,
        # así que una pulsación entrante sólo puede venir de un mensaje viejo
        # de antes de este cambio. Confirmarla apaga el reloj de arena; no hay
        # a qué enrutarla.
        callback = update.get("callback_query")
        if isinstance(callback, dict):
            callback_id = str(callback.get("id", ""))
            if callback_id:
                self._transport.answer_callback_query(callback_id)
            return

        message = update.get("message")
        if not isinstance(message, dict):
            return
        chat, sender = message.get("chat", {}), message.get("from", {})
        if chat.get("type") != "private" or not isinstance(sender, dict):
            return

        # Los mensajes de servicio de pago llegan **sin campo `text`**, así que
        # tienen que ramificar antes de la guarda de abajo: si se dejaran caer
        # hasta ella, cada cobro se descartaría en silencio y el suscriptor
        # pagaría sin recibir nada.
        if isinstance(message.get("successful_payment"), dict):
            self._process_successful_payment(
                int(sender["id"]), int(chat["id"]), message)
            return
        if isinstance(message.get("refunded_payment"), dict):
            self._process_refunded_payment(int(sender["id"]), message)
            return

        text = message.get("text")
        if not isinstance(text, str):
            return
        chat_id, user_id = int(chat["id"]), int(sender["id"])
        replies = self._reply(user_id, text.strip())
        for part, keyboard in replies:
            _send(self._transport, chat_id, part, keyboard)

    def _reply(self, user_id: int, text: str) -> list[tuple[str, dict[str, Any] | None]]:
        """Autoriza, limita y enruta un comando.

        El bot quedó reducido a cuenta y cobro: toda la exploración de datos y
        las predicciones manuales viven exclusivamente en la Mini App (Fase
        125), así que ya no hay estado de menú ni búsqueda de equipo que
        mantener entre mensajes.
        """

        command = text.split(maxsplit=1)[0].split("@", 1)[0].lower()
        if command == "/whoami":
            return [(f"Tu ID de Telegram es <code>{user_id}</code>.", None)]
        if command == "/start":
            return [(_welcome_text(), _main_keyboard())]
        if command == "/help":
            return [(_help_text(), _main_keyboard())]
        if not self._is_authorized(user_id):
            return [(_unauthorized_text(), None)]
        if not self._rate.allow(user_id, time.monotonic()):
            return [(_rate_limit_text(), None)]
        return self._authorized_reply(user_id, command)

    def _is_authorized(self, user_id: int) -> bool:
        """Autoriza por modo público o por membresía privada explícita."""

        return (
            self._config.access_mode == "public"
            or user_id in self._config.allowed_user_ids)

    def _authorized_reply(
        self, user_id: int, command: str,
    ) -> list[tuple[str, dict[str, Any] | None]]:
        """Ejecuta comandos de cuenta y cobro."""

        if command == "/premium":
            return self._premium_offer(user_id)
        if command == "/mi_plan":
            return self._plan_status(user_id)
        return [("Comando no reconocido.\n\n" + _help_text(), _main_keyboard())]

    def _premium_keyboard(self, user_id: int) -> dict[str, Any] | None:
        """Teclado con el enlace de pago, si se puede emitir."""

        link = self._billing.create_invoice(user_id) if self._billing else None
        if not link:
            return _main_keyboard()
        return {"inline_keyboard": [[{"text": "⭐ Activar Premium", "url": link}]]}

    def _premium_offer(
        self, user_id: int,
    ) -> list[tuple[str, dict[str, Any] | None]]:
        """Responde al comando `/premium` con el enlace de pago."""

        if self._billing is None:
            return [("El nivel Premium no está activo todavía.", _main_keyboard())]
        entitlement = self._entitlement(user_id)
        if entitlement.premium and not entitlement.degraded:
            return self._plan_status(user_id)
        return [(_premium_upsell_text(), self._premium_keyboard(user_id))]

    def _plan_status(
        self, user_id: int,
    ) -> list[tuple[str, dict[str, Any] | None]]:
        """Responde al comando `/mi_plan`."""

        entitlement = self._entitlement(user_id)
        if entitlement.degraded:
            return [(_billing_degraded_text(), _main_keyboard())]
        if entitlement.premium:
            until = (
                f"\nActivo hasta el <b>{entitlement.expires_at[:10]}</b>."
                if entitlement.expires_at else "")
            # "DIKAMAHA PREMIUM" a secas es el nombre del bot, no el nivel de
            # pago: con eso en la cabecera de bienvenida, decir aquí lo mismo
            # dejaba a un usuario gratuito sin forma de distinguir la marca de
            # su plan real. "Tu plan" lo ata explícitamente al estado, no al
            # producto.
            return [(
                "⭐ <b>Tu plan: Premium</b>\n"
                "Predicciones sin límite, en vivo y mayor probabilidad."
                f"{until}",
                _main_keyboard(),
            )]
        remaining = entitlement.remaining_predictions
        left = "sin límite" if remaining is None else f"{remaining}"
        return [(
            "🆓 <b>Tu plan: Gratuito</b>\n"
            f"Predicciones disponibles hoy: <b>{left}</b>\n"
            "Incluye aciertos del día, catálogo, equipos e historial.",
            self._premium_keyboard(user_id),
        )]


def _send(transport: TelegramTransport, chat_id: int, text: str, keyboard: dict[str, Any] | None) -> None:
    """Envía con teclado cuando el transporte lo admite."""

    if keyboard is None:
        transport.send_message(chat_id, text)
        return
    try:
        transport.send_message(chat_id, text, keyboard)
    except TypeError:
        transport.send_message(chat_id, text)


def _main_keyboard() -> dict[str, Any] | None:
    """Devuelve el único botón que el bot necesita.

    Hasta la Fase 125 esto era una rejilla de botones `callback_data` que
    abrían menús de exploración dentro del propio chat. Esos menús se
    retiraron: catálogo, predicciones, en vivo, mercados, jugadores y
    estadísticas viven exclusivamente en la Mini App. Lo único que un botón
    puede seguir haciendo aquí es abrirla -un botón `web_app`, que Telegram
    resuelve sin avisar al bot, así que no exige ningún manejador-.
    """

    miniapp_url = os.getenv("DIKAMAHA_MINIAPP_URL", "").strip()
    if not miniapp_url.startswith("https://"):
        return None
    return {"inline_keyboard": [[{
        "text": "📊 Abrir DIKAMAHA",
        "web_app": {"url": miniapp_url},
    }]]}


def _fixture_miniapp_link(
    config: TelegramBotConfig, fixture: dict[str, Any], prefix: str,
) -> str | None:
    """Crea un enlace ``startapp`` compacto sin introducir estado externo."""

    if not config.bot_username or not config.miniapp_short_name:
        return None
    match_id = str(fixture.get("match_id", "")).strip()
    league = str(fixture.get("league_slug", "")).strip()
    if not match_id.isdigit() or not league:
        return None
    encoded = base64.urlsafe_b64encode(
        league.encode("utf-8"),
    ).decode("ascii").rstrip("=")
    parameter = f"{prefix}_{match_id}_{encoded}"
    if prefix == "prediction":
        home = str(fixture.get("home_team_id", "")).strip()
        away = str(fixture.get("away_team_id", "")).strip()
        try:
            kickoff = int(datetime.fromisoformat(
                str(fixture.get("kickoff_ts", "")).replace(
                    "Z", "+00:00"),
            ).timestamp())
        except ValueError:
            return None
        if not home.isdigit() or not away.isdigit():
            return None
        parameter = f"{parameter}_{home}_{away}_{kickoff}"
    if len(parameter) > 64:
        return None
    username = config.bot_username.lstrip("@")
    return (
        f"https://t.me/{username}/{config.miniapp_short_name}"
        f"?startapp={parameter}"
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


def _plain_telegram_text(text: str) -> str:
    """Convierte HTML Telegram en texto seguro para un reintento."""

    without_tags = re.sub(r"<[^>]*>", "", text)
    return html.unescape(without_tags)


def _help_text() -> str:
    """Devuelve ayuda completa y reutilizable.

    Fase 125 retiró del bot toda exploración y predicción manual -catálogo,
    partidos, mercados, jugadores, estadísticas y en vivo pasaron por entero a
    la Mini App-, así que el bot quedó reducido a cuenta y cobro. Este texto
    ya no describe comandos ni navegación por botones que no existen.
    """

    return (
        "💎 <b>DIKAMAHA PREMIUM</b>\n"
        "<i>Análisis pre-match y en vivo</i>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📋 <b>COMANDOS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🏠 <b>/start</b> | <b>/help</b>\n"
        "   Muestra este mensaje.\n\n"
        "🆔 <b>/whoami</b>\n"
        "   Muestra tu ID de Telegram para solicitar acceso.\n\n"
        "⭐ <b>/premium</b>\n"
        "   Activa el plan de pago o muestra el enlace para hacerlo.\n\n"
        "📋 <b>/mi_plan</b>\n"
        "   Consulta tu plan actual y, si eres gratuito, cuántas\n"
        "   predicciones te quedan hoy.\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📊 <b>TODO LO DEMÁS, EN LA MINI APP</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Catálogo de partidos, predicciones pre-match y en vivo, mercados,\n"
        "play-by-play, estadísticas, plantillas, jugadores, favoritos,\n"
        "alertas e historial de aciertos se abren desde el botón de abajo,\n"
        "no por comandos ni menús de este chat.\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⭐ <b>PLANES</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🆓 <b>Gratuito</b>: aciertos del día, catálogo, equipos,\n"
        "   estadísticas, historial y 3 predicciones diarias.\n\n"
        "⭐ <b>Premium</b>: predicciones sin límite, en vivo, mayor\n"
        "   probabilidad, constructor de picks y sin topes de\n"
        "   favoritos ni alertas.\n\n"
        "   <b>/premium</b> para activarlo · <b>/mi_plan</b> para consultarlo\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "ℹ️ <b>Sobre las predicciones</b>\n"
        "Pre-match usa datos anteriores al inicio. Live usa snapshots ESPN y\n"
        "un prior causal reconstruido sólo con historia anterior al kickoff.\n"
        "El motor live es oficial. ESPN Predictor y Pickcenter son\n"
        "benchmarks externos separados.\n\n"
        "<i>La información es analítica y no constituye una apuesta.</i>")


def _welcome_text() -> str:
    """Presenta el inicio del bot sin saturar la primera pantalla."""

    return (
        "💎 <b>DIKAMAHA PREMIUM</b>\n"
        "<i>Predicciones y análisis pre-match + en vivo</i>\n\n"
        "Bienvenido. Toda la exploración -partidos, predicciones, en vivo,\n"
        "mercados y estadísticas- vive en la Mini App: ábrela con el botón\n"
        "de abajo.\n\n"
        "Aquí en el chat sólo gestionas tu cuenta: /mi_plan para ver tu\n"
        "plan, /premium para activarlo. Escribe /help para más detalle.")


def _unauthorized_text() -> str:
    """Informa cómo solicitar acceso sin revelar configuración."""

    return (
        "🔒 <b>ACCESO PREMIUM REQUERIDO</b>\n"
        "Tu usuario no tiene una membresía activa.\n"
        "Usa /whoami y envía tu identificador al administrador.")


def _rate_limit_text() -> str:
    """Informa el límite sin revelar la configuración interna."""

    return "Demasiadas solicitudes. Intenta de nuevo en un minuto."


def _premium_upsell_text() -> str:
    """Presenta el nivel de pago por acceso y volumen.

    Nunca por rentabilidad, retorno ni aciertos garantizados: el proyecto tiene
    congelados ROI, Kelly y stakes, y la superficie de venta es exactamente
    donde más tienta saltarse esa restricción.
    """

    return (
        "⭐ <b>DIKAMAHA PREMIUM</b>\n"
        "<i>Esta función forma parte del nivel de pago</i>\n\n"
        "Con Premium tienes:\n"
        "• Predicciones pre-match sin límite\n"
        "• Análisis en vivo\n"
        "• Menú de mayor probabilidad completo\n"
        "• Constructor de picks con probabilidad conjunta\n"
        "• Favoritos y alertas sin tope\n\n"
        "El plan gratuito mantiene los aciertos del día, el catálogo, "
        "los equipos, las estadísticas y 3 predicciones diarias.\n\n"
        "<i>DIKAMAHA publica análisis estadístico. No es asesoramiento "
        "financiero ni una recomendación de apuesta.</i>")



def _billing_degraded_text() -> str:
    """Explica una indisponibilidad temporal sin acusar de no haber pagado."""

    return (
        "⏳ No puedo comprobar tu plan en este momento.\n"
        "Vuelve a intentarlo en unos segundos; tu suscripción no se ve "
        "afectada.")


def _premium_active_text() -> str:
    """Confirma un cobro ya asentado."""

    return (
        "⭐ <b>Premium activo</b>\n"
        "Ya tienes predicciones sin límite, análisis en vivo y el menú de "
        "mayor probabilidad.\n\n"
        "Puedes gestionar o cancelar la renovación desde los ajustes de "
        "Telegram, en Estrellas → Suscripciones.")


def _premium_pending_text() -> str:
    """Reconoce un cobro cuyo asiento aún no se ha confirmado.

    Ni "falló" -sería falso, el dinero salió- ni "ya está activo" -sería
    prematuro-. La reconciliación lo recogerá en minutos.
    """

    return (
        "✅ <b>Pago recibido</b>\n"
        "Estamos activando tu Premium; puede tardar unos minutos.\n"
        "Si en 15 minutos sigue sin aparecer, escribe /mi_plan.")



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
        """Procesa un lote y confirma cada update por offset.

        El offset avanza **siempre**, procese o no con éxito. Antes sólo
        avanzaba tras un `process_update` exitoso: un update que hiciera
        fallar a un handler -un botón de menú expirado, un callback
        malformado, cualquier bug de un handler concreto- dejaba el offset
        clavado en ese mismo update para siempre. Telegram lo reenvía en
        cada `getUpdates` mientras no se confirme, así que el bot quedaba
        atascado repitiendo indefinidamente ese único update "veneno" y
        dejaba de procesar cualquier mensaje nuevo de cualquier usuario,
        incluso tras un reinicio -el offset vive sólo en memoria, así que el
        próximo arranque volvía a pedir el mismo update desde cero-. La
        semántica de `offset` en la API de Telegram es de confirmación, no
        de éxito: una vez visto, se descarta, se procese bien o mal.
        """

        updates = self._transport.get_updates(self._offset, self._timeout)
        processed = 0
        for update in sorted(updates, key=lambda row: int(row["update_id"])):
            update_id = int(update["update_id"])
            if self._offset is not None and update_id < self._offset:
                continue
            try:
                self._bot.process_update(update)
                processed += 1
            except Exception:  # noqa: BLE001 - un update roto no debe bloquear a los demás
                LOGGER.exception(
                    "telegram_update_processing_failed update_id=%s", update_id)
            finally:
                self._offset = update_id + 1
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


# Version: 2.4.0
# Created: 2026-07-30
