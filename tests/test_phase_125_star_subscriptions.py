"""Pruebas del cobro con Telegram Stars en el bot (Fase 125).

Dos bloqueadores duros vivían en el bot antes de esta fase y ambos son
silenciosos: `pre_checkout_query` no estaba en `allowed_updates`, así que
Telegram nunca lo entregaba; y `process_update` descartaba todo mensaje sin
campo `text`, que es exactamente la forma de un `successful_payment`. Las dos
primeras pruebas existen para que ninguna refactorización los reintroduzca.
"""
from __future__ import annotations

from typing import Any

import pytest

from src.telegram_billing import (
    BillingConfig,
    Entitlement,
    MiniappBillingClient,
    verify_billing_payload,
)
from src.telegram_bot import (
    TelegramBotConfig,
    TelegramHttpTransport,
    TelegramPredictionBot,
    TelegramTransport,
)

pytestmark = pytest.mark.unit

SECRET = "0123456789abcdef0123456789abcdef"


def _sign(user_id: int, stars: int = 250, issued_at: int | None = None) -> str:
    """Firma un payload igual que `lib/billing/payload.ts`.

    Por defecto se emite ahora: el verificador rechaza tanto lo caducado como
    lo fechado en el futuro, así que una constante quemada envejecería mal en
    los dos sentidos.
    """

    import base64
    import hashlib
    import hmac
    import time

    if issued_at is None:
        issued_at = int(time.time())

    body = f"v1.{user_id}.premium_monthly.{stars}.{issued_at}.nonce123"
    digest = hmac.new(
        SECRET.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).digest()
    signature = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return f"{body}.{signature}"


class FakeSession:
    """Sesión HTTP determinista que registra cada llamada."""

    def __init__(self, responses: list[Any] | None = None) -> None:
        """Inicializa cola de respuestas y registro de peticiones."""

        self.requests: list[dict[str, Any]] = []
        self.responses = responses or []

    def request(self, method: str, url: str, **kwargs: Any) -> Any:
        """Registra y devuelve la siguiente respuesta preparada."""

        self.requests.append({"method": method, "url": url, **kwargs})
        if not self.responses:
            return _Response(200, {})
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def post(self, url: str, **kwargs: Any) -> Any:
        """Compatibilidad con el transporte Telegram."""

        return self.request("POST", url, **kwargs)


class _Response:
    """Respuesta mínima compatible con `requests`."""

    def __init__(self, status_code: int, payload: Any) -> None:
        """Guarda estado y cuerpo."""

        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        """Devuelve el cuerpo preparado."""

        return self._payload

    def raise_for_status(self) -> None:
        """No lanza: el cliente decide por `status_code`."""

        return None


class RecordingTransport(TelegramTransport):
    """Transporte que registra las respuestas de pre-checkout."""

    def __init__(self) -> None:
        """Inicializa registros vacíos."""

        self.sent: list[tuple[int, str]] = []
        self.pre_checkout: list[tuple[str, bool, str | None]] = []

    def get_updates(
        self, offset: int | None, timeout_seconds: int,
    ) -> list[dict[str, Any]]:
        """No se usa en estas pruebas."""

        return []

    def send_message(
        self, chat_id: int, text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> None:
        """Conserva el mensaje enviado."""

        self.sent.append((chat_id, text))

    def answer_pre_checkout_query(
        self, query_id: str, ok: bool, error_message: str | None = None,
    ) -> None:
        """Conserva el veredicto del pre-checkout."""

        self.pre_checkout.append((query_id, ok, error_message))


class StubGateway:
    """Gateway que nunca se invoca en estas pruebas."""

    def predict_fixture(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Devuelve una predicción vacía."""

        return {}

    def predict_upcoming(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Devuelve una predicción vacía."""

        return {}


def _bot(
    transport: TelegramTransport, *, billing: bool = True,
    session: FakeSession | None = None,
) -> TelegramPredictionBot:
    """Construye un bot con cobro activo y cliente interno falso."""

    config = TelegramBotConfig(
        "secret", frozenset({7}), access_mode="public",
        billing=BillingConfig(
            enabled=billing,
            billing_secret=SECRET,
            miniapp_internal_url="https://miniapp.test",
            miniapp_internal_key="internal-key",
        ),
    )
    bot = TelegramPredictionBot(config, transport, StubGateway())  # type: ignore[arg-type]
    if billing and session is not None:
        bot._billing = MiniappBillingClient(  # noqa: SLF001
            "https://miniapp.test", "internal-key", session=session)  # type: ignore[arg-type]
    return bot


# --------------------------------------------------------------------------
# Los dos bloqueadores duros
# --------------------------------------------------------------------------

def test_allowed_updates_includes_pre_checkout_query() -> None:
    """Sin esta entrada Telegram nunca entrega el pre-checkout."""

    session = FakeSession([_Response(200, {"ok": True, "result": []})])
    config = TelegramBotConfig("secret", frozenset({7}))

    TelegramHttpTransport(config, session).get_updates(None, 25)  # type: ignore[arg-type]

    payload = session.requests[0]["json"]
    assert "pre_checkout_query" in payload["allowed_updates"]
    # `successful_payment` y `refunded_payment` viajan dentro de `message`.
    assert "message" in payload["allowed_updates"]


def test_successful_payment_without_text_is_not_dropped() -> None:
    """Un mensaje de servicio de pago no trae `text` y debe ramificar antes."""

    transport = RecordingTransport()
    session = FakeSession([_Response(200, {"applied": True})])
    bot = _bot(transport, session=session)

    bot.process_update({"message": {
        "chat": {"id": 70, "type": "private"},
        "from": {"id": 7},
        "successful_payment": {
            "telegram_payment_charge_id": "charge_1",
            "invoice_payload": _sign(7),
            "total_amount": 250,
            "currency": "XTR",
        },
    }})

    assert session.requests, "el pago no llegó al endpoint interno"
    assert session.requests[0]["url"].endswith("/api/internal/billing/payment")
    assert transport.sent, "no se confirmó nada al usuario"


# --------------------------------------------------------------------------
# Pre-checkout: la ventana de 10 segundos
# --------------------------------------------------------------------------

def test_pre_checkout_accepts_valid_payload_without_network() -> None:
    """Verificar en local es lo que garantiza responder dentro de 10 s."""

    transport = RecordingTransport()
    session = FakeSession()
    bot = _bot(transport, session=session)

    bot.process_update({"pre_checkout_query": {
        "id": "q1", "from": {"id": 7}, "invoice_payload": _sign(7),
    }})

    assert transport.pre_checkout == [("q1", True, None)]
    # Cero llamadas de red: un viaje a la Mini App dentro de esa ventana es una
    # moneda al aire durante un arranque en frío.
    assert session.requests == []


def test_pre_checkout_rejects_tampered_payload() -> None:
    """Una factura con el id cambiado no puede activar otra cuenta."""

    transport = RecordingTransport()
    bot = _bot(transport, session=FakeSession())

    bot.process_update({"pre_checkout_query": {
        "id": "q2", "from": {"id": 99}, "invoice_payload": _sign(7),
    }})

    query_id, ok, message = transport.pre_checkout[0]
    assert (query_id, ok) == ("q2", False)
    assert message


def test_pre_checkout_rejects_expired_invoice() -> None:
    """Una factura de hace días es basura o un intento de repetición."""

    transport = RecordingTransport()
    bot = _bot(transport, session=FakeSession())

    bot.process_update({"pre_checkout_query": {
        "id": "q3", "from": {"id": 7},
        "invoice_payload": _sign(7, issued_at=1_000_000_000),
    }})

    assert transport.pre_checkout[0][1] is False


# --------------------------------------------------------------------------
# Payload: paridad exacta con la implementación TypeScript
# --------------------------------------------------------------------------

def test_payload_signature_matches_typescript_format() -> None:
    """Si las dos implementaciones divergen, todo pre-checkout se rechaza."""

    parsed = verify_billing_payload(_sign(42), SECRET)

    assert parsed is not None
    assert parsed.user_id == 42
    assert parsed.stars_amount == 250
    assert parsed.plan_code == "premium_monthly"


def test_payload_age_ignored_when_not_requested() -> None:
    """Una renovación a los seis meses lleva el payload original."""

    old = _sign(42, issued_at=1_000_000_000)

    assert verify_billing_payload(old, SECRET, max_age_seconds=3600) is None
    # Sin `max_age_seconds` se acepta: exigir frescura al asentar el pago
    # rechazaría precisamente al suscriptor más fiel.
    assert verify_billing_payload(old, SECRET) is not None


def test_payload_rejects_wrong_secret_and_malformed_input() -> None:
    """Rechazos silenciosos, sin excepciones que escapen."""

    assert verify_billing_payload(_sign(42), "otro-secreto-cualquiera") is None
    assert verify_billing_payload("v1.42.x", SECRET) is None
    assert verify_billing_payload(None, SECRET) is None
    assert verify_billing_payload(_sign(42), None) is None


# --------------------------------------------------------------------------
# Degradación: nunca cerrar el acceso por un fallo propio
# --------------------------------------------------------------------------

def test_entitlement_fails_open_to_free_and_is_not_cached() -> None:
    """Un fallo transitorio no puede fijar 60 s de castigo a quien pagó."""

    import requests

    session = FakeSession([
        requests.RequestException("timeout"),
        _Response(200, {"plan": "premium", "remaining_predictions": None}),
    ])
    client = MiniappBillingClient("https://miniapp.test", "key", session=session)  # type: ignore[arg-type]

    degraded = client.entitlement_for(7, now=100.0)
    assert degraded.plan == "free"
    assert degraded.degraded is True

    # Segunda llamada dentro del TTL: como la degradación no se cachea, vuelve
    # a consultar y recupera el plan real.
    recovered = client.entitlement_for(7, now=101.0)
    assert recovered.plan == "premium"
    assert recovered.degraded is False


def test_entitlement_caches_a_successful_read() -> None:
    """Dentro del TTL no se repite la consulta."""

    session = FakeSession([
        _Response(200, {"plan": "premium", "remaining_predictions": None}),
    ])
    client = MiniappBillingClient("https://miniapp.test", "key", session=session)  # type: ignore[arg-type]

    client.entitlement_for(7, now=100.0)
    client.entitlement_for(7, now=120.0)

    assert len(session.requests) == 1

    # Pasado el TTL vuelve a preguntar.
    session.responses.append(
        _Response(200, {"plan": "free", "remaining_predictions": 3}))
    assert client.entitlement_for(7, now=200.0).plan == "free"


def test_entitlement_rejects_error_status_as_degraded() -> None:
    """Un 500 de la Mini App degrada igual que un timeout."""

    session = FakeSession([_Response(500, {})])
    client = MiniappBillingClient("https://miniapp.test", "key", session=session)  # type: ignore[arg-type]

    assert client.entitlement_for(7, now=1.0).degraded is True


def test_degraded_reply_never_accuses_the_user_of_not_paying() -> None:
    """Un fallo nuestro no puede decirle a un suscriptor que no ha pagado."""

    import requests

    transport = RecordingTransport()
    session = FakeSession([requests.RequestException("down")] * 6)
    bot = _bot(transport, session=session)

    replies = bot._require_premium(7)  # noqa: SLF001

    assert replies is not None
    text = replies[0][0]
    assert "Premium" not in text or "no puedo comprobar" in text.casefold()
    assert "temporalmente" in text.casefold() or "unos segundos" in text.casefold()


# --------------------------------------------------------------------------
# Puertas por nivel
# --------------------------------------------------------------------------

def test_free_tier_blocks_live_and_allows_the_free_surface() -> None:
    """El plan gratuito conserva historial, catálogo y estado."""

    transport = RecordingTransport()
    session = FakeSession([
        _Response(200, {"plan": "free", "remaining_predictions": 3}),
        _Response(200, {"link": "https://t.me/invoice"}),
    ])
    bot = _bot(transport, session=session)

    blocked = bot._require_premium(7)  # noqa: SLF001

    assert blocked is not None
    assert "PREMIUM" in blocked[0][0]


def test_premium_passes_the_gate() -> None:
    """Un suscriptor no ve ningún muro."""

    transport = RecordingTransport()
    session = FakeSession([
        _Response(200, {"plan": "premium", "remaining_predictions": None}),
    ])
    bot = _bot(transport, session=session)

    assert bot._require_premium(7) is None  # noqa: SLF001


def test_quota_gate_blocks_when_exhausted() -> None:
    """Agotado el cupo se ofrece Premium, no un error de servicio."""

    transport = RecordingTransport()
    session = FakeSession([
        _Response(200, {"plan": "free", "remaining_predictions": 0}),
        _Response(200, {"granted": False, "reason": "exhausted", "remaining": 0}),
        _Response(200, {"link": "https://t.me/invoice"}),
    ])
    bot = _bot(transport, session=session)

    replies = bot._quota_gate(7, "esp.1", 12345)  # noqa: SLF001

    assert replies is not None
    assert "3 predicciones" in replies[0][0]


def test_quota_gate_serves_when_accounting_is_down() -> None:
    """Negarse a predecir porque el contador no responde castiga al usuario."""

    import requests

    transport = RecordingTransport()
    session = FakeSession([
        _Response(200, {"plan": "free", "remaining_predictions": 3}),
        requests.RequestException("down"),
    ])
    bot = _bot(transport, session=session)

    assert bot._quota_gate(7, "esp.1", 12345) is None  # noqa: SLF001


def test_quota_key_matches_the_shared_fixture_key() -> None:
    """Bot, Mini App y tarjeta compartida gastan el mismo presupuesto."""

    bot = _bot(RecordingTransport(), session=FakeSession())

    assert bot._fixture_quota_key("esp.1", 12345) == "esp.1:12345"  # noqa: SLF001
    # Sin clave no se cobra: cobrar mal es peor que no cobrar.
    assert bot._fixture_quota_key(None, 12345) is None  # noqa: SLF001


# --------------------------------------------------------------------------
# Interruptor apagado: comportamiento idéntico al previo a la fase
# --------------------------------------------------------------------------

def test_billing_disabled_treats_everyone_as_premium() -> None:
    """Con el cobro apagado nada queda gateado y no hay llamadas internas."""

    transport = RecordingTransport()
    bot = _bot(transport, billing=False)

    assert bot._require_premium(7) is None  # noqa: SLF001
    assert bot._quota_gate(7, "esp.1", 1) is None  # noqa: SLF001
    assert bot._entitlement(7) == Entitlement(plan="premium")  # noqa: SLF001


def test_billing_config_requires_all_pieces_when_enabled() -> None:
    """Activar el cobro sin secretos debe fallar al construir, no en producción."""

    with pytest.raises(ValueError, match="telegram_billing_secrets_missing"):
        BillingConfig(enabled=True, miniapp_internal_url="https://x.test")

    with pytest.raises(ValueError, match="telegram_billing_miniapp_url_missing"):
        BillingConfig(
            enabled=True, billing_secret=SECRET, miniapp_internal_key="k")

    with pytest.raises(ValueError, match="https_required"):
        BillingConfig(
            enabled=True, billing_secret=SECRET, miniapp_internal_key="k",
            miniapp_internal_url="http://miniapp.test")


# --------------------------------------------------------------------------
# Reembolsos
# --------------------------------------------------------------------------

def test_refunded_payment_is_forwarded() -> None:
    """Sin esto, un reembolso hecho desde Telegram dejaría premium a quien no paga."""

    transport = RecordingTransport()
    session = FakeSession([_Response(200, {"applied": True})])
    bot = _bot(transport, session=session)

    bot.process_update({"message": {
        "chat": {"id": 70, "type": "private"},
        "from": {"id": 7},
        "refunded_payment": {
            "telegram_payment_charge_id": "charge_1", "total_amount": 250,
        },
    }})

    assert session.requests[0]["url"].endswith("/api/internal/billing/refund")


def test_payment_forward_failure_reports_pending_not_failure() -> None:
    """Decir que falló sería falso: el dinero salió."""

    import requests

    transport = RecordingTransport()
    session = FakeSession([requests.RequestException("down")] * 3)
    bot = _bot(transport, session=session)

    bot.process_update({"message": {
        "chat": {"id": 70, "type": "private"},
        "from": {"id": 7},
        "successful_payment": {
            "telegram_payment_charge_id": "charge_1",
            "invoice_payload": _sign(7),
            "total_amount": 250, "currency": "XTR",
        },
    }})

    assert transport.sent
    text = transport.sent[0][1]
    assert "recibido" in text.casefold()
    assert "activando" in text.casefold()
