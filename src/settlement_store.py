"""Persistencia append-only del veredicto por mercado ya liquidado.

El publicador escribe aquí el resultado reconciliado de una predicción
congelada antes del kickoff. El agregado que consume la Mini App se deriva de
esta tabla y nunca filtra por acierto: DEC-101 prohíbe seleccionar partidos por
desempeño, de modo que la ventana es siempre cronológica y contiene fallos.

Version: 1.0.0
Created: 2026-08-10
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import (
    BigInteger,
    DateTime,
    Integer,
    String,
    create_engine,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.types import JSON

OFFICIAL_MARKETS = ("one_x_two", "over_2_5", "btts")
MINIMUM_SAMPLE = 20
DEFAULT_WINDOW = 30
MAXIMUM_WINDOW = 200


class SettlementBase(DeclarativeBase):
    """Base ORM aislada de Fase 118."""


class PredictionSettlement(SettlementBase):
    """Veredicto sellado de un partido ya reconciliado."""

    __tablename__ = "prediction_settlements"

    fixture_key: Mapped[str] = mapped_column(String(160), primary_key=True)
    league_slug: Mapped[str] = mapped_column(String(100), nullable=False)
    match_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    competition_id: Mapped[str] = mapped_column(String(100), nullable=False)
    kickoff_ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False)
    settled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False)
    home_team_name: Mapped[str] = mapped_column(String(160), nullable=False)
    away_team_name: Mapped[str] = mapped_column(String(160), nullable=False)
    score_home: Mapped[int] = mapped_column(Integer, nullable=False)
    score_away: Mapped[int] = mapped_column(Integer, nullable=False)
    prediction_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    official_verdicts: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=dict)
    shadow_verdicts: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=dict)
    contract_version: Mapped[str | None] = mapped_column(String(60))


@dataclass(frozen=True, slots=True)
class SettlementRecord:
    """Vista desacoplada de un veredicto sellado."""

    fixture_key: str
    league_slug: str
    match_id: int
    competition_id: str
    kickoff_ts: datetime
    settled_at: datetime
    home_team_name: str
    away_team_name: str
    score_home: int
    score_away: int
    prediction_hash: str
    official_verdicts: dict[str, Any] = field(default_factory=dict)
    shadow_verdicts: dict[str, Any] = field(default_factory=dict)
    contract_version: str | None = None


class SettlementRepository(ABC):
    """Puerto append-only de veredictos liquidados."""

    @abstractmethod
    def add_if_absent(self, record: SettlementRecord) -> bool:
        """Inserta un veredicto nuevo y conserva el ya sellado."""

    @abstractmethod
    def recent(self, window: int) -> list[SettlementRecord]:
        """Devuelve la cola cronológica más reciente, sin filtrar por acierto."""

    @abstractmethod
    def on_date(self, target: date, tz: ZoneInfo) -> list[SettlementRecord]:
        """Devuelve los partidos de un día calendario local, sin filtrar por acierto.

        El día se define por la fecha local del kickoff, no por `settled_at`,
        para que "los partidos del día" coincida con lo que un usuario
        reconoce como la jornada, incluso si la liquidación cruzó la
        medianoche por la ventana de `kickoff + 3h`.
        """

    @abstractmethod
    def get(self, fixture_key: str) -> SettlementRecord | None:
        """Busca el veredicto sellado de un fixture puntual, si ya existe."""


class SqlAlchemySettlementRepository(SettlementRepository):
    """Adaptador SQLAlchemy compatible con PostgreSQL y SQLite."""

    def __init__(self, factory: sessionmaker[Session]) -> None:
        """Recibe una fábrica de sesiones ya configurada."""

        self._factory = factory

    def add_if_absent(self, record: SettlementRecord) -> bool:
        """Sella el veredicto una sola vez por fixture."""

        values = {
            "fixture_key": record.fixture_key,
            "league_slug": record.league_slug,
            "match_id": int(record.match_id),
            "competition_id": record.competition_id,
            "kickoff_ts": record.kickoff_ts,
            "settled_at": record.settled_at,
            "home_team_name": record.home_team_name,
            "away_team_name": record.away_team_name,
            "score_home": int(record.score_home),
            "score_away": int(record.score_away),
            "prediction_hash": record.prediction_hash,
            "official_verdicts": dict(record.official_verdicts),
            "shadow_verdicts": dict(record.shadow_verdicts),
            "contract_version": record.contract_version,
        }
        with self._factory() as session:
            with session.begin():
                existing = session.get(
                    PredictionSettlement, record.fixture_key)
                if existing is not None:
                    return False
                session.add(PredictionSettlement(**values))
        return True

    def recent(self, window: int) -> list[SettlementRecord]:
        """Lee los N más recientes por kickoff, sin ningún filtro adicional."""

        limit = max(1, min(int(window), MAXIMUM_WINDOW))
        statement = select(PredictionSettlement).order_by(
            PredictionSettlement.kickoff_ts.desc()).limit(limit)
        with self._factory() as session:
            return [_record(row) for row in session.execute(statement).scalars()]

    def on_date(self, target: date, tz: ZoneInfo) -> list[SettlementRecord]:
        """Filtra por fecha local de kickoff, cronológico y completo."""

        start_local = datetime.combine(target, time.min, tzinfo=tz)
        end_local = start_local + timedelta(days=1)
        statement = select(PredictionSettlement).where(
            PredictionSettlement.kickoff_ts >= start_local.astimezone(timezone.utc),
            PredictionSettlement.kickoff_ts < end_local.astimezone(timezone.utc),
        ).order_by(PredictionSettlement.kickoff_ts.asc())
        with self._factory() as session:
            return [_record(row) for row in session.execute(statement).scalars()]

    def get(self, fixture_key: str) -> SettlementRecord | None:
        """Lee por clave primaria; ya reconciliado si la fila existe."""

        with self._factory() as session:
            row = session.get(PredictionSettlement, fixture_key)
            return _record(row) if row is not None else None


def _record(row: PredictionSettlement) -> SettlementRecord:
    """Convierte una fila ORM en la vista inmutable."""

    return SettlementRecord(
        fixture_key=row.fixture_key,
        league_slug=row.league_slug,
        match_id=int(row.match_id),
        competition_id=row.competition_id,
        kickoff_ts=row.kickoff_ts,
        settled_at=row.settled_at,
        home_team_name=row.home_team_name,
        away_team_name=row.away_team_name,
        score_home=int(row.score_home),
        score_away=int(row.score_away),
        prediction_hash=row.prediction_hash,
        official_verdicts=dict(row.official_verdicts or {}),
        shadow_verdicts=dict(row.shadow_verdicts or {}),
        contract_version=row.contract_version,
    )


def build_repository(database_url: str) -> SqlAlchemySettlementRepository:
    """Crea el repositorio y asegura la tabla sin tocar otras fases."""

    engine = create_engine(database_url, future=True, pool_pre_ping=True)
    SettlementBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    return SqlAlchemySettlementRepository(factory)


def team_market_hit(direction: str, line: float, observed: float) -> bool:
    """Decide el acierto de una línea de equipo contra el conteo observado.

    Regla única compartida por `_shadow_verdicts` (rejilla dinámica de Fase
    102) y por la liquidación de picks de Fase 123 (línea fija de
    `MARKET_METADATA`): ambas comparan el mismo tipo de línea contra el mismo
    tipo de conteo, sólo cambia de dónde sale la línea.
    """

    actual_over = observed > line
    return (direction == "over") == actual_over


def wilson_interval(hits: int, total: int) -> tuple[float, float]:
    """Calcula el intervalo de Wilson al 95%, estable con muestras chicas."""

    if total <= 0:
        return (0.0, 0.0)
    z = 1.959963984540054
    rate = hits / total
    denominator = 1.0 + z * z / total
    centre = rate + z * z / (2.0 * total)
    spread = z * math.sqrt(
        rate * (1.0 - rate) / total + z * z / (4.0 * total * total))
    lower = (centre - spread) / denominator
    upper = (centre + spread) / denominator
    return (max(0.0, lower), min(1.0, upper))


def _market_summary(
    verdicts: list[dict[str, Any]], baseline_hits: int,
) -> dict[str, Any]:
    """Agrega un mercado y oculta el porcentaje sin muestra suficiente."""

    total = len(verdicts)
    hits = sum(1 for row in verdicts if bool(row.get("hit")))
    summary: dict[str, Any] = {
        "hits": hits,
        "total": total,
        "sufficient_sample": total >= MINIMUM_SAMPLE,
        "minimum_sample": MINIMUM_SAMPLE,
    }
    if total >= MINIMUM_SAMPLE:
        lower, upper = wilson_interval(hits, total)
        summary["rate"] = hits / total
        summary["interval_95"] = [lower, upper]
        summary["baseline_rate"] = baseline_hits / total
    else:
        summary["missing_for_rate"] = MINIMUM_SAMPLE - total
    return summary


def track_record(records: list[SettlementRecord]) -> dict[str, Any]:
    """Construye el agregado publicable desde la cola cronológica completa."""

    official: dict[str, Any] = {}
    for market in OFFICIAL_MARKETS:
        verdicts = [
            row.official_verdicts[market] for row in records
            if isinstance(row.official_verdicts.get(market), dict)
        ]
        official[market] = _market_summary(verdicts, _majority_hits(verdicts))
    shadow_keys = sorted({
        key for row in records for key in row.shadow_verdicts
    })
    shadow_markets = {}
    for key in shadow_keys:
        verdicts = [
            row.shadow_verdicts[key] for row in records
            if isinstance(row.shadow_verdicts.get(key), dict)
        ]
        shadow_markets[key] = {
            "hits": sum(1 for row in verdicts if bool(row.get("hit"))),
            "total": len(verdicts),
        }
    ordered = sorted(records, key=lambda row: row.kickoff_ts, reverse=True)
    return {
        "window": {
            "available": len(records),
            "from": ordered[-1].kickoff_ts.isoformat() if ordered else None,
            "to": ordered[0].kickoff_ts.isoformat() if ordered else None,
        },
        "official": official,
        "shadow": {
            "status": "experimental_not_promoted",
            "markets": shadow_markets,
        },
        "matches": [_match_row(row) for row in ordered],
        "disclosure": (
            "Cada predicción se congeló y publicó antes del kickoff. Se "
            "muestran todos los partidos verificados del periodo, acertados y "
            "no acertados."
        ),
    }


def _majority_hits(verdicts: list[dict[str, Any]]) -> int:
    """Cuenta los aciertos que lograría apostar siempre la clase mayoritaria.

    Es la referencia mínima honesta: sin ella un 58% parece bueno aunque esté
    por debajo de acertar siempre el resultado más frecuente del periodo.
    """

    if not verdicts:
        return 0
    counts: dict[str, int] = {}
    for row in verdicts:
        actual = str(row.get("actual"))
        counts[actual] = counts.get(actual, 0) + 1
    return max(counts.values())


def _match_row(row: SettlementRecord) -> dict[str, Any]:
    """Expone un partido liquidado con su huella verificable."""

    return {
        "fixture_key": row.fixture_key,
        "league_slug": row.league_slug,
        "match_id": row.match_id,
        "kickoff_ts": row.kickoff_ts.isoformat(),
        "home_team_name": row.home_team_name,
        "away_team_name": row.away_team_name,
        "score_home": row.score_home,
        "score_away": row.score_away,
        "prediction_hash": row.prediction_hash[:10],
        "official_verdicts": row.official_verdicts,
        "shadow_verdicts": row.shadow_verdicts,
    }
