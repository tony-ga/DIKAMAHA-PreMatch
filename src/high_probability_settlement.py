"""Congela y liquida los picks del menú de mayor probabilidad (Fase 123).

Fase 122 expone `GET /v1/high-probability` con evidencia histórica post-hoc:
el gate v2 que aprueba sus nueve celdas se re-especificó después de ver el
resultado de v1, y el holdout de 270 partidos es un subconjunto de la misma
cohorte, no una muestra independiente. Este módulo convierte esa evidencia en
confirmación prospectiva real: congela exactamente los picks que el menú
mostró un día dado, antes del kickoff de cada partido, y liquida cada uno
después contra el resultado real.

Reutiliza el pipeline de settlement ya existente en vez de duplicarlo. Un
partido sólo se liquida aquí cuando `prediction_settlements` (Fase 118, vía
`TelegramChannelPublisher._seal_settlement`) ya tiene una fila para su
`fixture_key` -eso ya certifica estado final, marcador reconciliado y
`kickoff + 3h`, así que Fase 123 no repite esa comprobación-. Para 1X2, Over
2.5 y BTTS el veredicto sale directo de `official_verdicts`. Para los
mercados de equipo el veredicto NO puede leerse de `shadow_verdicts`: esa
liquidación corre sobre la rejilla dinámica de Fase 102
(`bounded_market_grid_view`, líneas centradas en P(over)≈50% por partido),
mientras que el menú usa la línea fija de `MARKET_METADATA` (Fase 84A/88/89).
Ambas vistas comparten el mismo motor de probabilidad, pero no garantizan la
misma línea exacta, así que este módulo liquida los mercados de equipo con su
propia lectura de `explorer_statistics`, usando la misma regla de acierto
(`team_market_hit`, `src/settlement_store.py`) que ya usa `_shadow_verdicts`.

Version: 1.0.0
Created: 2026-08-12
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Boolean, DateTime, Float, Integer, String, create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.types import JSON

try:
    from src.ladder_audit import METRIC_LADDERS
    from src.prematch_raw_store import canonical_hash
    from src.settlement_store import (
        MINIMUM_SAMPLE, SettlementRecord, team_market_hit, wilson_interval,
    )
    from src.team_count_market_runtime import MARKET_METADATA
    from src.telegram_bot import PredictionGatewayError
except ModuleNotFoundError:  # pragma: no cover - ejecución directa desde src
    from ladder_audit import METRIC_LADDERS
    from prematch_raw_store import canonical_hash
    from settlement_store import (
        MINIMUM_SAMPLE, SettlementRecord, team_market_hit, wilson_interval,
    )
    from team_count_market_runtime import MARKET_METADATA
    from telegram_bot import PredictionGatewayError

GOAL_MARKET_KEYS = {"1x2": "one_x_two", "over_2_5": "over_2_5", "btts": "btts"}
MAXIMUM_WINDOW = 500
_LADDER_BASE_METRICS = frozenset(base for base, _ in METRIC_LADDERS.values())
_LADDER_SIDES = frozenset({"home", "away", "total"})


class PickSettlementBase(DeclarativeBase):
    """Base ORM aislada de Fase 123, independiente de Fase 118."""


class HighProbabilityPickFreeze(PickSettlementBase):
    """Pick congelado antes del kickoff, tal como lo vio el usuario."""

    __tablename__ = "high_probability_pick_freezes"

    pick_key: Mapped[str] = mapped_column(String(220), primary_key=True)
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
    line: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_probability: Mapped[float] = mapped_column(Float, nullable=False)
    observed_rate_declared: Mapped[float] = mapped_column(Float, nullable=False)
    sample_size_declared: Mapped[int] = mapped_column(Integer, nullable=False)
    edge_source: Mapped[str] = mapped_column(String(40), nullable=False)
    bucket_low: Mapped[float] = mapped_column(Float, nullable=False)
    bucket_high: Mapped[float] = mapped_column(Float, nullable=False)
    eligibility_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    prediction_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    frozen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False)


class HighProbabilityPickSettlement(PickSettlementBase):
    """Veredicto sellado de un pick ya liquidado."""

    __tablename__ = "high_probability_pick_settlements"

    pick_key: Mapped[str] = mapped_column(String(220), primary_key=True)
    fixture_key: Mapped[str] = mapped_column(String(160), nullable=False)
    hit: Mapped[bool] = mapped_column(Boolean, nullable=False)
    observed_value: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=dict)
    settlement_source: Mapped[str] = mapped_column(String(40), nullable=False)
    settled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False)


@dataclass(frozen=True, slots=True)
class PickFreezeRecord:
    """Vista desacoplada de un pick congelado."""

    pick_key: str
    fixture_key: str
    league_slug: str
    match_id: int
    kickoff_ts: datetime
    market: str
    direction: str
    metric: str
    team_side: str
    period: str
    line: float | None
    model_probability: float
    observed_rate_declared: float
    sample_size_declared: int
    edge_source: str
    bucket_low: float
    bucket_high: float
    eligibility_sha256: str
    prediction_hash: str
    frozen_at: datetime


@dataclass(frozen=True, slots=True)
class PickSettlementRecord:
    """Vista desacoplada de un veredicto de pick ya liquidado."""

    pick_key: str
    fixture_key: str
    hit: bool
    observed_value: dict[str, Any] = field(default_factory=dict)
    settlement_source: str = "official_verdict"
    settled_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class HighProbabilityPickRepository(ABC):
    """Puerto de congelación y liquidación de picks prospectivos."""

    @abstractmethod
    def freeze_if_absent(self, record: PickFreezeRecord) -> bool:
        """Congela un pick una sola vez, identificado por `pick_key`."""

    @abstractmethod
    def frozen_on_date(self, target: date, tz: ZoneInfo) -> list[PickFreezeRecord]:
        """Lista los picks cuyo kickoff cae en un día calendario local."""

    @abstractmethod
    def unsettled(self, before: datetime) -> list[PickFreezeRecord]:
        """Picks congelados con kickoff anterior a `before` y sin liquidar."""

    @abstractmethod
    def settle_if_absent(self, record: PickSettlementRecord) -> bool:
        """Sella el veredicto de un pick una sola vez."""

    @abstractmethod
    def settled_recent(self, window: int) -> list[PickSettlementRecord]:
        """Devuelve los veredictos más recientes, cronológicos y completos."""


class SqlAlchemyHighProbabilityPickRepository(HighProbabilityPickRepository):
    """Adaptador SQLAlchemy compatible con PostgreSQL y SQLite."""

    def __init__(self, factory: sessionmaker[Session]) -> None:
        """Recibe una fábrica de sesiones ya configurada."""

        self._factory = factory

    def freeze_if_absent(self, record: PickFreezeRecord) -> bool:
        """Inserta el pick si `pick_key` no existe todavía."""

        with self._factory() as session:
            with session.begin():
                if session.get(
                        HighProbabilityPickFreeze, record.pick_key) is not None:
                    return False
                session.add(HighProbabilityPickFreeze(
                    pick_key=record.pick_key,
                    fixture_key=record.fixture_key,
                    league_slug=record.league_slug,
                    match_id=int(record.match_id),
                    kickoff_ts=record.kickoff_ts,
                    market=record.market,
                    direction=record.direction,
                    metric=record.metric,
                    team_side=record.team_side,
                    period=record.period,
                    line=record.line,
                    model_probability=record.model_probability,
                    observed_rate_declared=record.observed_rate_declared,
                    sample_size_declared=int(record.sample_size_declared),
                    edge_source=record.edge_source,
                    bucket_low=record.bucket_low,
                    bucket_high=record.bucket_high,
                    eligibility_sha256=record.eligibility_sha256,
                    prediction_hash=record.prediction_hash,
                    frozen_at=record.frozen_at,
                ))
        return True

    def frozen_on_date(self, target: date, tz: ZoneInfo) -> list[PickFreezeRecord]:
        """Filtra por fecha local de kickoff, igual que `SettlementRepository.on_date`."""

        start_local = datetime.combine(target, time.min, tzinfo=tz)
        end_local = start_local + timedelta(days=1)
        statement = select(HighProbabilityPickFreeze).where(
            HighProbabilityPickFreeze.kickoff_ts >= start_local.astimezone(timezone.utc),
            HighProbabilityPickFreeze.kickoff_ts < end_local.astimezone(timezone.utc),
        ).order_by(HighProbabilityPickFreeze.kickoff_ts.asc())
        with self._factory() as session:
            return [_freeze_record(row) for row in session.execute(statement).scalars()]

    def unsettled(self, before: datetime) -> list[PickFreezeRecord]:
        """Picks cuyo kickoff ya pasó y que no tienen fila en settlements."""

        settled = select(HighProbabilityPickSettlement.pick_key)
        statement = select(HighProbabilityPickFreeze).where(
            HighProbabilityPickFreeze.kickoff_ts < before,
            HighProbabilityPickFreeze.pick_key.not_in(settled),
        ).order_by(HighProbabilityPickFreeze.kickoff_ts.asc()).limit(MAXIMUM_WINDOW)
        with self._factory() as session:
            return [_freeze_record(row) for row in session.execute(statement).scalars()]

    def settle_if_absent(self, record: PickSettlementRecord) -> bool:
        """Sella el veredicto una sola vez por `pick_key`."""

        with self._factory() as session:
            with session.begin():
                if session.get(
                        HighProbabilityPickSettlement, record.pick_key) is not None:
                    return False
                session.add(HighProbabilityPickSettlement(
                    pick_key=record.pick_key,
                    fixture_key=record.fixture_key,
                    hit=bool(record.hit),
                    observed_value=dict(record.observed_value),
                    settlement_source=record.settlement_source,
                    settled_at=record.settled_at,
                ))
        return True

    def settled_recent(self, window: int) -> list[PickSettlementRecord]:
        """Lee los N veredictos más recientes por instante de liquidación."""

        limit = max(1, min(int(window), MAXIMUM_WINDOW))
        statement = select(HighProbabilityPickSettlement).order_by(
            HighProbabilityPickSettlement.settled_at.desc()).limit(limit)
        with self._factory() as session:
            return [_settlement_record(row) for row in session.execute(statement).scalars()]


def _freeze_record(row: HighProbabilityPickFreeze) -> PickFreezeRecord:
    """Convierte una fila ORM de congelación en la vista inmutable."""

    return PickFreezeRecord(
        pick_key=row.pick_key, fixture_key=row.fixture_key,
        league_slug=row.league_slug, match_id=int(row.match_id),
        kickoff_ts=row.kickoff_ts, market=row.market, direction=row.direction,
        metric=row.metric, team_side=row.team_side, period=row.period,
        line=row.line, model_probability=row.model_probability,
        observed_rate_declared=row.observed_rate_declared,
        sample_size_declared=int(row.sample_size_declared),
        edge_source=row.edge_source, bucket_low=row.bucket_low,
        bucket_high=row.bucket_high, eligibility_sha256=row.eligibility_sha256,
        prediction_hash=row.prediction_hash, frozen_at=row.frozen_at,
    )


def _settlement_record(row: HighProbabilityPickSettlement) -> PickSettlementRecord:
    """Convierte una fila ORM de liquidación en la vista inmutable."""

    return PickSettlementRecord(
        pick_key=row.pick_key, fixture_key=row.fixture_key, hit=bool(row.hit),
        observed_value=dict(row.observed_value or {}),
        settlement_source=row.settlement_source, settled_at=row.settled_at,
    )


def build_repository(database_url: str) -> SqlAlchemyHighProbabilityPickRepository:
    """Crea el repositorio y asegura sus tablas sin tocar otras fases."""

    engine = create_engine(database_url, future=True, pool_pre_ping=True)
    PickSettlementBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    return SqlAlchemyHighProbabilityPickRepository(factory)


def fixture_key(league_slug: str, match_id: int) -> str:
    """Construye la misma clave que ya usa `TelegramChannelPublisher`."""

    return f"{league_slug}:{match_id}"


def pick_key(key_fixture: str, pick: dict[str, Any]) -> str:
    """Construye una clave determinista por pick dentro de un fixture."""

    line = pick.get("line")
    line_token = "na" if line is None else str(float(line)).replace(".", "_")
    return ":".join([
        key_fixture, str(pick["market"]), str(pick["team_side"]),
        str(pick["period"]), line_token, str(pick["direction"]),
    ])


def freeze_from_pick(
    pick: dict[str, Any], fixture: dict[str, Any], eligibility_sha256: str,
    now: datetime,
) -> PickFreezeRecord:
    """Construye el registro de congelación desde un pick de `/v1/high-probability`.

    `pick` y `fixture` son exactamente los dicts que ya devuelve el endpoint
    (`HighProbabilityView.picks()` + `_high_probability_fixture()`); no se
    recalcula nada, sólo se congela lo que el usuario vio.
    """

    key_fixture = fixture_key(str(fixture["league_slug"]), int(fixture["match_id"]))
    frozen_hash = canonical_hash({"pick": pick, "fixture": fixture})
    return PickFreezeRecord(
        pick_key=pick_key(key_fixture, pick), fixture_key=key_fixture,
        league_slug=str(fixture["league_slug"]), match_id=int(fixture["match_id"]),
        kickoff_ts=_parse_ts(fixture["kickoff_ts"]),
        market=str(pick["market"]), direction=str(pick["direction"]),
        metric=str(pick["metric"]), team_side=str(pick["team_side"]),
        period=str(pick["period"]),
        line=None if pick.get("line") is None else float(pick["line"]),
        model_probability=float(pick["model_probability"]),
        observed_rate_declared=float(pick["observed_rate"]),
        sample_size_declared=int(pick["sample_size"]),
        edge_source=str(pick["edge_source"]),
        bucket_low=float(pick["bucket"][0]), bucket_high=float(pick["bucket"][1]),
        eligibility_sha256=eligibility_sha256, prediction_hash=frozen_hash,
        frozen_at=now,
    )


def _parse_ts(value: Any) -> datetime:
    """Normaliza el timestamp de kickoff a `datetime` UTC-aware."""

    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("high_probability_kickoff_naive")
    return parsed.astimezone(timezone.utc)


def resolve_goal_market(
    pick: PickFreezeRecord, settlement: SettlementRecord,
) -> PickSettlementRecord | None:
    """Liquida un pick de 1X2/Over 2.5/BTTS contra el veredicto ya sellado."""

    key = GOAL_MARKET_KEYS.get(pick.market)
    if key is None:
        return None
    verdict = settlement.official_verdicts.get(key)
    if not isinstance(verdict, dict) or "hit" not in verdict:
        return None
    return PickSettlementRecord(
        pick_key=pick.pick_key, fixture_key=pick.fixture_key,
        hit=bool(verdict["hit"]), observed_value={"verdict": verdict},
        settlement_source="official_verdict", settled_at=settlement.settled_at,
    )


def resolve_team_market(
    pick: PickFreezeRecord, statistics: dict[str, Any], settled_at: datetime,
) -> PickSettlementRecord | None:
    """Liquida un pick de equipo contra `explorer_statistics` ya reconciliado.

    El llamador debe garantizar reconciliación -en la práctica, que
    `prediction_settlements` ya tenga una fila para este `fixture_key`, lo
    que certifica estado final y marcador reconciliado (Fase 118)-. Este
    resolutor no repite esa comprobación.

    Acepta dos orígenes de pick: las líneas fijas heredadas de
    `MARKET_METADATA` (Fase 84A/88/89, `market` es la clave del catálogo
    fijo) y las de la escalera auditada (Etapa 4, `market` es la clave del
    grupo, p.ej. `"home_corners"`, con línea variable por partido). No hace
    falta distinguirlas para liquidar: ambas ya traen `metric`/`team_side`/
    `period`/`line` como columnas propias e independientes del string
    `market`, y la regla de acierto es la misma (`team_market_hit`). La
    validación sólo existe para rechazar un `market` que no sea ninguna de
    las dos cosas -nunca liquidar un string inventado-.
    """

    is_legacy_fixed_line = pick.market in MARKET_METADATA
    is_ladder_market = (
        pick.metric in _LADDER_BASE_METRICS and pick.team_side in _LADDER_SIDES)
    if not is_legacy_fixed_line and not is_ladder_market:
        return None
    periods = statistics.get("periods")
    if not isinstance(periods, dict):
        return None
    counts = periods.get(pick.team_side)
    observed = (counts or {}).get(pick.period, {}).get(pick.metric)
    if not isinstance(observed, (int, float)) or pick.line is None:
        return None
    hit = team_market_hit(pick.direction, pick.line, float(observed))
    return PickSettlementRecord(
        pick_key=pick.pick_key, fixture_key=pick.fixture_key, hit=hit,
        observed_value={"observed": float(observed), "line": pick.line},
        settlement_source="team_market_statistics", settled_at=settled_at,
    )


def run_freeze_cycle(
    gateway: Any, repository: HighProbabilityPickRepository,
    date: str | None, now: datetime,
) -> dict[str, int]:
    """Congela los picks vigentes cuyo fixture todavía no arranca.

    Compartida por el runner standalone de Fase 123 y por la integración
    dentro del proceso del publicador del canal -misma regla, un solo lugar-.
    """

    response = gateway.high_probability(date=date, limit=30)
    picks = response.get("picks") or []
    eligibility_sha256 = str(
        (response.get("provenance") or {}).get("eligibility_sha256") or "")
    frozen = skipped_started = skipped_invalid = 0
    for pick in picks:
        fixture = pick.get("fixture")
        if not isinstance(fixture, dict):
            skipped_invalid += 1
            continue
        try:
            record = freeze_from_pick(pick, fixture, eligibility_sha256, now)
        except (KeyError, TypeError, ValueError):
            skipped_invalid += 1
            continue
        if record.kickoff_ts <= now:
            skipped_started += 1
            continue
        if repository.freeze_if_absent(record):
            frozen += 1
    return {
        "frozen": frozen, "skipped_started": skipped_started,
        "skipped_invalid": skipped_invalid, "candidates": len(picks),
    }


def run_settle_cycle(
    gateway: Any, repository: HighProbabilityPickRepository,
    settlements: Any, now: datetime,
) -> dict[str, int]:
    """Liquida los picks cuyo fixture ya está sellado en `prediction_settlements`."""

    settled = still_pending = failed = 0
    for pick in repository.unsettled(now):
        settlement = settlements.get(pick.fixture_key)
        if settlement is None:
            still_pending += 1
            continue
        record = resolve_goal_market(pick, settlement)
        if record is None:
            try:
                statistics = gateway.explorer_statistics(
                    pick.league_slug, str(pick.match_id),
                    settlement.competition_id)
            except PredictionGatewayError:
                failed += 1
                continue
            record = resolve_team_market(pick, statistics, settlement.settled_at)
        if record is None:
            failed += 1
            continue
        if repository.settle_if_absent(record):
            settled += 1
    return {"settled": settled, "still_pending": still_pending, "failed": failed}


def run_prospective_cycle(
    gateway: Any, repository: HighProbabilityPickRepository,
    settlements: Any, date: str | None = None,
) -> dict[str, Any]:
    """Ejecuta congelación y liquidación con instante UTC explícito.

    `settlements=None` congela igual pero omite la liquidación -degradación
    segura cuando no hay `DATABASE_URL` configurada, igual que Fase 118-.
    """

    now = datetime.now(timezone.utc)
    freeze_counts = run_freeze_cycle(gateway, repository, date, now)
    settle_counts = (
        run_settle_cycle(gateway, repository, settlements, now)
        if settlements is not None
        else {"settled": 0, "still_pending": 0, "failed": 0})
    return {"freeze": freeze_counts, "settle": settle_counts}


def prospective_reliability(
    frozen: list[PickFreezeRecord], settled: list[PickSettlementRecord],
) -> dict[str, Any]:
    """Compara la tasa observada prospectiva contra la declarada por Fase 122.

    Agrupa por `(market, bucket_low, bucket_high)` -el mismo tramo de
    confianza que gobierna el gate v2 post-hoc- y oculta la tasa mientras la
    muestra no alcance `MINIMUM_SAMPLE`, igual que hace `settlement_store`
    para el track record oficial. No decide promoción ni gate: sólo publica
    la cifra comparable para que una decisión posterior la use.
    """

    settled_by_key = {row.pick_key: row for row in settled}
    groups: dict[tuple[str, float, float], list[bool]] = {}
    declared: dict[tuple[str, float, float], list[float]] = {}
    for pick in frozen:
        outcome = settled_by_key.get(pick.pick_key)
        if outcome is None:
            continue
        cell = (pick.market, pick.bucket_low, pick.bucket_high)
        groups.setdefault(cell, []).append(outcome.hit)
        declared.setdefault(cell, []).append(pick.observed_rate_declared)
    cells: list[dict[str, Any]] = []
    for (market, low, high), hits in sorted(groups.items()):
        total = len(hits)
        entry: dict[str, Any] = {
            "market": market, "bucket_low": low, "bucket_high": high,
            "total": total, "sufficient_sample": total >= MINIMUM_SAMPLE,
            "minimum_sample": MINIMUM_SAMPLE,
            "declared_rate": sum(declared[(market, low, high)]) / total,
        }
        if total >= MINIMUM_SAMPLE:
            hit_count = sum(1 for value in hits if value)
            lower, upper = wilson_interval(hit_count, total)
            entry["observed_rate_prospective"] = hit_count / total
            entry["interval_95"] = [lower, upper]
        else:
            entry["missing_for_rate"] = MINIMUM_SAMPLE - total
        cells.append(entry)
    return {
        "cells": cells,
        "total_frozen": len(frozen),
        "total_settled": len(settled_by_key),
        "disclosure": (
            "Compara la tasa observada declarada por el gate v2 post-hoc de "
            "Fase 122 contra la tasa real de picks congelados antes del "
            "kickoff y liquidados después. No constituye gate ni promoción."
        ),
    }


# Version: 1.0.0
# Created: 2026-08-12
