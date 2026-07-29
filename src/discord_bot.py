"""Adaptador Discord privado para la API DIKAMAHA.

# Requirements:
# discord.py>=2.4,<3
# requests>=2.31

Version: 1.1.0
Created: 2026-07-29
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any

import discord
from discord import app_commands

from src.telegram_bot import DikamahaHttpGateway, PredictionGateway

LOGGER = logging.getLogger(__name__)
MAX_OPTIONS = 25


@dataclass(frozen=True, slots=True)
class DiscordBotConfig:
    """Configuración segura del adaptador Discord."""

    token: str = field(repr=False)
    application_id: int
    guild_id: int | None
    allowed_user_ids: frozenset[int]
    allowed_guild_ids: frozenset[int]
    dikamaha_base_url: str = "http://127.0.0.1:8000"
    dikamaha_api_key: str | None = field(default=None, repr=False)
    request_timeout_seconds: float = 20.0

    def __post_init__(self) -> None:
        """Valida credenciales y límites básicos."""

        if not self.token or self.application_id < 1:
            raise ValueError("discord_credentials_missing")
        if self.request_timeout_seconds <= 0:
            raise ValueError("discord_timeout_invalid")


def discord_config_from_env() -> DiscordBotConfig:
    """Carga configuración Discord sólo desde el entorno."""

    return DiscordBotConfig(
        token=os.getenv("DISCORD_BOT_TOKEN", ""),
        application_id=_required_int("DISCORD_APPLICATION_ID"),
        guild_id=_optional_int(os.getenv("DISCORD_GUILD_ID")),
        allowed_user_ids=_id_set("DISCORD_ALLOWED_USER_IDS"),
        allowed_guild_ids=_id_set("DISCORD_ALLOWED_GUILD_IDS"),
        dikamaha_base_url=os.getenv(
            "DIKAMAHA_BOT_API_URL", "http://127.0.0.1:8000"),
        dikamaha_api_key=os.getenv("DIKAMAHA_API_KEY") or None,
        request_timeout_seconds=float(
            os.getenv("DISCORD_REQUEST_TIMEOUT", "20")),
    )


def _required_int(name: str) -> int:
    """Lee un entero obligatorio sin exponer su valor."""

    try:
        return int(os.getenv(name, ""))
    except ValueError as error:
        raise ValueError(f"{name.lower()}_invalid") from error


def _optional_int(value: str | None) -> int | None:
    """Convierte un entero opcional."""

    if not value:
        return None
    try:
        return int(value)
    except ValueError as error:
        raise ValueError("discord_guild_id_invalid") from error


def _id_set(name: str) -> frozenset[int]:
    """Convierte una lista de IDs separada por comas."""

    raw = os.getenv(name, "")
    try:
        return frozenset(
            int(item.strip()) for item in raw.split(",") if item.strip())
    except ValueError as error:
        raise ValueError(f"{name.lower()}_invalid") from error


def _authorized(
    config: DiscordBotConfig, user_id: int, guild_id: int | None,
) -> bool:
    """Aplica allowlists de usuario y servidor."""

    user_ok = not config.allowed_user_ids or user_id in config.allowed_user_ids
    guild_ok = (
        guild_id is None or not config.allowed_guild_ids
        or guild_id in config.allowed_guild_ids
    )
    return user_ok and guild_ok


def _fixture_label(row: dict[str, Any]) -> str:
    """Construye una etiqueta compacta de partido."""

    home = str(row.get("home_team_name") or "Equipo 1")
    away = str(row.get("away_team_name") or "Equipo 2")
    kickoff = str(row.get("kickoff_ts") or "")[:16].replace("T", " ")
    return f"{home} vs {away} · {kickoff}"[:100]


def _fixture_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Convierte fixture al contrato de predicción DIKAMAHA."""

    return {
        "league_slug": str(row["league_slug"]),
        "home_team_id": int(row["home_team_id"]),
        "away_team_id": int(row["away_team_id"]),
        "kickoff_ts": str(row["kickoff_ts"]),
        "match_id": int(row["match_id"]),
    }


def _prediction_embed(payload: dict[str, Any]) -> discord.Embed:
    """Renderiza la predicción oficial y etiqueta mercados shadow."""

    fixture = payload.get("fixture") or {}
    home = str(fixture.get("home_team_name") or "Equipo 1")
    away = str(fixture.get("away_team_name") or "Equipo 2")
    embed = discord.Embed(
        title=f"{home} vs {away}", description="Predicción pre-match",
        color=discord.Color.blurple())
    embed.add_field(name=home, value=_pct(payload.get("probability_home")))
    embed.add_field(name="Empate", value=_pct(payload.get("probability_draw")))
    embed.add_field(name=away, value=_pct(payload.get("probability_away")))
    embed.add_field(
        name="Más de 2.5", value=_pct(payload.get("probability_over_2_5")))
    embed.add_field(name="Ambos marcan", value=_pct(payload.get("probability_btts")))
    embed.set_footer(text="Mercados secundarios: experimentales / shadow")
    return embed


def _context_embed(payload: dict[str, Any]) -> discord.Embed:
    """Renderiza contexto visible sin asociarlo a una recomendación."""

    if payload.get("status") != "available":
        return discord.Embed(title="Contexto del partido", description="Snapshot no disponible todavía.", color=discord.Color.dark_grey())
    teams = payload.get("teams") or {}; home = (teams.get("home") or {}).get("name", "Local")
    away = (teams.get("away") or {}).get("name", "Visitante")
    competition = payload.get("competition") or {}; venue = payload.get("venue") or {}
    embed = discord.Embed(title=f"{home} vs {away}", description="Contexto informativo pre-match", color=discord.Color.teal())
    embed.add_field(name="Competición", value=str(competition.get("name") or "N/D"), inline=True)
    embed.add_field(name="Fase", value=str(competition.get("phase") or "N/D"), inline=True)
    embed.add_field(name="Sede", value=_venue_text(venue), inline=False)
    officials = _context_names(payload.get("officials")); broadcasts = _context_names(payload.get("broadcasts"))
    if officials: embed.add_field(name="Oficiales", value=officials, inline=False)
    if broadcasts: embed.add_field(name="Transmisión", value=broadcasts, inline=False)
    standings = _standings_text(payload.get("team_context"), home, away)
    if standings: embed.add_field(name="Posiciones", value=standings, inline=False)
    availability = _availability_text(payload.get("availability"), home, away)
    if availability: embed.add_field(name="Disponibilidad", value=availability, inline=False)
    editorial = _editorial_text(payload.get("editorial"))
    if editorial: embed.add_field(name="Contexto editorial", value=editorial, inline=False)
    embed.set_footer(text="Contexto visible · no modifica la predicción")
    return embed


def _venue_text(venue: dict[str, Any]) -> str:
    """Compone sede con los campos que ESPN efectivamente publicó."""

    values = [venue.get("name"), venue.get("city"), venue.get("country")]
    return " · ".join(str(value) for value in values if value) or "N/D"


def _context_names(rows: Any) -> str:
    """Limita listas de contexto para conservar legibilidad en Discord."""

    values = [str(row.get("name")) for row in rows if isinstance(row, dict) and row.get("name")]
    return ", ".join(values[:3]) + ("…" if len(values) > 3 else "")


def _standings_text(context: Any, home: Any, away: Any) -> str:
    """Resume posiciones publicadas sin fabricar forma ni métricas nuevas."""

    rows = context if isinstance(context, dict) else {}
    values = [_standing_text(rows.get("home"), str(home)), _standing_text(rows.get("away"), str(away))]
    return "\n".join(value for value in values if value)


def _standing_text(row: Any, name: str) -> str:
    """Formatea posición y puntos cuando el snapshot los contiene."""

    standing = row.get("standing") if isinstance(row, dict) else None
    if not isinstance(standing, dict) or not standing.get("rank"):
        return ""
    return f"{name}: #{standing['rank']} · {standing.get('points') or '–'} pts"


def _availability_text(availability: Any, home: Any, away: Any) -> str:
    """Muestra lo publicado por ESPN sin diagnosticar lesiones ausentes."""

    rows = availability if isinstance(availability, dict) else {}
    values = [_availability_item(rows.get("home"), str(home)), _availability_item(rows.get("away"), str(away))]
    return "\n".join(value for value in values if value)


def _availability_item(row: Any, name: str) -> str:
    """Formatea una disponibilidad de equipo con ausencia explícita de proveedor."""

    data = row if isinstance(row, dict) else {}
    if data.get("injury_report_status") == "not_published":
        return f"{name}: reporte de lesiones no publicado"
    injuries = data.get("published_injuries") if isinstance(data.get("published_injuries"), list) else []
    return f"{name}: {len(injuries)} incidencias publicadas · roster {data.get('roster_count') or 'N/D'}"


def _editorial_text(editorial: Any) -> str:
    """Muestra un único titular contextual y preserva su carácter editorial."""

    data = editorial if isinstance(editorial, dict) else {}
    rows = data.get("articles") if isinstance(data.get("articles"), list) else []
    for row in rows:
        if isinstance(row, dict) and row.get("headline"):
            return str(row["headline"])[:1000]
    return ""


def _pct(value: Any) -> str:
    """Formatea una probabilidad sin inventar valores."""

    try:
        return f"{float(value):.1%}"
    except (TypeError, ValueError):
        return "N/D"


class RestrictedView(discord.ui.View):
    """Vista que conserva dependencias y propietario de la interacción."""

    def __init__(
        self, gateway: PredictionGateway, owner_id: int, timeout: float = 180,
    ) -> None:
        """Inicializa dependencias compartidas."""

        super().__init__(timeout=timeout)
        self.gateway = gateway
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Impide que otro usuario opere el menú privado."""

        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "Este menú pertenece a otra consulta.", ephemeral=True)
        return False

    async def on_error(
        self, interaction: discord.Interaction, error: Exception,
        item: discord.ui.Item[Any],
    ) -> None:
        """Sanitiza errores de componentes sin exponer payloads."""

        LOGGER.warning("Discord: componente rechazado (%s).", type(error).__name__)
        message = "No pude completar la consulta. Intenta nuevamente."
        if interaction.response.is_done():
            await interaction.edit_original_response(content=message, view=self)
            return
        await interaction.response.send_message(message, ephemeral=True)


class MainView(RestrictedView):
    """Menú principal equivalente al disponible en Telegram."""

    @discord.ui.button(label="Próximos y predicciones", emoji="🔮")
    async def upcoming(
        self, interaction: discord.Interaction, _: discord.ui.Button,
    ) -> None:
        """Abre las rutas de próximos partidos."""

        await interaction.response.edit_message(
            content="Elige cómo buscar el próximo partido:",
            view=UpcomingView(self.gateway, interaction.user.id))

    @discord.ui.button(label="Play-by-play", emoji="▶️")
    async def plays(
        self, interaction: discord.Interaction, _: discord.ui.Button,
    ) -> None:
        """Abre navegación histórica de eventos."""

        await _show_module_leagues(interaction, self.gateway, "plays")

    @discord.ui.button(label="Estadísticas", emoji="📊")
    async def statistics(
        self, interaction: discord.Interaction, _: discord.ui.Button,
    ) -> None:
        """Abre navegación histórica de estadísticas."""

        await _show_module_leagues(interaction, self.gateway, "stats")

    @discord.ui.button(label="Equipos y jugadores", emoji="👤")
    async def players(
        self, interaction: discord.Interaction, _: discord.ui.Button,
    ) -> None:
        """Abre navegación de equipos y perfiles."""

        await _show_module_leagues(interaction, self.gateway, "players")

    @discord.ui.button(label="Estado", emoji="✅")
    async def status(
        self, interaction: discord.Interaction, _: discord.ui.Button,
    ) -> None:
        """Consulta readiness desde el menú."""

        await interaction.response.defer(ephemeral=True, thinking=True)
        ready = await asyncio.to_thread(self.gateway.readiness)
        state = "Disponible" if ready.get("ready") else "No disponible"
        await interaction.edit_original_response(
            content=f"**DIKAMAHA:** {state}", view=self)


class UpcomingView(RestrictedView):
    """Menú de próximos partidos."""

    @discord.ui.button(label="Todos los próximos", emoji="🌍")
    async def all_games(
        self, interaction: discord.Interaction, _: discord.ui.Button,
    ) -> None:
        """Carga el catálogo global."""

        await interaction.response.defer(ephemeral=True, thinking=True)
        catalog = await asyncio.to_thread(self.gateway.explorer_leagues)
        leagues = catalog.get("leagues", [])
        slugs = ",".join(str(row.get("slug")) for row in leagues)
        result = await asyncio.to_thread(
            self.gateway.list_upcoming, 20, slugs)
        rows = result.get("fixtures", [])
        await _show_fixtures(interaction, self.gateway, rows)

    @discord.ui.button(label="Por liga", emoji="🏆")
    async def by_league(
        self, interaction: discord.Interaction, _: discord.ui.Button,
    ) -> None:
        """Abre selector de ligas."""

        result = await asyncio.to_thread(self.gateway.explorer_leagues)
        rows = result.get("leagues", [])
        view = LeagueView(self.gateway, interaction.user.id, rows)
        await interaction.response.edit_message(
            content="Selecciona una liga:", view=view)

    @discord.ui.button(label="Por fecha", emoji="📅")
    async def by_date(
        self, interaction: discord.Interaction, _: discord.ui.Button,
    ) -> None:
        """Abre selector de fechas futuras."""

        result = await asyncio.to_thread(
            self.gateway.explorer_dates, "future")
        rows = result.get("dates", [])
        view = DateView(self.gateway, interaction.user.id, rows)
        await interaction.response.edit_message(
            content="Selecciona una fecha:", view=view)


class LeagueSelect(discord.ui.Select):
    """Selector de una liga Discord."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        """Construye hasta 25 opciones permitidas."""

        options = [
            discord.SelectOption(
                label=str(row.get("name") or row.get("slug"))[:100],
                value=str(row.get("slug")))
            for row in rows[:MAX_OPTIONS]
        ]
        super().__init__(placeholder="Liga", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Consulta próximos de la liga seleccionada."""

        view = self.view
        if not isinstance(view, RestrictedView):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await asyncio.to_thread(
            view.gateway.list_upcoming, 20, self.values[0])
        rows = result.get("fixtures", [])
        await _show_fixtures(interaction, view.gateway, rows)


class LeagueView(RestrictedView):
    """Contenedor del selector de ligas."""

    def __init__(
        self, gateway: PredictionGateway, owner_id: int,
        rows: list[dict[str, Any]],
    ) -> None:
        """Añade selector de liga."""

        super().__init__(gateway, owner_id)
        self.add_item(LeagueSelect(rows))


async def _show_module_leagues(
    interaction: discord.Interaction, gateway: PredictionGateway, mode: str,
) -> None:
    """Carga ligas para un módulo del explorador."""

    result = await asyncio.to_thread(gateway.explorer_leagues)
    rows = [row for row in result.get("leagues", []) if isinstance(row, dict)]
    view = ExplorerLeagueView(gateway, interaction.user.id, rows, mode)
    title = {"plays": "Play-by-play", "stats": "Estadísticas",
             "players": "Equipos y jugadores"}.get(mode, "Explorador")
    await interaction.response.edit_message(
        content=f"**{title}** · selecciona una liga:", view=view)


class ExplorerLeagueSelect(discord.ui.Select):
    """Selector de liga para datos históricos o jugadores."""

    def __init__(self, rows: list[dict[str, Any]], mode: str) -> None:
        """Construye opciones y conserva el módulo."""

        self.mode = mode
        options = [discord.SelectOption(
            label=str(row.get("name") or row.get("slug"))[:100],
            value=str(row.get("slug"))) for row in rows[:MAX_OPTIONS]]
        super().__init__(placeholder="Liga", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Continúa a fechas pasadas o equipos."""

        view = self.view
        if not isinstance(view, RestrictedView):
            return
        league = self.values[0]
        if self.mode == "players":
            await _show_teams(interaction, view.gateway, league)
            return
        result = await asyncio.to_thread(view.gateway.explorer_dates, "past")
        rows = result.get("dates", [])
        next_view = PastDateView(
            view.gateway, interaction.user.id, rows, self.mode, league)
        await interaction.response.edit_message(
            content="Selecciona una fecha de los últimos ocho días:",
            view=next_view)


class ExplorerLeagueView(RestrictedView):
    """Contenedor de ligas del explorador."""

    def __init__(
        self, gateway: PredictionGateway, owner_id: int,
        rows: list[dict[str, Any]], mode: str,
    ) -> None:
        """Añade el selector correspondiente."""

        super().__init__(gateway, owner_id)
        self.add_item(ExplorerLeagueSelect(rows, mode))


class PastDateSelect(discord.ui.Select):
    """Selector de fecha histórica por liga."""

    def __init__(
        self, rows: list[dict[str, Any]], mode: str, league: str,
    ) -> None:
        """Conserva contexto del módulo."""

        self.mode, self.league = mode, league
        options = [discord.SelectOption(
            label=str(row.get("label") or row.get("date")),
            value=str(row.get("date"))) for row in rows[:MAX_OPTIONS]]
        super().__init__(placeholder="Fecha", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Carga partidos históricos de la fecha."""

        view = self.view
        if not isinstance(view, RestrictedView):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await asyncio.to_thread(
            view.gateway.explorer_fixtures, self.league, self.values[0])
        rows = [row for row in result.get("fixtures", [])
                if isinstance(row, dict)]
        await _show_historical_fixtures(
            interaction, view.gateway, rows, self.mode, self.league)


class PastDateView(RestrictedView):
    """Contenedor del calendario histórico."""

    def __init__(
        self, gateway: PredictionGateway, owner_id: int,
        rows: list[dict[str, Any]], mode: str, league: str,
    ) -> None:
        """Añade selector de fecha pasada."""

        super().__init__(gateway, owner_id)
        self.add_item(PastDateSelect(rows, mode, league))


async def _show_historical_fixtures(
    interaction: discord.Interaction, gateway: PredictionGateway,
    rows: list[dict[str, Any]], mode: str, league: str,
) -> None:
    """Muestra partidos históricos con marcador disponible."""

    if not rows:
        await interaction.edit_original_response(
            content="No hay partidos publicados para esa fecha.", view=None)
        return
    view = HistoricalFixtureView(
        gateway, interaction.user.id, rows, mode, league)
    await interaction.edit_original_response(
        content="Selecciona un partido:", view=view)


class HistoricalFixtureSelect(discord.ui.Select):
    """Selector de fixture histórico."""

    def __init__(
        self, rows: list[dict[str, Any]], mode: str, league: str,
    ) -> None:
        """Indexa fixtures sin incluir payloads en custom IDs."""

        self.mode, self.league = mode, league
        self.fixtures = {str(index): row for index, row in enumerate(
            rows[:MAX_OPTIONS])}
        options = [discord.SelectOption(
            label=_historical_label(row), value=str(index))
            for index, row in enumerate(rows[:MAX_OPTIONS])]
        super().__init__(placeholder="Partido", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Abre estadísticas o acciones de play-by-play."""

        view = self.view
        if not isinstance(view, RestrictedView):
            return
        fixture = {**self.fixtures[self.values[0]], "league_slug": self.league}
        if self.mode == "stats":
            await _show_historical_statistics(
                interaction, view.gateway, fixture)
            return
        next_view = HistoricalPlayView(
            view.gateway, interaction.user.id, fixture)
        await interaction.response.edit_message(
            content=f"**{_fixture_label(fixture)}**\nElige el alcance:",
            view=next_view)


class HistoricalFixtureView(RestrictedView):
    """Contenedor del selector histórico."""

    def __init__(
        self, gateway: PredictionGateway, owner_id: int,
        rows: list[dict[str, Any]], mode: str, league: str,
    ) -> None:
        """Añade selector de fixture."""

        super().__init__(gateway, owner_id)
        self.add_item(HistoricalFixtureSelect(rows, mode, league))


class DateSelect(discord.ui.Select):
    """Selector de fecha futura."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        """Construye fechas disponibles."""

        options = [
            discord.SelectOption(
                label=str(row.get("label") or row.get("date")),
                value=str(row.get("date")))
            for row in rows[:MAX_OPTIONS]
        ]
        super().__init__(placeholder="Fecha", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Consulta todas las ligas para la fecha."""

        view = self.view
        if not isinstance(view, RestrictedView):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        catalog = await asyncio.to_thread(view.gateway.explorer_leagues)
        leagues = catalog.get("leagues", [])
        slugs = ",".join(str(row.get("slug")) for row in leagues)
        result = await asyncio.to_thread(
            view.gateway.list_upcoming, 20, slugs, self.values[0])
        rows = result.get("fixtures", [])
        await _show_fixtures(interaction, view.gateway, rows)


class DateView(RestrictedView):
    """Contenedor del selector de fecha."""

    def __init__(
        self, gateway: PredictionGateway, owner_id: int,
        rows: list[dict[str, Any]],
    ) -> None:
        """Añade selector de fecha."""

        super().__init__(gateway, owner_id)
        self.add_item(DateSelect(rows))


class FixtureSelect(discord.ui.Select):
    """Selector de partido con fixture almacenado en servidor."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        """Conserva fixtures y crea opciones compactas."""

        self.fixtures = {
            str(index): row for index, row in enumerate(rows[:MAX_OPTIONS])}
        options = [
            discord.SelectOption(label=_fixture_label(row), value=str(index))
            for index, row in enumerate(rows[:MAX_OPTIONS])
        ]
        super().__init__(placeholder="Partido", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Muestra acciones del partido seleccionado."""

        view = self.view
        if not isinstance(view, RestrictedView):
            return
        fixture = self.fixtures[self.values[0]]
        match_view = MatchView(view.gateway, interaction.user.id, fixture)
        await interaction.response.edit_message(
            content=f"**{_fixture_label(fixture)}**", view=match_view)


class FixtureView(RestrictedView):
    """Contenedor de selección de partido."""

    def __init__(
        self, gateway: PredictionGateway, owner_id: int,
        rows: list[dict[str, Any]],
    ) -> None:
        """Añade selector de fixture."""

        super().__init__(gateway, owner_id)
        self.add_item(FixtureSelect(rows))


class MatchView(RestrictedView):
    """Acciones disponibles para un partido."""

    def __init__(
        self, gateway: PredictionGateway, owner_id: int,
        fixture: dict[str, Any],
    ) -> None:
        """Conserva fixture validado."""

        super().__init__(gateway, owner_id)
        self.fixture = fixture
        self.prediction_result: dict[str, Any] | None = None
        self.add_item(MarketPeriodSelect())

    @discord.ui.button(label="Predicción", emoji="🔮")
    async def prediction(
        self, interaction: discord.Interaction, _: discord.ui.Button,
    ) -> None:
        """Solicita predicción al router DIKAMAHA."""

        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await self.get_prediction()
        await interaction.edit_original_response(
            content=None, embed=_prediction_embed(result), view=self)

    async def get_prediction(self) -> dict[str, Any]:
        """Obtiene una sola predicción y la conserva durante la vista."""

        if self.prediction_result is not None:
            return self.prediction_result
        result = await asyncio.to_thread(
            self.gateway.predict_upcoming, _fixture_payload(self.fixture))
        result.setdefault("fixture", {
            "home_team_name": self.fixture.get("home_team_name"),
            "away_team_name": self.fixture.get("away_team_name")})
        self.prediction_result = result
        return result

    @discord.ui.button(label="Contexto", emoji="🏟")
    async def context(
        self, interaction: discord.Interaction, _: discord.ui.Button,
    ) -> None:
        """Muestra ficha raw-first sin solicitar ni alterar la predicción."""

        await interaction.response.defer(ephemeral=True, thinking=True)
        payload = await asyncio.to_thread(
            self.gateway.explorer_fixture_context,
            str(self.fixture["league_slug"]), str(self.fixture["match_id"]))
        await interaction.edit_original_response(
            content=None, embed=_context_embed(payload), view=self)

    @discord.ui.button(label="Estadísticas", emoji="📊")
    async def statistics(
        self, interaction: discord.Interaction, _: discord.ui.Button,
    ) -> None:
        """Consulta estadísticas reconciliadas del partido."""

        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await asyncio.to_thread(
            self.gateway.explorer_statistics,
            str(self.fixture["league_slug"]), str(self.fixture["match_id"]),
            str(self.fixture.get("competition_id") or self.fixture["match_id"]))
        await interaction.edit_original_response(
            content=_statistics_text(result), embed=None, view=self)

    @discord.ui.button(label="Play-by-play", emoji="▶️")
    async def plays(
        self, interaction: discord.Interaction, _: discord.ui.Button,
    ) -> None:
        """Consulta los eventos clave disponibles."""

        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await asyncio.to_thread(
            self.gateway.explorer_plays,
            str(self.fixture["league_slug"]), str(self.fixture["match_id"]),
            str(self.fixture.get("competition_id") or self.fixture["match_id"]))
        await interaction.edit_original_response(
            content=_plays_text(result), embed=None, view=self)


class MarketPeriodSelect(discord.ui.Select):
    """Selector compacto de mercados por periodo."""

    def __init__(self) -> None:
        """Añade primer tiempo, segundo tiempo y total."""

        options = [
            discord.SelectOption(label="Primer tiempo", value="first_half"),
            discord.SelectOption(label="Segundo tiempo", value="second_half"),
            discord.SelectOption(label="Totales", value="full_match"),
        ]
        super().__init__(placeholder="Mercados por periodo", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Muestra mercados shadow del periodo elegido."""

        view = self.view
        if not isinstance(view, MatchView):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await view.get_prediction()
        await interaction.edit_original_response(
            content=_market_text(result, self.values[0]),
            embed=None, view=view)


class HistoricalPlayView(RestrictedView):
    """Selector de eventos clave o secuencia completa."""

    def __init__(
        self, gateway: PredictionGateway, owner_id: int,
        fixture: dict[str, Any],
    ) -> None:
        """Conserva el fixture histórico."""

        super().__init__(gateway, owner_id)
        self.fixture = fixture

    @discord.ui.button(label="Eventos clave", emoji="⭐")
    async def key_events(
        self, interaction: discord.Interaction, _: discord.ui.Button,
    ) -> None:
        """Carga goles, tiros, tarjetas y cambios."""

        await self._load(interaction, "key")

    @discord.ui.button(label="Todos", emoji="📋")
    async def all_events(
        self, interaction: discord.Interaction, _: discord.ui.Button,
    ) -> None:
        """Carga la secuencia completa paginada."""

        await self._load(interaction, "all")

    async def _load(
        self, interaction: discord.Interaction, scope: str,
    ) -> None:
        """Consulta el PBP y abre la primera página."""

        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await asyncio.to_thread(
            self.gateway.explorer_plays, str(self.fixture["league_slug"]),
            str(self.fixture["match_id"]), _competition_id(self.fixture), scope)
        rows = [row for row in result.get("plays", []) if isinstance(row, dict)]
        page_view = PlayPageView(
            self.gateway, interaction.user.id, self.fixture, rows, scope, 0)
        await interaction.edit_original_response(
            content=_plays_page_text(self.fixture, rows, scope, 0),
            view=page_view)


class PlayPageView(RestrictedView):
    """Paginación de ocho eventos por mensaje."""

    def __init__(
        self, gateway: PredictionGateway, owner_id: int,
        fixture: dict[str, Any], rows: list[dict[str, Any]],
        scope: str, page: int,
    ) -> None:
        """Conserva página y habilita botones pertinentes."""

        super().__init__(gateway, owner_id)
        self.fixture, self.rows, self.scope, self.page = fixture, rows, scope, page
        self.previous.disabled = page <= 0
        self.next.disabled = (page + 1) * 8 >= len(rows)

    @discord.ui.button(label="Anterior", emoji="◀")
    async def previous(
        self, interaction: discord.Interaction, _: discord.ui.Button,
    ) -> None:
        """Retrocede una página."""

        await self._move(interaction, self.page - 1)

    @discord.ui.button(label="Siguiente", emoji="▶")
    async def next(
        self, interaction: discord.Interaction, _: discord.ui.Button,
    ) -> None:
        """Avanza una página."""

        await self._move(interaction, self.page + 1)

    async def _move(
        self, interaction: discord.Interaction, page: int,
    ) -> None:
        """Reemplaza la vista manteniendo datos en memoria."""

        view = PlayPageView(
            self.gateway, self.owner_id, self.fixture,
            self.rows, self.scope, page)
        await interaction.response.edit_message(
            content=_plays_page_text(
                self.fixture, self.rows, self.scope, page), view=view)


async def _show_historical_statistics(
    interaction: discord.Interaction, gateway: PredictionGateway,
    fixture: dict[str, Any],
) -> None:
    """Consulta estadísticas y abre el selector por periodo."""

    await interaction.response.defer(ephemeral=True, thinking=True)
    result = await asyncio.to_thread(
        gateway.explorer_statistics, str(fixture["league_slug"]),
        str(fixture["match_id"]), _competition_id(fixture))
    view = StatisticsView(
        gateway, interaction.user.id, fixture, result)
    await interaction.edit_original_response(
        content=_statistics_text(result, "total"), view=view)


class StatisticsPeriodSelect(discord.ui.Select):
    """Selector 1T, 2T y total de estadísticas."""

    def __init__(self) -> None:
        """Construye las tres opciones temporales."""

        options = [
            discord.SelectOption(label="Primer tiempo", value="first_half"),
            discord.SelectOption(label="Segundo tiempo", value="second_half"),
            discord.SelectOption(label="Total", value="total"),
        ]
        super().__init__(placeholder="Periodo", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Actualiza la tabla del periodo."""

        view = self.view
        if not isinstance(view, StatisticsView):
            return
        await interaction.response.edit_message(
            content=_statistics_text(view.payload, self.values[0]), view=view)


class StatisticsView(RestrictedView):
    """Contenedor de estadísticas del partido."""

    def __init__(
        self, gateway: PredictionGateway, owner_id: int,
        fixture: dict[str, Any], payload: dict[str, Any],
    ) -> None:
        """Conserva respuesta reconciliada."""

        super().__init__(gateway, owner_id)
        self.fixture, self.payload = fixture, payload
        self.add_item(StatisticsPeriodSelect())


async def _show_teams(
    interaction: discord.Interaction, gateway: PredictionGateway,
    league: str, query: str = "",
) -> None:
    """Lista equipos o coincidencias de búsqueda."""

    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)
    result = await asyncio.to_thread(gateway.explorer_teams, league, query)
    rows = [row for row in result.get("teams", []) if isinstance(row, dict)]
    view = TeamsView(gateway, interaction.user.id, league, rows)
    title = "Coincidencias" if query else "Selecciona un equipo"
    await interaction.edit_original_response(content=f"**{title}**", view=view)


class TeamSelect(discord.ui.Select):
    """Selector de equipo para abrir plantilla."""

    def __init__(self, rows: list[dict[str, Any]], league: str) -> None:
        """Indexa hasta 25 equipos."""

        self.league = league
        self.teams = {str(index): row for index, row in enumerate(
            rows[:MAX_OPTIONS])}
        options = [discord.SelectOption(
            label=str(row.get("name") or row.get("id"))[:100],
            value=str(index)) for index, row in enumerate(rows[:MAX_OPTIONS])]
        super().__init__(placeholder="Equipo", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Consulta la plantilla del equipo."""

        view = self.view
        if not isinstance(view, RestrictedView):
            return
        team = self.teams[self.values[0]]
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await asyncio.to_thread(
            view.gateway.explorer_roster, self.league, str(team["id"]))
        rows = [row for row in result.get("players", []) if isinstance(row, dict)]
        next_view = RosterView(
            view.gateway, interaction.user.id, self.league, team, rows)
        await interaction.edit_original_response(
            content=f"**Plantilla · {team.get('name', 'Equipo')}**\n"
            f"{len(rows)} jugadores", view=next_view)


class TeamSearchButton(discord.ui.Button):
    """Botón que abre el modal de búsqueda."""

    def __init__(self, league: str) -> None:
        """Conserva liga sin solicitarla al usuario."""

        super().__init__(label="Buscar equipo", emoji="🔎")
        self.league = league

    async def callback(self, interaction: discord.Interaction) -> None:
        """Abre formulario nativo de texto."""

        view = self.view
        if not isinstance(view, RestrictedView):
            return
        await interaction.response.send_modal(
            TeamSearchModal(view.gateway, interaction.user.id, self.league))


class TeamSearchModal(discord.ui.Modal, title="Buscar equipo"):
    """Modal de búsqueda equivalente al filtro Telegram."""

    query = discord.ui.TextInput(
        label="Nombre o prefijo", min_length=2, max_length=60,
        placeholder="Ejemplo: Cruz A")

    def __init__(
        self, gateway: PredictionGateway, owner_id: int, league: str,
    ) -> None:
        """Conserva dependencias de la consulta."""

        super().__init__()
        self.gateway, self.owner_id, self.league = gateway, owner_id, league

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Devuelve coincidencias como selector."""

        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Consulta no autorizada.", ephemeral=True)
            return
        await _show_teams(
            interaction, self.gateway, self.league, str(self.query))


class TeamsView(RestrictedView):
    """Contenedor de equipos y búsqueda."""

    def __init__(
        self, gateway: PredictionGateway, owner_id: int,
        league: str, rows: list[dict[str, Any]],
    ) -> None:
        """Añade selector disponible y modal."""

        super().__init__(gateway, owner_id)
        if rows:
            self.add_item(TeamSelect(rows, league))
        self.add_item(TeamSearchButton(league))


class PlayerSelect(discord.ui.Select):
    """Selector de jugador de una plantilla."""

    def __init__(
        self, rows: list[dict[str, Any]], league: str, team_id: str,
    ) -> None:
        """Indexa jugadores con dorsal y nombre."""

        self.league, self.team_id = league, team_id
        self.players = {str(index): row for index, row in enumerate(
            rows[:MAX_OPTIONS])}
        options = [discord.SelectOption(
            label=f"{row.get('jersey') or '—'} · {row.get('name')}"[:100],
            value=str(index)) for index, row in enumerate(rows[:MAX_OPTIONS])]
        super().__init__(placeholder="Jugador", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Consulta perfil y acumulados individuales."""

        view = self.view
        if not isinstance(view, RestrictedView):
            return
        player = self.players[self.values[0]]
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await asyncio.to_thread(
            view.gateway.explorer_player, self.league, self.team_id,
            str(player["id"]))
        await interaction.edit_original_response(
            content=_player_text(result), view=view)


class RosterView(RestrictedView):
    """Contenedor de jugadores de equipo."""

    def __init__(
        self, gateway: PredictionGateway, owner_id: int, league: str,
        team: dict[str, Any], rows: list[dict[str, Any]],
    ) -> None:
        """Añade selector de jugador cuando hay plantilla."""

        super().__init__(gateway, owner_id)
        if rows:
            self.add_item(PlayerSelect(rows, league, str(team["id"])))


async def _show_fixtures(
    interaction: discord.Interaction, gateway: PredictionGateway,
    rows: list[dict[str, Any]],
) -> None:
    """Edita la respuesta con fixtures o ausencia explícita."""

    valid = [row for row in rows if isinstance(row, dict)]
    if not valid:
        await interaction.edit_original_response(
            content="No hay partidos para ese filtro.", view=None)
        return
    await interaction.edit_original_response(
        content="Selecciona un partido:",
        view=FixtureView(gateway, interaction.user.id, valid))


def _statistics_text(
    payload: dict[str, Any], period: str = "total",
) -> str:
    """Resume estadísticas por periodo con nombres reales."""

    teams, periods = payload.get("teams", {}), payload.get("periods", {})
    home = (teams.get("home") or {}).get("name", "Equipo 1")
    away = (teams.get("away") or {}).get("name", "Equipo 2")
    first = (periods.get("home") or {}).get(period, {})
    second = (periods.get("away") or {}).get(period, {})
    rows = ["Goles", "Tiros", "A puerta", "Córners", "Amarillas", "Rojas",
            "Faltas", "Fuera juego", "Atajadas", "Cambios"]
    keys = ["goals", "shots", "shots_on_target", "corners", "yellow_cards",
            "red_cards", "fouls", "offsides", "saves", "substitutions"]
    body = "\n".join(
        f"{label:<10} {first.get(key, 0):>4} {second.get(key, 0):>4}"
        for label, key in zip(rows, keys))
    label = {"first_half": "1T", "second_half": "2T",
             "total": "Total"}.get(period, period)
    return (f"**{home} vs {away} · {label}**\n```text\n"
            f"{'Evento':<10} {'1':>4} {'2':>4}\n{body}\n```")[:2000]


def _plays_text(payload: dict[str, Any]) -> str:
    """Renderiza los eventos clave más recientes bajo el límite Discord."""

    rows = payload.get("plays", [])
    lines = [
        f"{row.get('clock', '')} · {row.get('label', 'Evento')} · "
        f"{row.get('text', '')}" for row in rows[-12:] if isinstance(row, dict)
    ]
    content = "\n".join(lines) or "No hay eventos disponibles."
    return f"**Play-by-play clave**\n{content}"[:2000]


def _plays_page_text(
    fixture: dict[str, Any], rows: list[dict[str, Any]],
    scope: str, page: int,
) -> str:
    """Renderiza ocho eventos y posición de página."""

    start, selected = page * 8, rows[page * 8:(page + 1) * 8]
    lines = [
        f"`{row.get('clock', ''):>6}` **{row.get('label', 'Evento')}**\n"
        f"{str(row.get('text', ''))[:180]}" for row in selected
    ]
    maximum = max(1, (len(rows) + 7) // 8)
    title = "Eventos clave" if scope == "key" else "Todos los eventos"
    body = "\n\n".join(lines) or "No hay eventos disponibles."
    return (f"**{_fixture_label(fixture)}**\n{title} · "
            f"página {page + 1}/{maximum} · {start + 1}-{start + len(selected)}"
            f" de {len(rows)}\n\n{body}")[:2000]


def _historical_label(row: dict[str, Any]) -> str:
    """Añade marcador al selector histórico cuando existe."""

    home = str(row.get("home_team_name") or "Equipo 1")
    away = str(row.get("away_team_name") or "Equipo 2")
    left, right = row.get("home_score"), row.get("away_score")
    score = f" {left}-{right}" if left is not None and right is not None else ""
    return f"{home}{score} {away}"[:100]


def _competition_id(fixture: dict[str, Any]) -> str:
    """Obtiene competencia con fallback exacto al match ID."""

    return str(fixture.get("competition_id") or fixture["match_id"])


def _player_text(payload: dict[str, Any]) -> str:
    """Presenta perfil y acumulados individuales."""

    rows = [
        ("Posición", payload.get("position") or "N/D"),
        ("Edad", payload.get("age") or "N/D"),
        ("Altura", payload.get("height") or "N/D"),
        ("Peso", payload.get("weight") or "N/D"),
        ("Nacionalidad", payload.get("citizenship") or "N/D"),
    ]
    profile = "\n".join(f"**{key}:** {value}" for key, value in rows)
    statistics = payload.get("statistics") or []
    values = "\n".join(
        f"• {row.get('displayName') or row.get('name')}: {row.get('value', 0)}"
        for row in statistics[:12] if isinstance(row, dict))
    values = values or "Estadísticas acumuladas no publicadas por ESPN."
    name = str(payload.get("name") or "Jugador")
    return f"## {name}\n{profile}\n\n**Temporada**\n{values}"[:2000]


def _market_text(payload: dict[str, Any], period: str) -> str:
    """Presenta probabilidades por periodo y equipo real."""

    fixture = payload.get("fixture") or {}
    names = {
        "home": fixture.get("home_team_name") or "Equipo 1",
        "away": fixture.get("away_team_name") or "Equipo 2",
        "total": "Total",
    }
    market = payload.get("experimental_team_markets") or {}
    recommended = market.get("recommended_market_view") or []
    rows = market.get("user_market_view") or []
    scenarios = [
        row for row in recommended if isinstance(row, dict)
        and row.get("period") == period
    ]
    selected = [
        row for row in rows if isinstance(row, dict)
        and row.get("period") == period
    ]
    lines = [_recommended_market_row(row, names) for row in scenarios]
    lines.extend(_market_row(row, names) for row in selected)
    label = {
        "first_half": "Primer tiempo", "second_half": "Segundo tiempo",
        "full_match": "Totales",
    }.get(period, period)
    body = "\n".join(lines) or "Sin líneas disponibles."
    return f"**Mercados · {label}**\n{body}\n\n_Experimentales / shadow._"[:2000]


def _recommended_market_row(
    row: dict[str, Any], names: dict[str, Any],
) -> str:
    """Renderiza un escenario distribucional seleccionado."""

    metric = {
        "corners": "Córners", "shots": "Tiros",
        "yellow_cards": "Tarjetas",
    }.get(str(row.get("metric")), str(row.get("metric") or "Mercado"))
    team = str(names.get(str(row.get("team_side")), "Total"))
    direction = "Más" if row.get("direction") == "over" else "Menos"
    edge = 100.0 * float(row.get("incremental_probability") or 0.0)
    return (
        f"⭐ {metric} · **{team}** · {direction} {row.get('line')}: "
        f"{_pct(row.get('probability'))} "
        f"(base {_pct(row.get('baseline_probability'))}, "
        f"Δequipo +{edge:.1f}pp)"
    )


def _market_row(row: dict[str, Any], names: dict[str, Any]) -> str:
    """Renderiza una línea de mercado sin orientación genérica."""

    metric = {
        "corners": "Córners", "shots": "Tiros",
        "shots_on_target": "Tiros a puerta",
    }.get(str(row.get("metric")), str(row.get("metric") or "Mercado"))
    team = str(names.get(str(row.get("team_side")), "Total"))
    line = row.get("line")
    return (
        f"• {metric} · **{team}** · +{line}: "
        f"{_pct(row.get('probability'))} "
        f"(base {_pct(row.get('baseline_probability'))})"
    )


class DikamahaDiscordClient(discord.Client):
    """Cliente Discord con árbol de slash commands."""

    def __init__(
        self, config: DiscordBotConfig, gateway: PredictionGateway,
    ) -> None:
        """Inicializa Gateway sin intents privilegiados."""

        super().__init__(
            intents=discord.Intents.none(),
            application_id=config.application_id)
        self.config = config
        self.gateway = gateway
        self.tree = app_commands.CommandTree(self)
        self._register_commands()

    async def setup_hook(self) -> None:
        """Sincroniza comandos globales o con el servidor de desarrollo."""

        if self.config.guild_id:
            guild = discord.Object(id=self.config.guild_id)
            self.tree.copy_global_to(guild=guild)
            try:
                await self.tree.sync(guild=guild)
                return
            except discord.Forbidden:
                LOGGER.warning(
                    "Guild Discord no accesible; usando sincronización global.")
        await self.tree.sync()

    def _register_commands(self) -> None:
        """Registra accesos directos equivalentes al menú."""

        self.tree.command(name="dikamaha", description="Abrir DIKAMAHA")(
            self._home_command)
        self.tree.command(name="proximos", description="Próximos partidos")(
            self._upcoming_command)
        self.tree.command(name="estado", description="Estado de DIKAMAHA")(
            self._status_command)
        self.tree.command(name="playbyplay", description="Eventos de partido")(
            self._plays_command)
        self.tree.command(name="estadisticas", description="Datos de partido")(
            self._statistics_command)
        self.tree.command(name="jugadores", description="Equipos y jugadores")(
            self._players_command)

    async def _home_command(self, interaction: discord.Interaction) -> None:
        """Abre el menú principal privado."""

        if not await self._allow(interaction):
            return
        await interaction.response.send_message(
            "**DIKAMAHA · fútbol pre-match**\nElige una opción.",
            view=MainView(self.gateway, interaction.user.id), ephemeral=True)

    async def _upcoming_command(
        self, interaction: discord.Interaction,
    ) -> None:
        """Abre directamente las rutas de próximos."""

        if not await self._allow(interaction):
            return
        await interaction.response.send_message(
            "Elige cómo buscar el próximo partido:",
            view=UpcomingView(self.gateway, interaction.user.id),
            ephemeral=True)

    async def _plays_command(self, interaction: discord.Interaction) -> None:
        """Acceso directo al play-by-play."""

        await self._module_command(interaction, "plays")

    async def _statistics_command(
        self, interaction: discord.Interaction,
    ) -> None:
        """Acceso directo a estadísticas."""

        await self._module_command(interaction, "stats")

    async def _players_command(self, interaction: discord.Interaction) -> None:
        """Acceso directo a equipos y jugadores."""

        await self._module_command(interaction, "players")

    async def _module_command(
        self, interaction: discord.Interaction, mode: str,
    ) -> None:
        """Abre un módulo desde slash command."""

        if not await self._allow(interaction):
            return
        result = await asyncio.to_thread(self.gateway.explorer_leagues)
        rows = [row for row in result.get("leagues", [])
                if isinstance(row, dict)]
        view = ExplorerLeagueView(
            self.gateway, interaction.user.id, rows, mode)
        await interaction.response.send_message(
            "Selecciona una liga:", view=view, ephemeral=True)

    async def _status_command(self, interaction: discord.Interaction) -> None:
        """Muestra readiness del servicio."""

        if not await self._allow(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        ready = await asyncio.to_thread(self.gateway.readiness)
        state = "Disponible" if ready.get("ready") else "No disponible"
        await interaction.edit_original_response(
            content=f"**DIKAMAHA:** {state}")

    async def _allow(self, interaction: discord.Interaction) -> bool:
        """Responde de forma privada cuando la allowlist rechaza."""

        guild_id = interaction.guild_id
        if _authorized(self.config, interaction.user.id, guild_id):
            return True
        await interaction.response.send_message(
            "Esta es una prueba privada de DIKAMAHA.", ephemeral=True)
        return False


def build_discord_client(
    config: DiscordBotConfig,
    gateway: PredictionGateway | None = None,
) -> DikamahaDiscordClient:
    """Construye el cliente con dependencias sustituibles."""

    effective_gateway = gateway or DikamahaHttpGateway(config)
    return DikamahaDiscordClient(config, effective_gateway)


def run_discord_bot(config: DiscordBotConfig) -> None:
    """Ejecuta el Gateway Discord sin registrar secretos."""

    LOGGER.info("Discord bot iniciado en modo privado.")
    build_discord_client(config).run(config.token, log_handler=None)


# Version: 1.1.0
# Created: 2026-07-29
