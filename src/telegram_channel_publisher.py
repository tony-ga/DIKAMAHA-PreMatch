"""Publicador idempotente de predicciones y resultados para Telegram.

# Requirements:
#   requests>=2.31
#   sqlalchemy>=2
#   tenacity>=8.2

Version: 1.9.0
Created: 2026-07-29
"""

from __future__ import annotations

import html
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import requests
from sqlalchemy import JSON, DateTime, Integer, String, UniqueConstraint, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.prematch_raw_store import canonical_hash
from src.telegram_bot import PredictionGateway

LOGGER = logging.getLogger(__name__)
MEXICO_TZ = ZoneInfo("America/Mexico_City")
SUMMARY_TIME = time(9, 0)
SETTLEMENT_DELAY = timedelta(hours=3)
CHANNEL_MODES = frozenset({"full", "lite"})
LITE_FIXTURE_LIMIT = 3
DISTRIBUTIONAL_CONTRACT_VERSION = "phase102_v4_direct_totals"
MARKET_LAYOUT_VERSION = "global_dashboard_direct_totals_v3"


class ChannelPublisherError(RuntimeError):
    """Error sanitizado de difusión o persistencia."""


class ChannelBroadcastBase(DeclarativeBase):
    """Base ORM aislada de Fase 101."""


class FrozenChannelPrediction(ChannelBroadcastBase):
    """Predicción inmutable persistida antes de su publicación."""

    __tablename__ = "channel_predictions"
    __table_args__ = (UniqueConstraint("fixture_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fixture_key: Mapped[str] = mapped_column(String(160), nullable=False)
    target_date: Mapped[str] = mapped_column(String(10), nullable=False)
    league_slug: Mapped[str] = mapped_column(String(100), nullable=False)
    match_id: Mapped[int] = mapped_column(Integer, nullable=False)
    competition_id: Mapped[str] = mapped_column(String(100), nullable=False)
    kickoff_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fixture_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    prediction_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    prediction_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    frozen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ChannelPublication(ChannelBroadcastBase):
    """Confirmación idempotente de un mensaje aceptado por Telegram."""

    __tablename__ = "channel_publications"
    __table_args__ = (UniqueConstraint("idempotency_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(220), nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    fixture_key: Mapped[str | None] = mapped_column(String(160))
    target_date: Mapped[str | None] = mapped_column(String(10))
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    telegram_message_id: Mapped[str] = mapped_column(String(100), nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FrozenChannelMarketSnapshot(ChannelBroadcastBase):
    """Snapshot append-only de un contrato de mercados versionado."""

    __tablename__ = "channel_market_snapshots"
    __table_args__ = (UniqueConstraint("fixture_key", "contract_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fixture_key: Mapped[str] = mapped_column(String(160), nullable=False)
    contract_version: Mapped[str] = mapped_column(String(40), nullable=False)
    prediction_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    prediction_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    frozen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


@dataclass(frozen=True, slots=True)
class FrozenPrediction:
    """Vista desacoplada de una predicción congelada."""

    fixture_key: str
    target_date: str
    league_slug: str
    match_id: int
    competition_id: str
    kickoff_ts: datetime
    fixture: dict[str, Any]
    prediction: dict[str, Any]
    prediction_hash: str
    frozen_at: datetime


def channel_prediction_messages(
    fixture: dict[str, Any], prediction: dict[str, Any],
) -> list[str]:
    """Renderiza una predicción con el contrato visual exacto del canal."""

    source = dict(fixture)
    nested = prediction.get("fixture")
    if isinstance(nested, dict):
        source.setdefault("home_team_name", nested.get("home_team_name"))
        source.setdefault("away_team_name", nested.get("away_team_name"))
    kickoff = _parse_ts(source.get("kickoff_ts") or prediction["kickoff_ts"])
    source.setdefault("league_slug", prediction.get("league_slug", ""))
    source.setdefault("match_id", prediction.get("match_id", 0))
    source.setdefault("competition_id", source.get("match_id", 0))
    now = datetime.now(timezone.utc)
    row = FrozenPrediction(
        _fixture_key(source), kickoff.astimezone(MEXICO_TZ).date().isoformat(),
        str(source["league_slug"]), int(source["match_id"]),
        str(source["competition_id"]), kickoff, source, dict(prediction),
        canonical_hash(prediction), now)
    return [_card_text(row), *_market_texts(row)]


class ChannelTransport(ABC):
    """Puerto para publicar mensajes en un canal."""

    @abstractmethod
    def send_message(self, text: str) -> str:
        """Publica un mensaje y devuelve el identificador confirmado."""

    def send_prediction_card(self, text: str, logos: list[str]) -> str:
        """Publica una tarjeta visual; usa texto si no hay soporte multimedia."""

        return self.send_message(text)


class ChannelRepository(ABC):
    """Puerto de persistencia append-only del publicador."""

    @abstractmethod
    def freeze(
        self, fixture: dict[str, Any], prediction: dict[str, Any],
        target_date: str, frozen_at: datetime,
    ) -> FrozenPrediction:
        """Congela o recupera la predicción del fixture."""

    @abstractmethod
    def predictions(self) -> list[FrozenPrediction]:
        """Devuelve predicciones congeladas por kickoff."""

    @abstractmethod
    def freeze_market_snapshot(
        self, fixture_key: str, contract_version: str,
        prediction: dict[str, Any], frozen_at: datetime,
    ) -> dict[str, Any]:
        """Congela o recupera un contrato de mercado append-only."""

    @abstractmethod
    def market_snapshot(
        self, fixture_key: str, contract_version: str,
    ) -> dict[str, Any] | None:
        """Recupera un snapshot versionado sin modificarlo."""

    @abstractmethod
    def publication_exists(self, key: str) -> bool:
        """Indica si Telegram confirmó la clave idempotente."""

    @abstractmethod
    def record_publication(
        self, key: str, kind: str, message_id: str, text: str,
        published_at: datetime, fixture_key: str | None = None,
        target_date: str | None = None,
    ) -> None:
        """Registra una publicación confirmada."""


class SqlAlchemyChannelRepository(ChannelRepository):
    """Repositorio SQLAlchemy transaccional de Fase 101."""

    def __init__(self, factory: sessionmaker[Session]) -> None:
        """Inyecta una fábrica de sesiones configurada."""

        self._factory = factory

    def freeze(
        self, fixture: dict[str, Any], prediction: dict[str, Any],
        target_date: str, frozen_at: datetime,
    ) -> FrozenPrediction:
        """Inserta una predicción una sola vez y preserva la primera versión."""

        key = _fixture_key(fixture)
        with self._factory() as session:
            with session.begin():
                row = session.execute(select(FrozenChannelPrediction).where(
                    FrozenChannelPrediction.fixture_key == key)).scalar_one_or_none()
                if row is None:
                    row = _prediction_row(fixture, prediction, target_date, frozen_at)
                    session.add(row)
                    session.flush()
                result = _frozen(row)
        return result

    def predictions(self) -> list[FrozenPrediction]:
        """Lee la cola completa sin modificar publicaciones."""

        statement = select(FrozenChannelPrediction).order_by(
            FrozenChannelPrediction.kickoff_ts, FrozenChannelPrediction.id)
        with self._factory() as session:
            return [_frozen(row) for row in session.execute(statement).scalars()]

    def freeze_market_snapshot(
        self, fixture_key: str, contract_version: str,
        prediction: dict[str, Any], frozen_at: datetime,
    ) -> dict[str, Any]:
        """Inserta una sola versión y preserva la primera respuesta."""

        with self._factory() as session:
            with session.begin():
                row = self._market_row(
                    session, fixture_key, contract_version)
                if row is None:
                    row = FrozenChannelMarketSnapshot(
                        fixture_key=fixture_key,
                        contract_version=contract_version,
                        prediction_json=dict(prediction),
                        prediction_hash=canonical_hash(prediction),
                        frozen_at=_utc(frozen_at))
                    session.add(row)
                    session.flush()
                return dict(row.prediction_json)

    def market_snapshot(
        self, fixture_key: str, contract_version: str,
    ) -> dict[str, Any] | None:
        """Lee un contrato versionado mediante ORM."""

        with self._factory() as session:
            row = self._market_row(session, fixture_key, contract_version)
            return dict(row.prediction_json) if row is not None else None

    @staticmethod
    def _market_row(
        session: Session, fixture_key: str, contract_version: str,
    ) -> FrozenChannelMarketSnapshot | None:
        """Busca un snapshot por su identidad compuesta."""

        statement = select(FrozenChannelMarketSnapshot).where(
            FrozenChannelMarketSnapshot.fixture_key == fixture_key,
            FrozenChannelMarketSnapshot.contract_version == contract_version)
        return session.execute(statement).scalar_one_or_none()

    def publication_exists(self, key: str) -> bool:
        """Consulta la clave única mediante ORM."""

        statement = select(ChannelPublication.id).where(
            ChannelPublication.idempotency_key == key)
        with self._factory() as session:
            return session.execute(statement).first() is not None

    def record_publication(
        self, key: str, kind: str, message_id: str, text: str,
        published_at: datetime, fixture_key: str | None = None,
        target_date: str | None = None,
    ) -> None:
        """Inserta la confirmación dentro de una transacción."""

        with self._factory() as session:
            with session.begin():
                existing = session.execute(select(ChannelPublication.id).where(
                    ChannelPublication.idempotency_key == key)).first()
                if existing is None:
                    session.add(ChannelPublication(
                        idempotency_key=key, kind=kind, fixture_key=fixture_key,
                        target_date=target_date, payload_hash=canonical_hash(text),
                        telegram_message_id=message_id, published_at=published_at))


class TelegramChannelTransport(ChannelTransport):
    """Adaptador HTTPS de Telegram para un canal administrado por el bot."""

    def __init__(
        self, token: str, channel: str,
        session: requests.Session | None = None,
    ) -> None:
        """Configura credenciales fuera del código y destino explícito."""

        if not token or not channel:
            raise ValueError("telegram_channel_configuration_missing")
        self._base_url = f"https://api.telegram.org/bot{token}"
        self._channel = channel
        self._session = session or requests.Session()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        retry=retry_if_exception_type(ChannelPublisherError),
        reraise=True,
    )
    def send_message(self, text: str) -> str:
        """Publica HTML compacto con retry y respuesta validada."""

        result = self._post("sendMessage", {
            "chat_id": self._channel, "text": text, "parse_mode": "HTML",
            "disable_web_page_preview": True})
        return str(result.get("message_id") or "")

    def send_prediction_card(self, text: str, logos: list[str]) -> str:
        """Publica dos escudos oficiales como álbum con la predicción."""

        valid = [logo for logo in logos if logo.startswith("https://")]
        if len(valid) < 2:
            return self.send_message(text)
        result = self._post("sendMediaGroup", {
            "chat_id": self._channel,
            "media": [
                {"type": "photo", "media": valid[0], "caption": text,
                 "parse_mode": "HTML"},
                {"type": "photo", "media": valid[1]},
            ],
        })
        first = result[0] if isinstance(result, list) and result else {}
        return str(first.get("message_id") or "")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        retry=retry_if_exception_type(ChannelPublisherError),
        reraise=True,
    )
    def _post(self, method: str, body: dict[str, Any]) -> Any:
        """Ejecuta una llamada Telegram validada con retry exponencial."""

        try:
            response = self._session.post(
                f"{self._base_url}/{method}", json=body, timeout=(5, 20))
            payload = response.json()
        except (requests.RequestException, ValueError) as error:
            raise ChannelPublisherError("telegram_channel_unavailable") from error
        if response.status_code >= 400 or not payload.get("ok"):
            raise ChannelPublisherError("telegram_channel_rejected_message")
        return payload.get("result")


class TelegramChannelPublisher:
    """Orquesta resumen, tarjetas y resultados con replay idempotente."""

    def __init__(
        self, gateway: PredictionGateway, repository: ChannelRepository,
        transport: ChannelTransport, mode: str = "full",
    ) -> None:
        """Inyecta los tres puertos sin acoplar negocio a infraestructura."""

        self._gateway = gateway
        self._repository = repository
        self._transport = transport
        self._mode = _channel_mode(mode)

    def run_cycle(self, now: datetime) -> dict[str, int]:
        """Ejecuta un ciclo determinista con reloj UTC consciente."""

        current = _utc(now)
        counts = {
            "frozen": 0, "summaries": 0, "cards": 0,
            "markets": 0, "results": 0,
        }
        local = current.astimezone(MEXICO_TZ)
        if local.timetz().replace(tzinfo=None) >= SUMMARY_TIME:
            frozen, summaries, cards, markets = self._daily(
                local.date() + timedelta(days=1), current)
            counts.update(
                frozen=frozen, summaries=summaries,
                cards=cards, markets=markets)
        counts["results"] = self._results(current)
        return counts

    def _daily(
        self, target: date, now: datetime,
    ) -> tuple[int, int, int, int]:
        """Publica aviso y predicciones juntas una sola vez."""

        target_text = target.isoformat()
        complete_key = f"daily:{target_text}:complete"
        if self._repository.publication_exists(complete_key):
            predictions = [row for row in self._repository.predictions()
                           if row.target_date == target_text]
            cards, markets = self._publish_predictions(
                self._select_frozen(predictions), now)
            return 0, 0, cards, markets
        fixtures = self._select_fixtures(self._tomorrow_fixtures(target))
        fixtures = self._with_logos(fixtures)
        predictions = [self._freeze(row, target_text, now) for row in fixtures]
        chunks = _summary_chunks(target, predictions, self._mode)
        published = sum(self._publish(
            f"daily:{target_text}:{index}", "daily_summary", text, now,
            target_date=target_text) for index, text in enumerate(chunks))
        cards, markets = self._publish_predictions(predictions, now)
        self._repository.record_publication(
            complete_key, "daily_complete", "internal",
            f"daily_complete:{target_text}:{len(chunks)}", now,
            target_date=target_text)
        return len(predictions), published, cards, markets

    def _select_frozen(
        self, predictions: list[FrozenPrediction],
    ) -> list[FrozenPrediction]:
        """Aplica lite a predicciones ya congeladas durante un replay."""

        return predictions if self._mode == "full" else predictions[:LITE_FIXTURE_LIMIT]

    def _publish_predictions(
        self, rows: list[FrozenPrediction], now: datetime,
    ) -> tuple[int, int]:
        """Intercala tarjeta principal y mercados de cada partido."""

        cards, markets = 0, 0
        for row in rows:
            cards += self._publish_card(row, now)
            markets += self._publish_markets(row, now)
        return cards, markets

    def _tomorrow_fixtures(self, target: date) -> list[dict[str, Any]]:
        """Agrega por liga para evitar el límite global de veinte fixtures."""

        compact = target.strftime("%Y%m%d")
        leagues = self._gateway.explorer_leagues().get("leagues", [])
        rows: list[dict[str, Any]] = []
        for league in leagues:
            slug = str(league.get("slug") or "")
            try:
                batch = self._gateway.list_upcoming(20, slug, compact)
            except (OSError, RuntimeError, ValueError) as error:
                LOGGER.warning(
                    "channel_league_skipped league=%s error=%s",
                    slug, type(error).__name__)
                continue
            for row in batch.get("fixtures", []):
                if isinstance(row, dict):
                    rows.append({**row, "league_name": league.get("name") or slug})
        unique = {str(row.get("match_id")): row for row in rows if row.get("match_id")}
        return sorted(unique.values(), key=lambda row: str(row.get("kickoff_ts")))

    def _select_fixtures(
        self, fixtures: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Aplica el interruptor full/lite sin alterar el universo consultado."""

        return fixtures if self._mode == "full" else fixtures[:LITE_FIXTURE_LIMIT]

    def _with_logos(
        self, fixtures: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Añade escudos oficiales sólo para presentación."""

        catalogs: dict[str, dict[str, str]] = {}
        for slug in {str(row.get("league_slug")) for row in fixtures}:
            teams = self._gateway.explorer_teams(slug).get("teams", [])
            catalogs[slug] = {
                str(team.get("id")): str(team.get("logo") or "")
                for team in teams if isinstance(team, dict)}
        return [_attach_logos(row, catalogs) for row in fixtures]

    def _freeze(
        self, fixture: dict[str, Any], target: str, now: datetime,
    ) -> FrozenPrediction:
        """Genera la predicción sólo cuando no existe una versión congelada."""

        existing = next((
            row for row in self._repository.predictions()
            if row.fixture_key == _fixture_key(fixture)), None)
        if existing is not None:
            return existing
        prediction = self._gateway.predict_upcoming(_prediction_payload(fixture))
        prediction.setdefault("fixture", _fixture_names(fixture))
        frozen = self._repository.freeze(fixture, prediction, target, now)
        self._repository.freeze_market_snapshot(
            frozen.fixture_key, DISTRIBUTIONAL_CONTRACT_VERSION,
            prediction, now)
        return frozen

    def _results(self, now: datetime) -> int:
        """Publica sólo resultados elegibles y reconciliados."""

        count = 0
        for row in self._repository.predictions():
            key = f"result:{row.fixture_key}"
            if (now < row.kickoff_ts + SETTLEMENT_DELAY
                    or self._repository.publication_exists(key)):
                continue
            result = self._settled_result(row)
            if result is not None:
                count += self._publish(
                    key, "result", _result_text(row, result), now,
                    fixture_key=row.fixture_key, target_date=row.target_date)
        return count

    def _settled_result(self, row: FrozenPrediction) -> dict[str, Any] | None:
        """Exige final, marcador y estadísticas reconciliadas."""

        fixture = self._final_fixture(row)
        if not isinstance(fixture, dict) or not _is_final(fixture):
            return None
        stats = self._gateway.explorer_statistics(
            row.league_slug, str(row.match_id), row.competition_id)
        if not stats.get("reconciled") or not stats.get("score_reconciled"):
            LOGGER.warning("channel_result_rejected fixture=%s reason=reconciliation", row.fixture_key)
            return None
        return stats.get("score") if isinstance(stats.get("score"), dict) else None

    def _final_fixture(self, row: FrozenPrediction) -> dict[str, Any] | None:
        """Busca el evento por fecha México y UTC para cubrir cruces de día."""

        dates = {
            row.kickoff_ts.astimezone(MEXICO_TZ).strftime("%Y%m%d"),
            row.kickoff_ts.strftime("%Y%m%d"),
        }
        for date_text in sorted(dates):
            payload = self._gateway.explorer_fixtures(
                row.league_slug, date_text)
            fixture = next((
                item for item in payload.get("fixtures", [])
                if int(item.get("match_id") or 0) == row.match_id), None)
            if isinstance(fixture, dict):
                return fixture
        return None

    def _publish(
        self, key: str, kind: str, text: str, now: datetime,
        fixture_key: str | None = None, target_date: str | None = None,
    ) -> int:
        """Envía y registra sólo cuando la clave no existe."""

        if self._repository.publication_exists(key):
            return 0
        message_id = self._transport.send_message(text)
        self._repository.record_publication(
            key, kind, message_id, text, now, fixture_key, target_date)
        return 1

    def _publish_card(self, row: FrozenPrediction, now: datetime) -> int:
        """Publica la tarjeta inmediatamente después del aviso diario."""

        key = f"card:{row.fixture_key}"
        if self._repository.publication_exists(key):
            return 0
        text = _card_text(row)
        message_id = self._transport.send_prediction_card(text, _logos(row))
        self._repository.record_publication(
            key, "prematch_card", message_id, text, now,
            row.fixture_key, row.target_date)
        return 1

    def _publish_markets(self, row: FrozenPrediction, now: datetime) -> int:
        """Publica todas las líneas disponibles con etiqueta shadow."""

        current = self._distributional_row(row, now)
        texts = _market_texts(current)
        if not texts:
            return 0
        variable = (
            _has_bounded_grid(current.prediction)
            or _has_recommendations(current.prediction))
        prefix = (f"markets:{MARKET_LAYOUT_VERSION}:{row.fixture_key}"
               if variable else f"markets:{row.fixture_key}")
        return sum(self._publish(
            f"{prefix}:{index}", "prematch_markets", text, now,
            fixture_key=row.fixture_key, target_date=row.target_date)
            for index, text in enumerate(texts))

    def _distributional_row(
        self, row: FrozenPrediction, now: datetime,
    ) -> FrozenPrediction:
        """Recupera o congela mercados variables antes del kickoff."""

        if (_has_bounded_grid(row.prediction)
                and _has_global_market_view(row.prediction)):
            return row
        stored = self._repository.market_snapshot(
            row.fixture_key, DISTRIBUTIONAL_CONTRACT_VERSION)
        if stored is not None:
            return replace(row, prediction=stored)
        if _utc(now) >= row.kickoff_ts:
            return row
        prediction = self._gateway.predict_upcoming(
            _prediction_payload(row.fixture))
        frozen = self._repository.freeze_market_snapshot(
            row.fixture_key, DISTRIBUTIONAL_CONTRACT_VERSION,
            prediction, now)
        return replace(row, prediction=frozen)


def _prediction_row(
    fixture: dict[str, Any], prediction: dict[str, Any],
    target_date: str, frozen_at: datetime,
) -> FrozenChannelPrediction:
    """Construye la fila ORM sin mutar los payloads de entrada."""

    return FrozenChannelPrediction(
        fixture_key=_fixture_key(fixture), target_date=target_date,
        league_slug=str(fixture["league_slug"]), match_id=int(fixture["match_id"]),
        competition_id=str(fixture.get("competition_id") or fixture["match_id"]),
        kickoff_ts=_parse_ts(fixture["kickoff_ts"]), fixture_json=dict(fixture),
        prediction_json=dict(prediction), prediction_hash=canonical_hash(prediction),
        frozen_at=_utc(frozen_at))


def _frozen(row: FrozenChannelPrediction) -> FrozenPrediction:
    """Desacopla una entidad ORM antes de cerrar la sesión."""

    return FrozenPrediction(
        row.fixture_key, row.target_date, row.league_slug, row.match_id,
        row.competition_id, _utc(row.kickoff_ts), dict(row.fixture_json),
        dict(row.prediction_json), row.prediction_hash, _utc(row.frozen_at))


def _fixture_key(fixture: dict[str, Any]) -> str:
    """Compone identidad estable con liga y match ESPN."""

    return f"{fixture.get('league_slug')}:{int(fixture.get('match_id') or 0)}"


def _prediction_payload(fixture: dict[str, Any]) -> dict[str, Any]:
    """Reduce un fixture al contrato oficial de predicción upcoming."""

    return {
        "league_slug": str(fixture["league_slug"]),
        "home_team_id": int(fixture["home_team_id"]),
        "away_team_id": int(fixture["away_team_id"]),
        "kickoff_ts": str(fixture["kickoff_ts"]),
        "match_id": int(fixture["match_id"]),
    }


def _fixture_names(fixture: dict[str, Any]) -> dict[str, Any]:
    """Conserva nombres publicados para las tarjetas."""

    return {
        "home_team_name": fixture.get("home_team_name"),
        "away_team_name": fixture.get("away_team_name"),
    }


def _summary_chunks(
    target: date, rows: list[FrozenPrediction], mode: str,
) -> list[str]:
    """Divide el resumen diario sin exceder el límite conservador."""

    badge = "⚡ MODO LITE · 3 SELECCIONADOS" if mode == "lite" else "🌐 ENTREGA COMPLETA"
    header = "\n".join([
        "✨ <b>DIKAMAHA · AGENDA DE MAÑANA</b>",
        f"📅 {target.strftime('%d/%m/%Y')} · Ciudad de México",
        f"{badge}",
        "━━━━━━━━━━━━━━━━━━━━",
    ])
    if not rows:
        return [header + "\n\nSin partidos disponibles en las ligas cubiertas."]
    lines = [_summary_line(row) for row in rows]
    footer = "\n\n⬇️ <i>Predicciones individuales a continuación.</i>"
    chunks = _chunk_lines(header, lines, 3700)
    chunks[-1] += footer
    return chunks


def _summary_line(row: FrozenPrediction) -> str:
    """Resume horario, equipos y probabilidad 1X2 más alta."""

    home, away = _names(row)
    local = row.kickoff_ts.astimezone(MEXICO_TZ).strftime("%H:%M")
    league = _mobile_label(
        str(row.fixture.get("league_name") or row.league_slug), 36)
    return (f"\n\n🏆 <b>{html.escape(str(league))}</b>\n"
            f"└ 🕘 {local} · {html.escape(home)} vs {html.escape(away)}")


def _card_text(row: FrozenPrediction) -> str:
    """Construye la tarjeta individual previa al kickoff."""

    home, away = _names(row)
    prediction = row.prediction
    local = row.kickoff_ts.astimezone(MEXICO_TZ).strftime("%d/%m · %H:%M")
    label, probability = _top_1x2(prediction, home, away)
    league = row.fixture.get("league_name") or row.league_slug
    return "\n".join([
        "🔮 <b>PRONÓSTICO DIKAMAHA</b>",
        f"⚽ <b>{html.escape(home)}  vs  {html.escape(away)}</b>",
        f"🏆 {html.escape(str(league))} · 🕘 {local}",
        "━━━━━━━━━━━━━━━━━━━━",
        "<b>1X2</b>",
        f"🏠 {html.escape(home)}  {_pct(prediction.get('probability_home'))}",
        f"🤝 Empate  {_pct(prediction.get('probability_draw'))}",
        f"✈️ {html.escape(away)}  {_pct(prediction.get('probability_away'))}",
        "",
        "<b>GOLES</b>",
        f"🎯 Más de 2.5  {_pct(prediction.get('probability_over_2_5'))}",
        f"🥅 Ambos marcan  {_pct(prediction.get('probability_btts'))}",
        "",
        f"📌 <b>Escenario principal:</b> {html.escape(label)} · {probability:.0%}",
        "ℹ️ <i>Probabilidades calculadas antes del inicio.</i>",
    ])


def _market_text(row: FrozenPrediction) -> str | None:
    """Agrupa la vista pública completa de mercados por periodo."""

    block = row.prediction.get("experimental_team_markets")
    source = block if isinstance(block, dict) else {}
    grid = source.get("bounded_market_grid_view")
    bounded = [item for item in grid or [] if isinstance(item, dict)]
    if bounded:
        global_rows = source.get("global_market_view") or []
        return "\n\n".join(_bounded_market_texts(
            row, bounded, [item for item in global_rows if isinstance(item, dict)]))
    recommended = source.get("recommended_market_view")
    selected = [item for item in recommended or [] if isinstance(item, dict)]
    if selected:
        return _recommended_market_text(row, selected)
    values = source.get("user_market_view")
    rows = [item for item in values or [] if isinstance(item, dict)]
    if not rows:
        return None
    home, away = _names(row)
    lines = [
        "📈 <b>MERCADOS DISPONIBLES</b>",
        f"⚽ <b>{html.escape(home)} vs {html.escape(away)}</b>",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    for period in ("first_half", "second_half", "full_match"):
        lines.extend(_period_market_lines(rows, period, home, away))
    lines.extend(["", "ℹ️ <i>Probabilidades calculadas antes del inicio.</i>"])
    return "\n".join(lines)


def _market_texts(row: FrozenPrediction) -> list[str]:
    """Minimiza mensajes sin separar las secciones de un fixture."""

    block = row.prediction.get("experimental_team_markets")
    source = block if isinstance(block, dict) else {}
    grid = source.get("bounded_market_grid_view")
    bounded = [item for item in grid or [] if isinstance(item, dict)]
    if bounded:
        global_rows = source.get("global_market_view") or []
        return _combine_market_cards(_bounded_market_texts(
            row, bounded, [item for item in global_rows if isinstance(item, dict)]))
    text = _market_text(row)
    return [text] if text is not None else []


def _combine_market_cards(cards: list[str]) -> list[str]:
    """Une tarjetas del fixture salvo que superarían el límite Telegram."""

    sections = [_dashboard_section(card, index == len(cards) - 1)
                for index, card in enumerate(cards)]
    combined = "\n\n━━━━━━━━━━━━━━━━━━━━\n\n".join(sections)
    return [combined] if len(combined) <= 3900 else cards


def _dashboard_section(card: str, keep_footer: bool) -> str:
    """Retira metadatos repetidos sin quitar título ni tablas del periodo."""

    output = []
    for line in card.splitlines():
        repeated = line.startswith("⚽ ") or line.startswith("<i>Más / Menos")
        footer = line.startswith("ℹ️ ")
        if repeated or (footer and not keep_footer):
            continue
        output.append(line)
    return "\n".join(output)


def _bounded_market_texts(
    row: FrozenPrediction, groups: list[dict[str, Any]],
    global_rows: list[dict[str, Any]],
) -> list[str]:
    """Construye una tarjeta móvil para cada periodo disponible."""

    home, away = _names(row)
    return [_grid_period_text(groups, period, home, away, global_rows)
            for period in ("first_half", "second_half", "full_match")
            if any(item.get("period") == period for item in groups)]


def _grid_period_text(
    groups: list[dict[str, Any]], period: str, home: str, away: str,
    global_rows: list[dict[str, Any]],
) -> str:
    """Renderiza tablas compactas por equipo para un solo periodo."""

    selected = [row for row in groups if row.get("period") == period]
    labels = {
        "first_half": "1️⃣ <b>PRIMER TIEMPO · MERCADOS</b>",
        "second_half": "2️⃣ <b>SEGUNDO TIEMPO · MERCADOS</b>",
        "full_match": "📈 <b>PARTIDO COMPLETO · MERCADOS</b>",
    }
    lines = [labels[period], f"⚽ <b>{html.escape(home)} vs {html.escape(away)}</b>",
             "<i>Probabilidad de Más / Menos · líneas 1.5–9.5</i>",
             "━━━━━━━━━━━━━━━━━━━━"]
    for side, team in (("home", home), ("away", away), ("total", "Total")):
        team_groups = [item for item in selected if item.get("team_side") == side]
        if team_groups:
            lines.extend(_grid_team_block(team, team_groups))
    if period == "full_match" and global_rows:
        lines.extend(_global_market_block(global_rows))
    lines.extend(["", "ℹ️ <i>Estimaciones pre-partido.</i>"])
    return "\n".join(lines)


def _global_market_block(rows: list[dict[str, Any]]) -> list[str]:
    """Resume los conteos totales y su incertidumbre por mercado."""

    values = [_global_market_line(row) for row in rows]
    return ["", "🌐 <b>PRONÓSTICO GLOBAL · TOTALES</b>", "<pre>" +
            "\n".join(["Mercado       Media Moda Rango 60%", *values]) +
            "</pre>", "<i>Rango central estimado para el partido.</i>"]


def _global_market_line(row: dict[str, Any]) -> str:
    """Formatea media, moda e intervalo central de una PMF total."""

    metric = {"corners": "Corners", "shots": "Tiros", "yellow_cards": "Tarjetas",
              "shots_on_target": "A puerta"}.get(str(row.get("metric")), "Mercado")
    interval = row.get("central_interval_60") or [0, 0]
    return (f"{metric:<12} {float(row.get('expected_count', 0.0)):>4.1f} "
            f"{int(row.get('most_likely_count', 0)):>4} "
            f"{int(interval[0]):>2}–{int(interval[1]):<2}")


def _grid_team_block(team: str, groups: list[dict[str, Any]]) -> list[str]:
    """Agrupa las tablas de corners, tiros y tarjetas de un equipo."""

    return ["", f"🔹 <b>{html.escape(team)}</b>", *[
        _grid_table(row) for row in groups]]


def _grid_table(row: dict[str, Any]) -> str:
    """Presenta tres líneas over/under en una tabla monoespaciada."""

    values = [item for item in row.get("lines", []) if isinstance(item, dict)]
    metric = _metric_label(str(row.get("metric")))
    header = "Línea " + " ".join(f"{item['line']:>5.1f}" for item in values)
    over = "Más   " + " ".join(f"{_pct(item['over_probability']):>5}" for item in values)
    under = "Menos " + " ".join(f"{_pct(item['under_probability']):>5}" for item in values)
    return f"<b>{html.escape(metric)}</b>\n<pre>{header}\n{over}\n{under}</pre>"


def _has_recommendations(prediction: dict[str, Any]) -> bool:
    """Indica si el payload contiene escenarios distribucionales válidos."""

    block = prediction.get("experimental_team_markets")
    source = block if isinstance(block, dict) else {}
    values = source.get("recommended_market_view")
    return any(isinstance(item, dict) for item in values or [])


def _has_bounded_grid(prediction: dict[str, Any]) -> bool:
    """Indica si existe la rejilla acotada de tres líneas."""

    block = prediction.get("experimental_team_markets")
    source = block if isinstance(block, dict) else {}
    values = source.get("bounded_market_grid_view")
    return any(isinstance(item, dict) for item in values or [])


def _has_global_market_view(prediction: dict[str, Any]) -> bool:
    """Indica si el payload contiene los totales agregados coherentemente."""

    block = prediction.get("experimental_team_markets")
    source = block if isinstance(block, dict) else {}
    values = source.get("global_market_view")
    return any(isinstance(item, dict) for item in values or [])


def _recommended_market_text(
    row: FrozenPrediction, recommendations: list[dict[str, Any]],
) -> str:
    """Presenta únicamente los escenarios distribucionales seleccionados."""

    home, away = _names(row)
    lines = [
        "🎯 <b>ESCENARIOS MÁS PROBABLES</b>",
        f"⚽ <b>{html.escape(home)} vs {html.escape(away)}</b>",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    for period in ("first_half", "second_half", "full_match"):
        lines.extend(_recommended_period_lines(
            recommendations, period, home, away))
    lines.extend(["", "ℹ️ <i>Estimaciones pre-partido.</i>"])
    return "\n".join(lines)


def _recommended_period_lines(
    rows: list[dict[str, Any]], period: str, home: str, away: str,
) -> list[str]:
    """Agrupa escenarios seleccionados por periodo."""

    selected = [row for row in rows if row.get("period") == period]
    if not selected:
        return []
    labels = {
        "first_half": "1️⃣ <b>PRIMER TIEMPO</b>",
        "second_half": "2️⃣ <b>SEGUNDO TIEMPO</b>",
        "full_match": "📊 <b>PARTIDO COMPLETO</b>",
    }
    return ["", labels[period], *[
        _recommended_line(row, home, away) for row in selected]]


def _recommended_line(
    row: dict[str, Any], home: str, away: str,
) -> str:
    """Formatea dirección, línea y conteo esperado."""

    metric = _metric_label(str(row.get("metric")))
    team = {
        "home": home, "away": away, "total": "Total",
    }.get(str(row.get("team_side")), "Total")
    direction = "Más" if row.get("direction") == "over" else "Menos"
    probability = _pct(row.get("probability"))
    expected = float(row.get("expected_count") or 0.0)
    label = f"{metric} · {team} · {direction} de {row.get('line')}"
    return (f"• {html.escape(label)} · <b>{probability}</b> "
            f"<i>(media esperada {expected:.1f})</i>")


def _period_market_lines(
    rows: list[dict[str, Any]], period: str, home: str, away: str,
) -> list[str]:
    """Renderiza todas las líneas disponibles de un periodo."""

    selected = [row for row in rows if row.get("period") == period]
    if not selected:
        return []
    labels = {
        "first_half": "1️⃣ <b>PRIMER TIEMPO</b>",
        "second_half": "2️⃣ <b>SEGUNDO TIEMPO</b>",
        "full_match": "📊 <b>PARTIDO COMPLETO</b>",
    }
    output = ["", labels[period]]
    output.extend(_market_line(row, home, away) for row in selected)
    return output


def _market_line(row: dict[str, Any], home: str, away: str) -> str:
    """Formatea equipo, línea y probabilidad pública."""

    label = _market_label(row, home, away)
    model = _pct(row.get("probability"))
    return f"• {html.escape(label)} · <b>{model}</b>"


def _market_label(row: dict[str, Any], home: str, away: str) -> str:
    """Construye una etiqueta comercial sin local/visitante."""

    metric = _metric_label(str(row.get("metric")))
    team = {
        "home": home, "away": away, "total": "Total",
    }.get(str(row.get("team_side")), str(row.get("team_side") or ""))
    return f"{metric} · {team} · Más de {row.get('line')}"


def _metric_label(metric: str) -> str:
    """Traduce métricas distribucionales a etiquetas visibles."""

    return {
        "corners": "Corners", "shots": "Tiros",
        "shots_on_target": "Tiros a puerta",
        "yellow_cards": "Tarjetas amarillas",
        "red_cards": "Tarjetas rojas",
    }.get(metric, metric or "Mercado")


def _attach_logos(
    fixture: dict[str, Any], catalogs: dict[str, dict[str, str]],
) -> dict[str, Any]:
    """Adjunta URLs de escudos sin introducirlas al payload del modelo."""

    catalog = catalogs.get(str(fixture.get("league_slug")), {})
    return {
        **fixture,
        "home_team_logo": catalog.get(str(fixture.get("home_team_id")), ""),
        "away_team_logo": catalog.get(str(fixture.get("away_team_id")), ""),
    }


def _logos(row: FrozenPrediction) -> list[str]:
    """Obtiene los dos escudos asociados al fixture congelado."""

    return [
        str(row.fixture.get("home_team_logo") or ""),
        str(row.fixture.get("away_team_logo") or ""),
    ]


def _channel_mode(value: str) -> str:
    """Valida el interruptor de entrega completa o lite."""

    normalized = value.strip().casefold()
    if normalized not in CHANNEL_MODES:
        raise ValueError("telegram_channel_mode_invalid")
    return normalized


def _result_text(row: FrozenPrediction, score: dict[str, Any]) -> str:
    """Compara el resultado reconciliado con tres decisiones pre-match."""

    home, away = _names(row)
    home_score, away_score = int(score["home"]), int(score["away"])
    actual = home if home_score > away_score else away if away_score > home_score else "Empate"
    predicted, _ = _top_1x2(row.prediction, home, away)
    goals, btts = home_score + away_score, home_score > 0 and away_score > 0
    return "\n".join([
        "✅ <b>RESULTADO FINAL VERIFICADO</b>",
        f"<b>{html.escape(home)} {home_score}–{away_score} {html.escape(away)}</b>", "",
        f"1X2 previsto: {html.escape(predicted)} · {_mark(predicted == actual)}",
        f"Más de 2.5 previsto: {_yes(row.prediction.get('probability_over_2_5'))} · "
        f"{_mark((_value(row.prediction.get('probability_over_2_5')) >= 0.5) == (goals > 2))}",
        f"Ambos marcan previsto: {_yes(row.prediction.get('probability_btts'))} · "
        f"{_mark((_value(row.prediction.get('probability_btts')) >= 0.5) == btts)}", "",
        f"<i>Predicción congelada antes del kickoff · {row.prediction_hash[:10]}</i>",
    ])


def _names(row: FrozenPrediction) -> tuple[str, str]:
    """Obtiene nombres legibles desde fixture o predicción."""

    nested = row.prediction.get("fixture") or {}
    home = row.fixture.get("home_team_name") or nested.get("home_team_name") or "Equipo 1"
    away = row.fixture.get("away_team_name") or nested.get("away_team_name") or "Equipo 2"
    return _mobile_label(str(home), 28), _mobile_label(str(away), 28)


def _mobile_label(value: str, limit: int) -> str:
    """Normaliza espacios y acota etiquetas variables para pantalla móvil."""

    clean = " ".join(value.split())
    return clean if len(clean) <= limit else clean[:limit - 1].rstrip() + "…"


def _top_1x2(
    prediction: dict[str, Any], home: str, away: str,
) -> tuple[str, float]:
    """Selecciona sólo la probabilidad máxima, sin denominarla apuesta."""

    values = {
        home: _value(prediction.get("probability_home")),
        "Empate": _value(prediction.get("probability_draw")),
        away: _value(prediction.get("probability_away")),
    }
    return max(values.items(), key=lambda item: item[1])


def _chunk_lines(header: str, lines: list[str], limit: int) -> list[str]:
    """Agrupa líneas completas bajo un límite fijo."""

    chunks, current = [], header
    for line in lines:
        if len(current) + len(line) > limit:
            chunks.append(current)
            current = header + line
        else:
            current += line
    chunks.append(current)
    return chunks


def _is_final(fixture: dict[str, Any]) -> bool:
    """Reconoce sólo estados finales explícitos del scoreboard."""

    value = str(fixture.get("status_detail") or "").casefold()
    return any(token in value for token in ("final", "full time", "ft"))


def _yes(value: Any) -> str:
    """Convierte una probabilidad a una decisión binaria visible."""

    return "Sí" if _value(value) >= 0.5 else "No"


def _mark(value: bool) -> str:
    """Etiqueta acierto o fallo sin lenguaje de rentabilidad."""

    return "✅ acertado" if value else "❌ no acertado"


def _pct(value: Any) -> str:
    """Formatea una probabilidad segura."""

    return f"{_value(value):.0%}"


def _value(value: Any) -> float:
    """Convierte a probabilidad acotada con fallback seguro."""

    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _parse_ts(value: Any) -> datetime:
    """Parsea ISO-8601 y exige zona horaria."""

    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return _utc(parsed)


def _utc(value: datetime) -> datetime:
    """Normaliza un instante consciente a UTC."""

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


# Version: 1.9.0
# Created: 2026-07-29
