"""Cobro con Telegram Stars visto desde el bot.

El bot es el único proceso que hace `getUpdates`, así que `pre_checkout_query`
y `successful_payment` aterrizan aquí. Pero el bot **no tiene base de datos**:
la Fase 109 lo diseñó sin ella a propósito, y aplicar un pago son tres
escrituras acopladas -asiento, suscripción y plan- que deben ir en una única
transacción. Reimplementarlas en Python garantizaría divergencia, y el modo de
divergencia sería "el usuario pagó y no tiene premium".

Por eso este módulo hace dos cosas y sólo dos:

1. Verifica firmas **sin red**, para poder contestar `pre_checkout_query`
   dentro de la ventana de 10 segundos que concede Telegram.
2. Habla con los endpoints internos de la Mini App, que es quien escribe.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import requests

LOGGER = logging.getLogger(__name__)

_PAYLOAD_VERSION = "v1"
_PAYLOAD_PARTS = 7


@dataclass(frozen=True, slots=True)
class BillingPayload:
    """Contenido verificado de una factura."""

    user_id: int
    plan_code: str
    stars_amount: int
    issued_at: int
    nonce: str


def verify_billing_payload(
    raw: str | None,
    secret: str | None,
    *,
    expected_user_id: int | None = None,
    max_age_seconds: int | None = None,
    now: float | None = None,
) -> BillingPayload | None:
    """Valida firma y forma de un `invoice_payload`.

    Espejo exacto de `miniapp/lib/billing/payload.ts`: mismo formato, misma
    clave y mismo HMAC-SHA256 en base64url. Si las dos implementaciones
    divergieran, el síntoma sería "todos los pre-checkout rechazados", que
    parece un problema de Telegram y no lo es.

    `max_age_seconds` es opcional a propósito. En `pre_checkout_query` sí se
    acota -una factura de hace días es basura-, pero en `successful_payment`
    **no se puede**: una renovación a los seis meses llega con el payload
    original, y exigir frescura ahí rechazaría al suscriptor fiel.
    """

    if not raw or not secret:
        return None
    if len(raw) > 256:
        return None
    parts = raw.split(".")
    if len(parts) != _PAYLOAD_PARTS or parts[0] != _PAYLOAD_VERSION:
        return None

    body = ".".join(parts[:6])
    digest = hmac.new(
        secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256,
    ).digest()
    expected = _b64url(digest)
    # `compare_digest` no filtra por tiempo la posición del primer byte que
    # difiere; comparar con `==` sí lo haría.
    if not hmac.compare_digest(expected, parts[6]):
        return None

    try:
        user_id = int(parts[1])
        stars_amount = int(parts[3])
        issued_at = int(parts[4])
    except ValueError:
        return None
    if user_id <= 0 or stars_amount <= 0 or issued_at <= 0:
        return None
    if not parts[2] or not parts[5]:
        return None

    # El pagador tiene que ser el titular de la factura: sin esta comprobación,
    # una factura válida reenviada a otra persona activaría la cuenta ajena.
    if expected_user_id is not None and expected_user_id != user_id:
        return None
    if max_age_seconds is not None:
        age = int(now if now is not None else time.time()) - issued_at
        if age > max_age_seconds or age < -60:
            return None
    return BillingPayload(
        user_id=user_id, plan_code=parts[2], stars_amount=stars_amount,
        issued_at=issued_at, nonce=parts[5],
    )


def _b64url(value: bytes) -> str:
    """Codifica en base64url sin relleno, como hace Node."""

    import base64

    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


@dataclass(frozen=True, slots=True)
class Entitlement:
    """Nivel resuelto de un usuario, tal y como lo ve el bot."""

    plan: str
    #: `None` significa ilimitado, no cero.
    remaining_predictions: int | None = None
    expires_at: str | None = None
    #: Cierto cuando se resolvió por degradación y no por lectura real.
    degraded: bool = False

    @property
    def premium(self) -> bool:
        """Indica si el nivel abre las funciones de pago."""

        return self.plan == "premium"


_FREE_DEGRADED = Entitlement(plan="free", remaining_predictions=None, degraded=True)


class MiniappBillingClient:
    """Cliente de los endpoints internos de la Mini App.

    Cachea el nivel 60 segundos y **degrada a `free`** cuando la Mini App no
    responde. Las tres opciones posibles ante un fallo no son equivalentes:

    * cerrar el acceso le diría "no tienes acceso" a alguien que pagó, por un
      reinicio de 30 segundos que además es culpa nuestra;
    * abrir a premium regala el producto entero en cada parpadeo;
    * degradar a `free` acota el daño y deja el mensaje honesto: historial,
      catálogo y predicciones siguen funcionando.

    La degradación **no se cachea**: un fallo transitorio no debe fijar 60
    segundos de castigo a un suscriptor.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        session: requests.Session | None = None,
        ttl_seconds: float = 60.0,
        timeout: tuple[float, float] = (2.0, 3.0),
        max_entries: int = 2000,
    ) -> None:
        """Configura destino, credencial y caché acotada."""

        self._base = base_url.rstrip("/")
        self._key = api_key
        self._session = session or requests.Session()
        self._ttl = ttl_seconds
        self._timeout = timeout
        self._max_entries = max_entries
        self._lock = threading.Lock()
        self._cache: dict[int, tuple[float, Entitlement]] = {}

    def entitlement_for(self, user_id: int, now: float | None = None) -> Entitlement:
        """Devuelve el nivel del usuario, con caché y degradación segura."""

        moment = now if now is not None else time.monotonic()
        with self._lock:
            cached = self._cache.get(user_id)
            if cached and cached[0] > moment:
                return cached[1]
        try:
            payload = self._request(
                "GET", "/api/internal/entitlement", params={"user_id": user_id})
        except _BillingUnavailable:
            LOGGER.warning("entitlement_fail_open user_id=%s", user_id)
            return _FREE_DEGRADED
        remaining = payload.get("remaining_predictions")
        value = Entitlement(
            plan=str(payload.get("plan", "free")),
            remaining_predictions=(
                int(remaining) if isinstance(remaining, (int, float)) else None),
            expires_at=(
                str(payload["expires_at"]) if payload.get("expires_at") else None),
        )
        with self._lock:
            if len(self._cache) >= self._max_entries:
                self._cache.pop(next(iter(self._cache)), None)
            self._cache[user_id] = (moment + self._ttl, value)
        return value

    def invalidate(self, user_id: int) -> None:
        """Olvida el nivel cacheado tras un pago o un reembolso."""

        with self._lock:
            self._cache.pop(user_id, None)

    def consume_prediction(self, user_id: int, fixture_key: str) -> dict[str, Any] | None:
        """Descuenta una predicción del cupo diario compartido.

        Devuelve `None` cuando la contabilidad no está disponible. Quien llama
        debe **servir igualmente**: negarse a predecir porque el contador está
        caído castiga al usuario por un fallo nuestro, y la pérdida ya la acota
        el rate limiter del bot.
        """

        try:
            return self._request("POST", "/api/internal/quota/consume", json={
                "user_id": user_id, "fixture_key": fixture_key,
            })
        except _BillingUnavailable:
            LOGGER.warning("quota_fail_open user_id=%s", user_id)
            return None

    def release_prediction(self, user_id: int, fixture_key: str) -> None:
        """Devuelve al cupo una unidad que no llegó a producir nada."""

        try:
            self._request("POST", "/api/internal/quota/consume", json={
                "user_id": user_id, "fixture_key": fixture_key, "release": True,
            })
        except _BillingUnavailable:
            # Se pierde una unidad del día de alguien. Es un daño acotado y
            # preferible a propagar el fallo por encima de la predicción que
            # de verdad falló.
            LOGGER.warning("quota_release_failed user_id=%s", user_id)

    def create_invoice(self, user_id: int) -> str | None:
        """Pide a la Mini App el enlace de pago."""

        try:
            payload = self._request("POST", "/api/internal/billing/invoice", json={
                "user_id": user_id,
            })
        except _BillingUnavailable:
            return None
        link = payload.get("link")
        return str(link) if isinstance(link, str) and link else None

    def forward_payment(self, body: dict[str, Any]) -> bool:
        """Entrega un `successful_payment` a quien sí escribe en la base."""

        return self._forward("/api/internal/billing/payment", body)

    def forward_refund(self, body: dict[str, Any]) -> bool:
        """Entrega un `refunded_payment`."""

        return self._forward("/api/internal/billing/refund", body)

    def _forward(self, path: str, body: dict[str, Any]) -> bool:
        """Reenvía con reintentos y sin propagar la excepción."""

        for attempt in range(3):
            try:
                self._request("POST", path, json=body)
                return True
            except _BillingUnavailable:
                if attempt == 2:
                    return False
                time.sleep(0.5 * (2 ** attempt))
        return False

    def _request(
        self, method: str, path: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Ejecuta una llamada interna y sanitiza cualquier fallo."""

        try:
            response = self._session.request(
                method, f"{self._base}{path}",
                params=params, json=json,
                headers={"X-Dikamaha-Internal-Key": self._key},
                timeout=self._timeout,
            )
        except requests.RequestException as error:
            raise _BillingUnavailable("miniapp_unreachable") from error
        if response.status_code >= 400:
            LOGGER.warning(
                "miniapp_internal_rejected path=%s status=%s",
                path, response.status_code)
            raise _BillingUnavailable("miniapp_rejected")
        try:
            payload = response.json()
        except ValueError as error:
            raise _BillingUnavailable("miniapp_response_invalid") from error
        if not isinstance(payload, dict):
            raise _BillingUnavailable("miniapp_response_invalid")
        return payload


class _BillingUnavailable(RuntimeError):
    """Fallo sanitizado del canal interno con la Mini App."""


@dataclass(frozen=True, slots=True)
class BillingConfig:
    """Configuración de cobro del bot."""

    enabled: bool = False
    billing_secret: str | None = field(default=None, repr=False)
    miniapp_internal_url: str | None = None
    miniapp_internal_key: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        """Valida que activar el cobro exija todas sus piezas."""

        if not self.enabled:
            return
        if not self.billing_secret or not self.miniapp_internal_key:
            raise ValueError("telegram_billing_secrets_missing")
        if not self.miniapp_internal_url:
            raise ValueError("telegram_billing_miniapp_url_missing")
        url = self.miniapp_internal_url
        loopback = url.startswith("http://127.0.0.1") or url.startswith("http://localhost")
        if not url.startswith("https://") and not loopback:
            raise ValueError("telegram_billing_miniapp_url_https_required")
