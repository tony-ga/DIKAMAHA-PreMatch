"""Gestor de base de datos para el sistema predictivo."""

from __future__ import annotations

import logging
import os
from datetime import datetime, date
from typing import Any, Callable, Optional, TypeVar

from dotenv import load_dotenv
from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    selectinload,
    sessionmaker,
)

load_dotenv()

logger = logging.getLogger("db_manager")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)

T = TypeVar("T")


class DatabaseConnectionError(RuntimeError):
    """Se lanza cuando la conexión con PostgreSQL falla."""


class Base(DeclarativeBase):
    """Base declarativa para todos los modelos ORM."""


class Team(Base):
    """Modelo ORM para la tabla teams."""

    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    city: Mapped[str] = mapped_column(String(150), nullable=False)
    stadium: Mapped[str] = mapped_column(String(150), nullable=False)
    altitude: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    foundation_year: Mapped[int] = mapped_column(Integer, nullable=False)
    espn_team_id: Mapped[Optional[int]] = mapped_column(Integer)

    home_matches: Mapped[list["Match"]] = relationship(
        back_populates="home_team",
        foreign_keys="Match.home_team_id",
    )
    away_matches: Mapped[list["Match"]] = relationship(
        back_populates="away_team",
        foreign_keys="Match.away_team_id",
    )
    players: Mapped[list["Player"]] = relationship(back_populates="team", cascade="all, delete-orphan")


class Match(Base):
    """Modelo ORM para la tabla matches."""

    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    match_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    season: Mapped[str] = mapped_column(String(20), nullable=False)
    home_score: Mapped[Optional[int]] = mapped_column(Integer)
    away_score: Mapped[Optional[int]] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), nullable=False)

    home_team: Mapped["Team"] = relationship(foreign_keys=[home_team_id], back_populates="home_matches")
    away_team: Mapped["Team"] = relationship(foreign_keys=[away_team_id], back_populates="away_matches")
    events: Mapped[list["EventTimeline"]] = relationship(back_populates="match", cascade="all, delete-orphan")
    external_factors: Mapped[list["ExternalFactor"]] = relationship(back_populates="match", cascade="all, delete-orphan")
    state_transitions: Mapped[list["StateTransition"]] = relationship(back_populates="match", cascade="all, delete-orphan")
    raw_responses: Mapped[list["RawApiResponse"]] = relationship(back_populates="match", cascade="all, delete-orphan")
    processed_controls: Mapped[list["ProcessedMatchControl"]] = relationship(back_populates="match", cascade="all, delete-orphan")


class Player(Base):
    """Modelo ORM para la tabla players."""

    __tablename__ = "players"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    height_cm: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    position: Mapped[str] = mapped_column(String(60), nullable=False)
    birth_date: Mapped[Optional[date]] = mapped_column(Date)

    team: Mapped["Team"] = relationship(back_populates="players")


class EventTimeline(Base):
    """Modelo ORM para la tabla events_timeline."""

    __tablename__ = "events_timeline"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), nullable=False)
    minute: Mapped[int] = mapped_column(Integer, nullable=False)
    second: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    team_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teams.id", ondelete="SET NULL"))
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    player_name: Mapped[Optional[str]] = mapped_column(String(200))
    assist_name: Mapped[Optional[str]] = mapped_column(String(200))

    match: Mapped["Match"] = relationship(back_populates="events")


class ExternalFactor(Base):
    """Modelo ORM para la tabla external_factors."""

    __tablename__ = "external_factors"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), nullable=False)
    temperature: Mapped[Optional[float]] = mapped_column()
    humidity: Mapped[Optional[float]] = mapped_column()
    wind_speed: Mapped[Optional[float]] = mapped_column()
    weather_condition: Mapped[Optional[str]] = mapped_column(String(100))
    travel_distance_home: Mapped[Optional[int]] = mapped_column(Integer)
    travel_distance_away: Mapped[Optional[int]] = mapped_column(Integer)

    match: Mapped["Match"] = relationship(back_populates="external_factors")


class StateTransition(Base):
    """Modelo ORM para la tabla state_transitions."""

    __tablename__ = "state_transitions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), nullable=False)
    minute: Mapped[int] = mapped_column(Integer, nullable=False)
    home_state: Mapped[str] = mapped_column(String(20), nullable=False)
    away_state: Mapped[str] = mapped_column(String(20), nullable=False)
    home_goals_at_minute: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    away_goals_at_minute: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    match: Mapped["Match"] = relationship(back_populates="state_transitions")


class RawApiResponse(Base):
    """Modelo ORM para la tabla raw_api_responses."""

    __tablename__ = "raw_api_responses"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(255), nullable=False)
    response_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    match: Mapped["Match"] = relationship(back_populates="raw_responses")


class ProcessedMatchControl(Base):
    """Modelo ORM para la tabla processed_matches_control."""

    __tablename__ = "processed_matches_control"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), nullable=False)
    season: Mapped[str] = mapped_column(String(20), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text)

    match: Mapped["Match"] = relationship(back_populates="processed_controls")


class DatabaseManager:
    """Administrador de sesiones, creación de tablas y consultas frecuentes."""

    def __init__(self) -> None:
        self.database_url = os.getenv("DATABASE_URL")
        if not self.database_url:
            logger.critical("DATABASE_URL no está definida en el entorno.")
            raise DatabaseConnectionError("DATABASE_URL no está definida.")
        try:
            self.engine: Engine = create_engine(self.database_url, future=True, pool_pre_ping=True)
            self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, future=True)
            with self.SessionLocal() as session:
                session.execute(select(1))
        except SQLAlchemyError as exc:
            logger.critical("Fallo crítico al conectar con la base de datos: %s", exc, exc_info=True)
            raise DatabaseConnectionError("No fue posible conectar a la base de datos.") from exc

    def create_tables(self) -> None:
        """Crea todas las tablas definidas en el ORM."""

        Base.metadata.create_all(self.engine)

    def drop_tables(self) -> None:
        """Elimina todas las tablas; usar solo en entornos de prueba."""

        Base.metadata.drop_all(self.engine)

    def insert_team(self, **kwargs: Any) -> Team:
        """Inserta un equipo y devuelve la instancia creada."""

        return self._insert_record(Team, **kwargs)

    def insert_match(self, **kwargs: Any) -> Match:
        """Inserta un partido y devuelve la instancia creada."""

        return self._insert_record(Match, **kwargs)

    def insert_event(self, **kwargs: Any) -> EventTimeline:
        """Inserta un evento del partido."""

        return self._insert_record(EventTimeline, **kwargs)

    def insert_raw_response(self, **kwargs: Any) -> RawApiResponse:
        """Inserta la respuesta cruda de la API."""

        return self._insert_record(RawApiResponse, **kwargs)

    def get_match_with_events(self, match_id: int) -> Optional[Match]:
        """Obtiene un partido con sus eventos relacionados."""

        logger.info("SELECT pesado: partido con eventos para match_id=%s", match_id)
        with self.SessionLocal() as session:
            stmt = select(Match).options(selectinload(Match.events)).where(Match.id == match_id)
            return session.execute(stmt).unique().scalar_one_or_none()

    def get_teams_by_season(self, season: str) -> list[Team]:
        """Obtiene los equipos que participaron en una temporada."""

        logger.info("SELECT pesado: equipos por temporada=%s", season)
        with self.SessionLocal() as session:
            stmt = (
                select(Team)
                .join(Match, (Team.id == Match.home_team_id) | (Team.id == Match.away_team_id))
                .where(Match.season == season)
                .distinct()
            )
            return list(session.execute(stmt).scalars().all())

    def execute_in_transaction(self, func: Callable[[Session], T]) -> T:
        """Ejecuta una función dentro de una transacción con rollback automático."""

        with self.SessionLocal() as session:
            try:
                with session.begin():
                    return func(session)
            except SQLAlchemyError as exc:
                logger.error("Error en transacción: %s", exc, exc_info=True)
                raise

    def _insert_record(self, model: type[Base], **kwargs: Any) -> Any:
        """Inserta un registro genérico dentro de una transacción."""

        logger.info("INSERT en %s con datos=%s", model.__tablename__, kwargs)

        def _op(session: Session) -> Any:
            instance = model(**kwargs)
            session.add(instance)
            session.flush()
            session.refresh(instance)
            return instance

        return self.execute_in_transaction(_op)


def _format_team(team: Team) -> str:
    """Formatea un equipo para salida por consola."""

    return f"{team.id} - {team.name} ({team.city})"


def _ensure_team(
    manager: DatabaseManager,
    session: Session,
    *,
    name: str,
    city: str,
    stadium: str,
    altitude: int,
    foundation_year: int,
) -> Team:
    """Obtiene o crea un equipo sin duplicarlo en ejecuciones repetidas."""

    existing_team = session.execute(select(Team).where(Team.name == name).order_by(Team.id)).scalars().first()
    if existing_team is not None:
        return existing_team
    return manager.insert_team(
        name=name,
        city=city,
        stadium=stadium,
        altitude=altitude,
        foundation_year=foundation_year,
    )


if __name__ == "__main__":
    """Ejemplo mínimo de uso para validar la estructura."""

    manager = DatabaseManager()
    manager.create_tables()
    def _bootstrap(session: Session) -> None:
        _ensure_team(
            manager,
            session,
            name="Real Madrid",
            city="Madrid",
            stadium="Santiago Bernabéu",
            altitude=667,
            foundation_year=1902,
        )
        _ensure_team(
            manager,
            session,
            name="Barcelona",
            city="Barcelona",
            stadium="Spotify Camp Nou",
            altitude=12,
            foundation_year=1899,
        )

    manager.execute_in_transaction(_bootstrap)
    with manager.SessionLocal() as session:
        teams = session.execute(
            select(Team.name, Team.city, Team.id).order_by(Team.name, Team.id)
        ).all()
    for team in teams:
        print(f"{team.id} - {team.name} ({team.city})")
    assert len(teams) >= 2
    assert any(team.name == "Real Madrid" for team in teams)
    assert any(team.name == "Barcelona" for team in teams)
