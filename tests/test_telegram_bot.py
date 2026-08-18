"""Pruebas del adaptador Telegram: cuenta, autorización y transporte.

Hasta la Fase 125 el bot exploraba catálogo, predecía partidos y navegaba
menús de botones. Ese excedente se retiró porque la Mini App ya lo hace
completo: el bot quedó reducido a alta de cuenta (`/whoami`, `/start`,
`/help`) y a lo que necesita el cobro con Stars (`/premium`, `/mi_plan`,
`pre_checkout_query`, `successful_payment`, `refunded_payment` -cubiertos en
`tests/test_phase_125_star_subscriptions.py`, no aquí-). Estas pruebas cubren
lo que le queda al bot por sí mismo: autorización, límite de ráfagas,
mensajes de servicio de pago que no llegan por `text`, botones de chats
anteriores a este cambio, y el transporte HTTP.
"""
from __future__ import annotations

from typing import Any

from src.telegram_bot import (
    LongPollingRunner,
    PredictionGateway,
    TelegramBotConfig,
    TelegramHttpTransport,
    TelegramPredictionBot,
    TelegramTransport,
    _split_message,
)


class FakeTransport(TelegramTransport):
    """Transporte determinista sin red."""

    def __init__(self, updates: list[dict[str, Any]] | None = None) -> None:
        """Inicializa bandeja y mensajes enviados."""

        self.updates = updates or []
        self.sent: list[tuple[int, str]] = []
        self.offsets: list[int | None] = []
        self.answered_callbacks: list[str] = []

    def get_updates(
        self, offset: int | None, timeout_seconds: int,
    ) -> list[dict[str, Any]]:
        """Devuelve el lote configurado."""

        self.offsets.append(offset)
        return self.updates

    def send_message(self, chat_id: int, text: str) -> None:
        """Conserva la respuesta para assertions."""

        self.sent.append((chat_id, text))

    def answer_callback_query(self, callback_id: str) -> None:
        """Conserva el id confirmado, sin enrutar nada más."""

        self.answered_callbacks.append(callback_id)


class StubGateway(PredictionGateway):
    """Gateway mínimo instanciable.

    El bot ya no llama a ningún método suyo -toda predicción vive en la Mini
    App-, pero `TelegramPredictionBot.__init__` sigue exigiendo uno para no
    romper `scripts/run_phase_97_telegram_bot.py`. Sólo implementa los tres
    métodos que `PredictionGateway` marca abstractos.
    """

    def predict_fixture(self, payload: dict[str, Any]) -> dict[str, Any]:
        """No se invoca; existe para satisfacer la ABC."""

        return {}

    def predict_upcoming(self, payload: dict[str, Any]) -> dict[str, Any]:
        """No se invoca; existe para satisfacer la ABC."""

        return {}

    def readiness(self) -> dict[str, Any]:
        """No se invoca; existe para satisfacer la ABC."""

        return {"ready": True}


class _Response:
    """Respuesta mínima para probar el transporte sin red."""

    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, Any]:
        """Devuelve el cuerpo Telegram configurado."""

        return self._payload


class _Session:
    """Sesión que rechaza HTML y acepta el reintento plano."""

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def post(
        self, _url: str, json: dict[str, Any], timeout: tuple[int, int],
    ) -> _Response:
        """Registra payloads y simula dos respuestas consecutivas."""

        self.payloads.append(json)
        if len(self.payloads) == 1:
            return _Response(400, {
                "ok": False, "description": "can't parse entities"})
        return _Response(200, {"ok": True, "result": {}})


def _update(update_id: int, text: str, user_id: int = 7) -> dict[str, Any]:
    """Construye un mensaje privado."""

    return {
        "update_id": update_id,
        "message": {
            "chat": {"id": 70, "type": "private"},
            "from": {"id": user_id}, "text": text,
        },
    }


def _callback(update_id: int, data: str, user_id: int = 7) -> dict[str, Any]:
    """Construye un callback privado -de un chat anterior a la Fase 125-."""

    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"cb-{update_id}", "data": data, "from": {"id": user_id},
            "message": {"chat": {"id": 70, "type": "private"}},
        },
    }


def _bot(
    transport: FakeTransport, gateway: StubGateway | None = None,
    allowed: frozenset[int] = frozenset({7}),
    access_mode: str = "private",
    rate_limit: int = 10,
) -> TelegramPredictionBot:
    """Construye el bot con dependencias falsas."""

    config = TelegramBotConfig(
        "secret", allowed, access_mode=access_mode,
        rate_limit_requests=rate_limit)
    return TelegramPredictionBot(config, transport, gateway or StubGateway())


def test_whoami_does_not_require_authorization() -> None:
    """Permite descubrir el ID sin necesitar membresía."""

    transport = FakeTransport()
    _bot(transport, allowed=frozenset()).process_update(_update(1, "/whoami"))

    assert "7" in transport.sent[0][1]


def test_help_is_available_and_does_not_require_authorization() -> None:
    """Entrega ayuda a cualquier chat privado, sin secretos filtrados."""

    transport = FakeTransport()
    _bot(transport, allowed=frozenset()).process_update(_update(1, "/help"))

    assert len(transport.sent) == 1
    message = transport.sent[0][1]
    assert "COMANDOS" in message
    assert "/premium" in message
    assert "TELEGRAM_BOT_TOKEN" not in message
    assert "CONFIGURACIÓN (env vars)" not in message


def test_start_shows_welcome_instead_of_full_help() -> None:
    """El inicio saluda y deja el detalle de comandos bajo demanda."""

    transport = FakeTransport()
    _bot(transport, allowed=frozenset()).process_update(_update(1, "/start"))

    assert "Bienvenido" in transport.sent[0][1]
    assert "COMANDOS" not in transport.sent[0][1]


def test_transport_retries_rejected_html_as_plain_text() -> None:
    """Evita que un HTML rechazado deje bloqueado el polling."""

    session = _Session()
    config = TelegramBotConfig("secret", frozenset({7}))
    TelegramHttpTransport(config, session).send_message(
        70, "<b>Ayuda</b> &amp; opciones", {"inline_keyboard": []})

    assert len(session.payloads) == 2
    assert session.payloads[0]["parse_mode"] == "HTML"
    assert "parse_mode" not in session.payloads[1]
    assert session.payloads[1]["text"] == "Ayuda & opciones"


def test_unauthorized_user_cannot_use_account_commands() -> None:
    """Bloquea `/mi_plan` fuera de la allowlist, igual que antes con la inferencia."""

    transport = FakeTransport()
    _bot(transport, allowed=frozenset()).process_update(_update(1, "/mi_plan"))

    assert "ACCESO PREMIUM REQUERIDO" in transport.sent[0][1]


def test_public_mode_allows_any_private_user() -> None:
    """Abre consultas privadas sin requerir una allowlist."""

    transport = FakeTransport()
    _bot(
        transport, allowed=frozenset(), access_mode="public",
    ).process_update(_update(1, "/whoami", 999))

    assert "999" in transport.sent[0][1]


def test_public_mode_keeps_per_user_rate_limit() -> None:
    """Impide que la apertura pública retire el control de ráfagas."""

    transport = FakeTransport()
    bot = _bot(
        transport, allowed=frozenset(), access_mode="public", rate_limit=1)
    bot.process_update(_update(1, "/mi_plan", 999))
    bot.process_update(_update(2, "/mi_plan", 999))

    assert "Demasiadas solicitudes" in transport.sent[-1][1]


def test_stale_callback_query_is_only_acknowledged() -> None:
    """Un botón de un chat de antes de la Fase 125 no puede hacer nada.

    El bot ya no genera ningún botón `callback_data` -sólo el `web_app` de
    abrir la Mini App, que Telegram resuelve sin avisar-. Si igual llega una
    pulsación vieja, sólo se confirma para apagar el reloj de arena.
    """

    transport = FakeTransport()
    bot = _bot(transport, allowed=frozenset(), access_mode="public")

    bot.process_update(_callback(1, "menu:upcoming", 999))

    assert transport.answered_callbacks == ["cb-1"]
    assert transport.sent == []


def test_public_mode_ignores_group_messages() -> None:
    """Mantiene el bot público limitado a conversaciones privadas."""

    update = _update(1, "/whoami", 999)
    update["message"]["chat"]["type"] = "group"
    transport = FakeTransport()
    _bot(
        transport, allowed=frozenset(), access_mode="public",
    ).process_update(update)

    assert not transport.sent


def test_removed_manual_commands_fall_back_to_help() -> None:
    """Los comandos de exploración manual retirados no rompen nada.

    `/partido`, `/predict`, `/estado`, `/en_vivo`, `/modelos`, `/partidos`,
    `/menu` y `/buscar_equipo` vivían en el bot antes de la Fase 125; ahora
    esa exploración es exclusiva de la Mini App, y cualquiera de ellos cae en
    el mismo mensaje de comando no reconocido.
    """

    transport = FakeTransport()
    bot = _bot(transport)
    for command in (
        "/partido esp.1 20300110 Real Madrid | Barcelona",
        "/predict esp.1 94 86 2030-01-10T20:00:00+00:00",
        "/estado", "/en_vivo", "/modelos", "/partidos", "/menu",
        "/buscar_equipo esp.1 Real Madrid",
    ):
        transport.sent.clear()
        bot.process_update(_update(1, command))
        assert "Comando no reconocido" in transport.sent[0][1], command


def test_long_polling_ignores_duplicate_update() -> None:
    """Confirma cada update mediante offset monotónico."""

    updates = [_update(4, "/whoami"), _update(4, "/whoami")]
    transport = FakeTransport(updates)
    runner = LongPollingRunner(_bot(transport), transport, 1)

    assert runner.poll_once() == 1
    assert len(transport.sent) == 1


def test_long_polling_advances_past_a_poison_update() -> None:
    """Un update que hace fallar al handler no debe bloquear el offset para siempre.

    Antes, `self._offset` sólo avanzaba tras un `process_update` exitoso: un
    único update "veneno" -que siempre hace fallar a un handler- quedaba
    reenviado por Telegram en cada `getUpdates` (el offset nunca lo supera),
    así que el bot dejaba de procesar cualquier mensaje nuevo de cualquier
    usuario, para siempre. La corrección exige que el offset avance aunque
    el procesamiento falle -confirmación, no éxito-, y que el update sano
    que viene después sí se procese en la misma pasada.
    """

    poison, healthy = _update(5, "/whoami"), _update(6, "/whoami")
    transport = FakeTransport([poison, healthy])
    bot = _bot(transport)
    original = bot.process_update

    def flaky(update: dict[str, Any]) -> None:
        if update["update_id"] == 5:
            raise RuntimeError("handler roto")
        original(update)

    bot.process_update = flaky  # type: ignore[method-assign]
    runner = LongPollingRunner(bot, transport, 1)

    processed = runner.poll_once()

    # Sólo el update sano cuenta como procesado, pero el offset ya reconoce
    # ambos -si no, el siguiente `poll_once` volvería a pedir el veneno-.
    assert processed == 1
    assert runner._offset == 7
    assert len(transport.sent) == 1


def test_messages_are_split_below_telegram_limit() -> None:
    """Divide mensajes sin exceder el límite conservador."""

    parts = _split_message(("línea\n" * 1000).strip())

    assert len(parts) > 1
    assert all(len(part) <= 3900 for part in parts)


# Version: 2.0.0
# Created: 2026-07-29
