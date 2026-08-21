"""Congelación y liquidación prospectiva del Constructor de Parlays (Fase 136).

Store propio, separado del de Fase 123. La razón no es organizativa: un pick
suelto se valida por sí mismo, mientras que **un parlay sólo se puede validar
como conjunto**. El número que Fase 135 promete al usuario -el ratio de entrega,
`0.94–0.97` fuera de muestra- no se deriva de la calibración de cada pierna por
separado, porque el error se compone al multiplicar. Hace falta registrar qué
piernas formaron cada parlay y si el parlay entero se cumplió.

De ahí las cuatro tablas: dos para las piernas -congelada y liquidada, con la
forma que ya usa Fase 123- y dos para los parlays de referencia, que son
combinaciones materializadas **antes del kickoff** para poder medir contra ellas
sin que nadie elija a posteriori qué combinación mirar. Es el mismo principio
que Fase 86 aplica al baseline: si la combinación se decidiera después de
conocer resultados, el ratio medido no significaría nada.

Un parlay se liquida sólo cuando **todas** sus piernas están liquidadas; hasta
entonces queda pendiente, nunca se resuelve por mayoría ni se descarta la pierna
que falta.

Version: 1.0.0
Created: 2026-08-21
"""

from __future__ import annotations

import hashlib
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    create_engine,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.types import JSON

try:
    from src.settlement_store import observed_team_count, team_market_hit
except ModuleNotFoundError:  # pragma: no cover - ejecución directa desde src
    from settlement_store import observed_team_count, team_market_hit

LOGGER = logging.getLogger(__name__)
STATUS = "experimental_shadow_not_promoted"
REFERENCE_LEG_COUNTS = (2, 3, 4, 5)


class ParlayBase(DeclarativeBase):
    """Base ORM aislada de Fase 136."""


class ParlayLegFreeze(ParlayBase):
    """Pierna elegible congelada antes del kickoff de su partido."""

    __tablename__ = "parlay_leg_freezes"

    leg_key: Mapped[str] = mapped_column(String(220), primary_key=True)
    fixture_key: Mapped[str] = mapped_column(String(160), nullable=False)
    league_slug: Mapped[str] = mapped_column(String(100), nullable=False)
    match_id: Mapped[int] = mapped_column(Integer, nullable=False)
    kickoff_ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False)
    market: Mapped[str] = mapped_column(String(80), nullable=False)
    direction: Mapped[str] = mapped_column(String(20), nullable=False)
    metric: Mapped[str] = mapped_column(String(40), nullable=False)
    team_side: Mapped[str] = mapped_column(String(20), nullable=False)
    period: Mapped[str] = mapped_column(String(20), nullable=False)
    line: Mapped[float] = mapped_column(Float, nullable=False)
    model_probability: Mapped[float] = mapped_column(Float, nullable=False)
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    criteria_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    frozen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False)


class ParlayLegSettlement(ParlayBase):
    """Veredicto sellado de una pierna ya liquidada."""

    __tablename__ = "parlay_leg_settlements"

    leg_key: Mapped[str] = mapped_column(String(220), primary_key=True)
    fixture_key: Mapped[str] = mapped_column(String(160), nullable=False)
    hit: Mapped[bool] = mapped_column(Boolean, nullable=False)
    observed_value: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=dict)
    settlement_source: Mapped[str] = mapped_column(String(40), nullable=False)
    settled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False)


class ParlayFreeze(ParlayBase):
    """Parlay de referencia materializado antes de cualquier kickoff."""

    __tablename__ = "parlay_freezes"

    parlay_key: Mapped[str] = mapped_column(String(220), primary_key=True)
    leg_count: Mapped[int] = mapped_column(Integer, nullable=False)
    leg_keys: Mapped[list[str]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=list)
    declared_probability: Mapped[float] = mapped_column(Float, nullable=False)
    declared_delivery_ratio: Mapped[float | None] = mapped_column(
        Float, nullable=True)
    earliest_kickoff_ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False)
    criteria_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    frozen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False)


class ParlaySettlement(ParlayBase):
    """Veredicto de un parlay cuyas piernas están todas liquidadas."""

    __tablename__ = "parlay_settlements"

    parlay_key: Mapped[str] = mapped_column(String(220), primary_key=True)
    hit: Mapped[bool] = mapped_column(Boolean, nullable=False)
    legs_hit: Mapped[int] = mapped_column(Integer, nullable=False)
    leg_count: Mapped[int] = mapped_column(Integer, nullable=False)
    settled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False)


@dataclass(frozen=True, slots=True)
class LegFreezeRecord:
    """Vista desacoplada de una pierna congelada."""

    leg_key: str
    fixture_key: str
    league_slug: str
    match_id: int
    kickoff_ts: datetime
    market: str
    direction: str
    metric: str
    team_side: str
    period: str
    line: float
    model_probability: float
    threshold: float
    criteria_sha256: str
    frozen_at: datetime


@dataclass(frozen=True, slots=True)
class LegSettlementRecord:
    """Vista desacoplada del veredicto de una pierna."""

    leg_key: str
    fixture_key: str
    hit: bool
    observed_value: dict[str, Any] = field(default_factory=dict)
    settlement_source: str = "team_market_statistics"
    settled_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True, slots=True)
class ParlayFreezeRecord:
    """Vista desacoplada de un parlay de referencia congelado."""

    parlay_key: str
    leg_count: int
    leg_keys: list[str]
    declared_probability: float
    declared_delivery_ratio: float | None
    earliest_kickoff_ts: datetime
    criteria_sha256: str
    frozen_at: datetime


@dataclass(frozen=True, slots=True)
class ParlaySettlementRecord:
    """Vista desacoplada del veredicto de un parlay."""

    parlay_key: str
    hit: bool
    legs_hit: int
    leg_count: int
    settled_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ParlayRepository(ABC):
    """Puerto append-only del store de parlays."""

    @abstractmethod
    def freeze_leg_if_absent(self, record: LegFreezeRecord) -> bool:
        """Congela una pierna una sola vez."""

    @abstractmethod
    def settle_leg_if_absent(self, record: LegSettlementRecord) -> bool:
        """Sella el veredicto de una pierna una sola vez."""

    @abstractmethod
    def freeze_parlay_if_absent(self, record: ParlayFreezeRecord) -> bool:
        """Congela un parlay de referencia una sola vez."""

    @abstractmethod
    def settle_parlay_if_absent(self, record: ParlaySettlementRecord) -> bool:
        """Sella el veredicto de un parlay una sola vez."""

    @abstractmethod
    def unsettled_legs(self, now: datetime) -> list[LegFreezeRecord]:
        """Piernas ya jugadas y todavía sin veredicto, más antigua primero."""

    @abstractmethod
    def legs_frozen_today(self, day: str) -> list[LegFreezeRecord]:
        """Piernas congeladas cuya fecha de congelación es la indicada."""

    @abstractmethod
    def unsettled_parlays(self) -> list[ParlayFreezeRecord]:
        """Parlays de referencia todavía sin veredicto."""

    @abstractmethod
    def leg_settlements(self, leg_keys: list[str]) -> dict[str, LegSettlementRecord]:
        """Veredictos ya sellados de las piernas indicadas."""

    @abstractmethod
    def frozen_parlays(self) -> list[ParlayFreezeRecord]:
        """Todos los parlays de referencia congelados."""

    @abstractmethod
    def parlay_settlements(self) -> list[ParlaySettlementRecord]:
        """Todos los veredictos de parlay sellados."""


class SqlAlchemyParlayRepository(ParlayRepository):
    """Adaptador SQLAlchemy compatible con PostgreSQL y SQLite."""

    def __init__(self, factory: sessionmaker[Session]) -> None:
        """Recibe una fábrica de sesiones ya configurada."""

        self._factory = factory

    def _insert_once(self, model: Any, primary: Any, values: dict[str, Any]) -> bool:
        """Inserta si la clave no existe todavía; nunca sobrescribe."""

        with self._factory() as session:
            with session.begin():
                if session.get(model, primary) is not None:
                    return False
                session.add(model(**values))
        return True

    def freeze_leg_if_absent(self, record: LegFreezeRecord) -> bool:
        """Congela una pierna, conservando la ya sellada."""

        return self._insert_once(ParlayLegFreeze, record.leg_key, {
            "leg_key": record.leg_key, "fixture_key": record.fixture_key,
            "league_slug": record.league_slug, "match_id": int(record.match_id),
            "kickoff_ts": record.kickoff_ts, "market": record.market,
            "direction": record.direction, "metric": record.metric,
            "team_side": record.team_side, "period": record.period,
            "line": float(record.line),
            "model_probability": float(record.model_probability),
            "threshold": float(record.threshold),
            "criteria_sha256": record.criteria_sha256,
            "frozen_at": record.frozen_at,
        })

    def settle_leg_if_absent(self, record: LegSettlementRecord) -> bool:
        """Sella el veredicto de una pierna."""

        return self._insert_once(ParlayLegSettlement, record.leg_key, {
            "leg_key": record.leg_key, "fixture_key": record.fixture_key,
            "hit": bool(record.hit),
            "observed_value": dict(record.observed_value),
            "settlement_source": record.settlement_source,
            "settled_at": record.settled_at,
        })

    def freeze_parlay_if_absent(self, record: ParlayFreezeRecord) -> bool:
        """Congela un parlay de referencia."""

        return self._insert_once(ParlayFreeze, record.parlay_key, {
            "parlay_key": record.parlay_key, "leg_count": int(record.leg_count),
            "leg_keys": list(record.leg_keys),
            "declared_probability": float(record.declared_probability),
            "declared_delivery_ratio": (
                None if record.declared_delivery_ratio is None
                else float(record.declared_delivery_ratio)),
            "earliest_kickoff_ts": record.earliest_kickoff_ts,
            "criteria_sha256": record.criteria_sha256,
            "frozen_at": record.frozen_at,
        })

    def settle_parlay_if_absent(self, record: ParlaySettlementRecord) -> bool:
        """Sella el veredicto de un parlay."""

        return self._insert_once(ParlaySettlement, record.parlay_key, {
            "parlay_key": record.parlay_key, "hit": bool(record.hit),
            "legs_hit": int(record.legs_hit), "leg_count": int(record.leg_count),
            "settled_at": record.settled_at,
        })

    def unsettled_legs(self, now: datetime) -> list[LegFreezeRecord]:
        """Piernas cuyo partido ya arrancó y siguen sin veredicto."""

        statement = (
            select(ParlayLegFreeze)
            .where(
                ParlayLegFreeze.kickoff_ts <= now,
                ParlayLegFreeze.leg_key.not_in(select(ParlayLegSettlement.leg_key)))
            .order_by(ParlayLegFreeze.kickoff_ts.asc()))
        with self._factory() as session:
            return [_leg(row) for row in session.execute(statement).scalars()]

    def legs_frozen_today(self, day: str) -> list[LegFreezeRecord]:
        """Piernas congeladas en la fecha indicada, por kickoff ascendente."""

        statement = select(ParlayLegFreeze).order_by(
            ParlayLegFreeze.kickoff_ts.asc(), ParlayLegFreeze.leg_key.asc())
        with self._factory() as session:
            rows = [_leg(row) for row in session.execute(statement).scalars()]
        return [row for row in rows if row.frozen_at.date().isoformat() == day]

    def unsettled_parlays(self) -> list[ParlayFreezeRecord]:
        """Parlays de referencia todavía sin veredicto."""

        statement = (
            select(ParlayFreeze)
            .where(ParlayFreeze.parlay_key.not_in(select(ParlaySettlement.parlay_key)))
            .order_by(ParlayFreeze.earliest_kickoff_ts.asc()))
        with self._factory() as session:
            return [_parlay(row) for row in session.execute(statement).scalars()]

    def leg_settlements(self, leg_keys: list[str]) -> dict[str, LegSettlementRecord]:
        """Veredictos sellados de las piernas indicadas."""

        if not leg_keys:
            return {}
        statement = select(ParlayLegSettlement).where(
            ParlayLegSettlement.leg_key.in_(list(leg_keys)))
        with self._factory() as session:
            return {row.leg_key: _leg_settlement(row)
                    for row in session.execute(statement).scalars()}

    def frozen_parlays(self) -> list[ParlayFreezeRecord]:
        """Todos los parlays de referencia congelados."""

        statement = select(ParlayFreeze).order_by(
            ParlayFreeze.earliest_kickoff_ts.asc())
        with self._factory() as session:
            return [_parlay(row) for row in session.execute(statement).scalars()]

    def parlay_settlements(self) -> list[ParlaySettlementRecord]:
        """Todos los veredictos de parlay sellados."""

        with self._factory() as session:
            return [_parlay_settlement(row)
                    for row in session.execute(select(ParlaySettlement)).scalars()]


def _leg(row: ParlayLegFreeze) -> LegFreezeRecord:
    """Convierte una fila ORM de pierna en su vista inmutable."""

    return LegFreezeRecord(
        leg_key=row.leg_key, fixture_key=row.fixture_key,
        league_slug=row.league_slug, match_id=int(row.match_id),
        kickoff_ts=row.kickoff_ts, market=row.market, direction=row.direction,
        metric=row.metric, team_side=row.team_side, period=row.period,
        line=float(row.line), model_probability=float(row.model_probability),
        threshold=float(row.threshold), criteria_sha256=row.criteria_sha256,
        frozen_at=row.frozen_at)


def _leg_settlement(row: ParlayLegSettlement) -> LegSettlementRecord:
    """Convierte una fila ORM de veredicto de pierna."""

    return LegSettlementRecord(
        leg_key=row.leg_key, fixture_key=row.fixture_key, hit=bool(row.hit),
        observed_value=dict(row.observed_value or {}),
        settlement_source=row.settlement_source, settled_at=row.settled_at)


def _parlay(row: ParlayFreeze) -> ParlayFreezeRecord:
    """Convierte una fila ORM de parlay congelado."""

    return ParlayFreezeRecord(
        parlay_key=row.parlay_key, leg_count=int(row.leg_count),
        leg_keys=list(row.leg_keys or []),
        declared_probability=float(row.declared_probability),
        declared_delivery_ratio=(
            None if row.declared_delivery_ratio is None
            else float(row.declared_delivery_ratio)),
        earliest_kickoff_ts=row.earliest_kickoff_ts,
        criteria_sha256=row.criteria_sha256, frozen_at=row.frozen_at)


def _parlay_settlement(row: ParlaySettlement) -> ParlaySettlementRecord:
    """Convierte una fila ORM de veredicto de parlay."""

    return ParlaySettlementRecord(
        parlay_key=row.parlay_key, hit=bool(row.hit),
        legs_hit=int(row.legs_hit), leg_count=int(row.leg_count),
        settled_at=row.settled_at)


def build_repository(database_url: str) -> SqlAlchemyParlayRepository:
    """Crea el repositorio y asegura sus tablas sin tocar otras fases."""

    engine = create_engine(database_url, future=True, pool_pre_ping=True)
    ParlayBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    return SqlAlchemyParlayRepository(factory)


# -- congelación ----------------------------------------------------------

def leg_key(fixture_key: str, market: str, line: float) -> str:
    """Identidad estable de una pierna dentro de su partido."""

    digest = hashlib.sha256(
        f"{fixture_key}|{market}|{line}".encode("utf-8")).hexdigest()[:16]
    return f"{fixture_key}:{market}:{digest}"


def parlay_key(leg_keys: list[str]) -> str:
    """Identidad estable de una combinación, independiente del orden.

    Ordenar antes de hashear evita que la misma combinación se congele dos
    veces por haber llegado en distinto orden.
    """

    joined = "|".join(sorted(leg_keys))
    return f"parlay:{hashlib.sha256(joined.encode('utf-8')).hexdigest()[:32]}"


def freeze_from_leg(
    leg: dict[str, Any], fixture: dict[str, Any], criteria_sha256: str,
    now: datetime,
) -> LegFreezeRecord:
    """Construye la pierna congelada desde la salida del gate de Fase 135."""

    kickoff = fixture["kickoff_ts"]
    if isinstance(kickoff, str):
        kickoff = datetime.fromisoformat(kickoff.replace("Z", "+00:00"))
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=timezone.utc)
    return LegFreezeRecord(
        leg_key=leg_key(str(fixture["fixture_key"]), str(leg["key"]),
                        float(leg["line"])),
        fixture_key=str(fixture["fixture_key"]),
        league_slug=str(fixture["league_slug"]),
        match_id=int(fixture["match_id"]),
        kickoff_ts=kickoff,
        market=str(leg["key"]), direction=str(leg.get("direction", "over")),
        metric=str(leg["metric"]), team_side=str(leg["team_side"]),
        period=str(leg["period"]), line=float(leg["line"]),
        model_probability=float(leg["probability"]),
        threshold=float(leg["threshold"]),
        criteria_sha256=str(criteria_sha256), frozen_at=now)


def reference_parlays(
    legs: list[LegFreezeRecord], delivery: dict[str, float],
    criteria_sha256: str, now: datetime,
    leg_counts: tuple[int, ...] = REFERENCE_LEG_COUNTS,
) -> list[ParlayFreezeRecord]:
    """Materializa combinaciones de referencia antes de cualquier kickoff.

    Determinista y sin ninguna elección posterior: se ordenan las piernas por
    kickoff y clave -no por probabilidad, que sesgaría hacia las más altas- y
    se toman bloques consecutivos disjuntos de cada tamaño. Cada bloque
    respeta la regla de una pierna por partido descartando repeticiones de
    `fixture_key`.

    Las combinaciones se fijan aquí, antes de conocer ningún resultado, porque
    elegir después qué parlay mirar convertiría el ratio medido en una cifra
    sin significado.
    """

    ordered = sorted(legs, key=lambda row: (row.kickoff_ts, row.leg_key))
    output: list[ParlayFreezeRecord] = []
    for count in leg_counts:
        used: set[str] = set()
        block: list[LegFreezeRecord] = []
        for leg in ordered:
            if leg.fixture_key in used:
                continue
            block.append(leg)
            used.add(leg.fixture_key)
            if len(block) < count:
                continue
            keys = [row.leg_key for row in block]
            declared = 1.0
            for row in block:
                declared *= row.model_probability
            output.append(ParlayFreezeRecord(
                parlay_key=parlay_key(keys), leg_count=count, leg_keys=keys,
                declared_probability=declared,
                declared_delivery_ratio=delivery.get(str(count)),
                earliest_kickoff_ts=min(row.kickoff_ts for row in block),
                criteria_sha256=criteria_sha256, frozen_at=now))
            block = []
            used = set()
    return output


def resolve_leg(
    leg: LegFreezeRecord, statistics: dict[str, Any], settled_at: datetime,
) -> LegSettlementRecord | None:
    """Liquida una pierna contra `explorer_statistics` ya reconciliado.

    El llamador garantiza la reconciliación -en la práctica, que
    `prediction_settlements` ya tenga fila para este `fixture_key`, lo que
    certifica estado final, marcador reconciliado y `kickoff + 3h`-. Reutiliza
    `team_market_hit` y `observed_team_count`, las mismas reglas que liquidan
    los picks de Fase 123, para que ambos no puedan divergir.
    """

    periods = statistics.get("periods")
    if not isinstance(periods, dict):
        return None
    observed = observed_team_count(
        periods, leg.team_side, leg.period, leg.metric)
    if observed is None:
        return None
    return LegSettlementRecord(
        leg_key=leg.leg_key, fixture_key=leg.fixture_key,
        hit=team_market_hit(leg.direction, leg.line, float(observed)),
        observed_value={"observed": float(observed), "line": leg.line},
        settled_at=settled_at)


def settle_ready_parlays(
    repository: ParlayRepository, now: datetime,
) -> dict[str, int]:
    """Liquida los parlays cuyas piernas están todas resueltas.

    Un parlay con una sola pierna pendiente sigue pendiente: no se resuelve
    por mayoría ni se descarta la que falta. Un parlay al que ya le falló una
    pierna **tampoco** se cierra antes de tiempo, porque el conteo `legs_hit`
    forma parte de la evidencia y exige el veredicto completo.
    """

    settled = pending = 0
    for parlay in repository.unsettled_parlays():
        verdicts = repository.leg_settlements(parlay.leg_keys)
        if len(verdicts) < len(parlay.leg_keys):
            pending += 1
            continue
        hits = sum(1 for key in parlay.leg_keys if verdicts[key].hit)
        if repository.settle_parlay_if_absent(ParlaySettlementRecord(
            parlay_key=parlay.parlay_key, hit=hits == len(parlay.leg_keys),
            legs_hit=hits, leg_count=len(parlay.leg_keys), settled_at=now,
        )):
            settled += 1
    return {"settled": settled, "still_pending": pending}


def prospective_delivery(
    frozen: list[ParlayFreezeRecord], settled: list[ParlaySettlementRecord],
    minimum_sample: int = 30,
) -> dict[str, Any]:
    """Contrasta lo declarado contra lo entregado, por número de piernas.

    Oculta el ratio mientras la muestra no alcance `minimum_sample`, igual que
    `settlement_store.track_record` oculta el porcentaje: con cinco parlays
    liquidados cualquier ratio es ruido, y publicarlo invitaría a leerlo como
    confirmación de Fase 135.
    """

    verdicts = {row.parlay_key: row for row in settled}
    grouped: dict[int, list[tuple[float, bool]]] = {}
    for parlay in frozen:
        verdict = verdicts.get(parlay.parlay_key)
        if verdict is None:
            continue
        grouped.setdefault(parlay.leg_count, []).append(
            (parlay.declared_probability, verdict.hit))
    output: dict[str, Any] = {}
    for count, rows in sorted(grouped.items()):
        declared = sum(value for value, _ in rows) / len(rows)
        observed = sum(1 for _, hit in rows if hit) / len(rows)
        block: dict[str, Any] = {
            "parlays": len(rows), "declared": declared, "observed": observed,
            "sufficient_sample": len(rows) >= minimum_sample,
            "minimum_sample": minimum_sample,
        }
        if len(rows) >= minimum_sample and declared > 0:
            block["delivery_ratio"] = observed / declared
        else:
            block["missing_for_ratio"] = max(0, minimum_sample - len(rows))
        output[str(count)] = block
    return {
        "status": STATUS,
        "settled_parlays": len(verdicts),
        "frozen_parlays": len(frozen),
        "by_legs": output,
    }


# Version: 1.0.0
# Created: 2026-08-21
