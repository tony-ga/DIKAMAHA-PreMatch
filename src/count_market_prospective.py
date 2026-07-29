"""Persistencia append-only para predicciones prospectivas de conteo.

Requirements:
    sqlalchemy>=2

Version: 1.0.0
Created: 2026-07-28
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String, UniqueConstraint, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


class CountCohortBase(DeclarativeBase):
    """Base ORM aislada de Fase 86."""


class CountMarketPredictionRow(CountCohortBase):
    """Predicción inmutable materializada antes del kickoff."""

    __tablename__ = "count_market_predictions"
    __table_args__ = (
        UniqueConstraint("league_slug", "match_id",
                         name="uq_count_prediction_fixture"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_slug: Mapped[str] = mapped_column(String(100), nullable=False)
    match_id: Mapped[int] = mapped_column(Integer, nullable=False)
    kickoff_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    home_team_id: Mapped[int] = mapped_column(Integer, nullable=False)
    away_team_id: Mapped[int] = mapped_column(Integer, nullable=False)
    probabilities: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    baseline_probabilities: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    model_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)


class CountMarketOutcomeRow(CountCohortBase):
    """Outcome post-match reconciliado e inmutable."""

    __tablename__ = "count_market_outcomes"
    __table_args__ = (
        UniqueConstraint("league_slug", "match_id",
                         name="uq_count_outcome_fixture"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_slug: Mapped[str] = mapped_column(String(100), nullable=False)
    match_id: Mapped[int] = mapped_column(Integer, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    counts: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    outcomes: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    summary_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    plays_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    reconciliation_status: Mapped[str] = mapped_column(String(30), nullable=False)


@dataclass(frozen=True, slots=True)
class FrozenCountPrediction:
    """Comando de persistencia de una predicción prospectiva."""

    league_slug: str
    match_id: int
    kickoff_ts: datetime
    captured_at: datetime
    home_team_id: int
    away_team_id: int
    probabilities: dict[str, float]
    baseline_probabilities: dict[str, float]
    model_sha256: str
    snapshot_sha256: str
    status: str


@dataclass(frozen=True, slots=True)
class FrozenCountOutcome:
    """Comando reconciliado para un outcome post-match."""

    league_slug: str
    match_id: int
    captured_at: datetime
    counts: dict[str, int]
    outcomes: dict[str, bool]
    summary_sha256: str
    plays_sha256: str
    reconciliation_status: str


class CountPredictionRepository(ABC):
    """Puerto append-only para la cohorte."""

    @abstractmethod
    def add_if_absent(self, prediction: FrozenCountPrediction) -> bool:
        """Inserta una predicción nueva o conserva la ya congelada."""

    @abstractmethod
    def all(self) -> list[FrozenCountPrediction]:
        """Lee la cohorte sin outcomes."""


class CountOutcomeRepository(ABC):
    """Puerto append-only para outcomes reconciliados."""

    @abstractmethod
    def add_if_absent(self, outcome: FrozenCountOutcome) -> bool:
        """Inserta un outcome o conserva el existente."""

    @abstractmethod
    def all(self) -> list[FrozenCountOutcome]:
        """Lee outcomes sin alterar predicciones."""


class SqlAlchemyCountPredictionRepository(CountPredictionRepository):
    """Adaptador SQLAlchemy transaccional."""

    def __init__(self, factory: sessionmaker[Session]) -> None:
        """Recibe una fábrica de sesiones."""

        self._factory = factory

    def add_if_absent(self, prediction: FrozenCountPrediction) -> bool:
        """Inserta con unicidad por fixture sin sobrescribir."""

        try:
            with self._factory() as session:
                with session.begin():
                    existing = session.execute(self._query(
                        prediction.league_slug, prediction.match_id)).first()
                    if existing is not None:
                        return False
                    session.add(CountMarketPredictionRow(**_values(prediction)))
            return True
        except SQLAlchemyError:
            raise

    def all(self) -> list[FrozenCountPrediction]:
        """Devuelve la cohorte ordenada causalmente."""

        with self._factory() as session:
            statement = select(CountMarketPredictionRow).order_by(
                CountMarketPredictionRow.kickoff_ts,
                CountMarketPredictionRow.match_id)
            rows = session.execute(statement).scalars().all()
        return [_frozen(row) for row in rows]

    @staticmethod
    def _query(league_slug: str, match_id: int) -> Any:
        """Construye la consulta de identidad."""

        return select(CountMarketPredictionRow.id).where(
            CountMarketPredictionRow.league_slug == league_slug,
            CountMarketPredictionRow.match_id == match_id)


class SqlAlchemyCountOutcomeRepository(CountOutcomeRepository):
    """Adaptador SQLAlchemy append-only de outcomes."""

    def __init__(self, factory: sessionmaker[Session]) -> None:
        """Recibe la misma fábrica aislada de la cohorte."""

        self._factory = factory

    def add_if_absent(self, outcome: FrozenCountOutcome) -> bool:
        """Inserta sin sobrescribir un outcome existente."""

        with self._factory() as session:
            with session.begin():
                existing = session.execute(select(
                    CountMarketOutcomeRow.id).where(
                        CountMarketOutcomeRow.league_slug == outcome.league_slug,
                        CountMarketOutcomeRow.match_id == outcome.match_id,
                    )).first()
                if existing is not None:
                    return False
                session.add(CountMarketOutcomeRow(**_outcome_values(outcome)))
        return True

    def all(self) -> list[FrozenCountOutcome]:
        """Devuelve outcomes ordenados por identidad."""

        with self._factory() as session:
            statement = select(CountMarketOutcomeRow).order_by(
                CountMarketOutcomeRow.league_slug,
                CountMarketOutcomeRow.match_id)
            rows = session.execute(statement).scalars().all()
        return [_outcome(row) for row in rows]


def _utc(value: datetime) -> datetime:
    """Normaliza timestamps conscientes."""

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _values(prediction: FrozenCountPrediction) -> dict[str, Any]:
    """Convierte el comando al esquema ORM."""

    return {
        "league_slug": prediction.league_slug,
        "match_id": prediction.match_id,
        "kickoff_ts": _utc(prediction.kickoff_ts),
        "captured_at": _utc(prediction.captured_at),
        "home_team_id": prediction.home_team_id,
        "away_team_id": prediction.away_team_id,
        "probabilities": prediction.probabilities,
        "baseline_probabilities": prediction.baseline_probabilities,
        "model_sha256": prediction.model_sha256,
        "snapshot_sha256": prediction.snapshot_sha256,
        "status": prediction.status,
    }


def _frozen(row: CountMarketPredictionRow) -> FrozenCountPrediction:
    """Desacopla una fila ORM."""

    return FrozenCountPrediction(
        row.league_slug, row.match_id, _utc(row.kickoff_ts),
        _utc(row.captured_at), row.home_team_id, row.away_team_id,
        {key: float(value) for key, value in row.probabilities.items()},
        {key: float(value)
         for key, value in row.baseline_probabilities.items()},
        row.model_sha256, row.snapshot_sha256, row.status)


def _outcome_values(outcome: FrozenCountOutcome) -> dict[str, Any]:
    """Convierte el outcome al esquema ORM."""

    return {
        "league_slug": outcome.league_slug, "match_id": outcome.match_id,
        "captured_at": _utc(outcome.captured_at), "counts": outcome.counts,
        "outcomes": outcome.outcomes,
        "summary_sha256": outcome.summary_sha256,
        "plays_sha256": outcome.plays_sha256,
        "reconciliation_status": outcome.reconciliation_status,
    }


def _outcome(row: CountMarketOutcomeRow) -> FrozenCountOutcome:
    """Desacopla una fila de outcome."""

    return FrozenCountOutcome(
        row.league_slug, row.match_id, _utc(row.captured_at),
        {key: int(value) for key, value in row.counts.items()},
        {key: bool(value) for key, value in row.outcomes.items()},
        row.summary_sha256, row.plays_sha256, row.reconciliation_status)


# Version: 1.0.0
# Created: 2026-07-28
