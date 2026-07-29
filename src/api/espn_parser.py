"""Parser de play-by-play de ESPN hacia events_timeline."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Optional

from src.database.manager import DatabaseManager
from src.espn_event_taxonomy import KNOWN_EVENTS, classify_event_type

logger = logging.getLogger("espn_parser")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)


@dataclass(slots=True)
class ParsedEvent:
    """Representa una jugada limpia lista para persistir."""

    minute: int
    second: int
    team_id: Optional[int]
    athlete_ref: Optional[str]
    event_type: str
    event_type_raw: Optional[str]
    description: Optional[str]
    player_name: Optional[str]
    assist_name: Optional[str]


@dataclass(slots=True)
class ParseReport:
    """Resumen de cobertura y filtrado del parseo."""

    total_events: int
    events_with_team_id: int
    events_with_player_name: int
    events_without_clock: int
    kept_events: int
    discarded_events: int
    event_type_distribution: dict[str, int]
    discarded_by_type: dict[str, int]


class ESPNPlayParser:
    """Transforma el JSON de ESPN en filas compatibles con events_timeline."""

    ALLOWED_EVENT_TYPES = set(KNOWN_EVENTS)
    TYPE_MAP = {
        "goal": "goal",
        "shot-on-target": "shot_on_target",
        "shot-off-target": "shot_off_target",
        "corner-awarded": "corner",
        "foul": "foul",
        "yellow-card": "yellow",
        "red-card": "red",
        "substitution": "substitution",
        "shot-blocked": "shot_blocked",
        "penalty---scored": "goal",
    }

    def __init__(self) -> None:
        self.last_report: Optional[ParseReport] = None

    def parse(self, raw_json: dict[str, Any]) -> list[dict[str, Any]]:
        """Convierte la respuesta cruda en una lista de diccionarios limpios."""

        plays = self._extract_plays(raw_json)
        parsed_events: list[ParsedEvent] = []
        discarded_by_type: Counter[str] = Counter()
        for play in plays:
            parsed = self._parse_play(play, raw_json)
            if parsed is None:
                discarded_by_type[self._raw_event_type(play)] += 1
                continue
            parsed_events.append(parsed)
        self.last_report = self._build_report(plays, parsed_events, discarded_by_type)
        return [asdict(event) for event in parsed_events]

    def report(self, raw_json: dict[str, Any]) -> ParseReport:
        """Genera un informe sin filtrar ni mutar el payload."""

        plays = self._extract_plays(raw_json)
        parsed_events: list[ParsedEvent] = []
        discarded_by_type: Counter[str] = Counter()
        for play in plays:
            parsed = self._parse_play(play, raw_json)
            if parsed is None:
                discarded_by_type[self._raw_event_type(play)] += 1
                continue
            parsed_events.append(parsed)
        return self._build_report(plays, parsed_events, discarded_by_type)

    def parse_and_save(self, match_id: int, raw_json: dict[str, Any], db_manager: DatabaseManager) -> list[Any]:
        """Parses the payload and persists the resulting events."""

        parsed_events = self.parse(raw_json)
        saved_events = []
        for event in parsed_events:
            saved_events.append(db_manager.insert_event(match_id=match_id, **event))
        return saved_events

    def _extract_plays(self, raw_json: dict[str, Any]) -> list[dict[str, Any]]:
        """Extrae el array de jugadas de varias formas posibles."""

        items = raw_json.get("items")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
        # TODO: confirmar estructura exacta en la documentación de ESPN.
        return []

    def _parse_play(self, play: dict[str, Any], raw_json: dict[str, Any]) -> Optional[ParsedEvent]:
        """Normaliza una sola jugada."""

        minute, second = self._parse_clock(play.get("clock"))
        team_id = self._resolve_team_id(play, raw_json)
        raw_event_type = self._raw_event_type(play)
        event_type = self._map_event_type(play)
        if play.get("scoringPlay") is True and event_type != "goal":
            event_type = "goal"
        description = self._first_text(play, ("text", "shortText", "alternativeText"))
        if description is None:
            logger.warning("description no encontrado; se guardará como None.")
        athlete_ref = self._resolve_athlete_ref(play)
        player_name = self._get_player_name(play)
        assist_name = None
        logger.warning("assist_name no viene documentado en el sample real; se guardará como None.")
        return ParsedEvent(
            minute=minute,
            second=second,
            team_id=team_id,
            athlete_ref=athlete_ref,
            event_type=event_type,
            event_type_raw=raw_event_type,
            description=description,
            player_name=player_name,
            assist_name=assist_name,
        )

    def _map_event_type(self, play: dict[str, Any]) -> Optional[str]:
        """Convierte el tipo de ESPN al tipo del modelo local."""

        raw_type = self._raw_event_type(play)
        mapped = classify_event_type(raw_type, play.get("scoringPlay") is True)
        if mapped == "unclassified":
            logger.warning("Tipo de evento no encontrado; se marcará como unclassified.")
        return mapped

    def _raw_event_type(self, play: dict[str, Any]) -> Optional[str]:
        """Devuelve el tipo original de ESPN sin transformarlo."""

        raw_type = play.get("type")
        if isinstance(raw_type, dict):
            value = raw_type.get("type")
            if isinstance(value, str) and value.strip():
                return value.strip()
            text = raw_type.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip().lower().replace(" ", "-")
        return None

    def _resolve_team_id(self, play: dict[str, Any], raw_json: dict[str, Any]) -> Optional[int]:
        """Resuelve el id del equipo a partir del JSON del play o del evento."""

        refs = []
        team_ref = play.get("team")
        if isinstance(team_ref, dict):
            refs.append(team_ref.get("$ref"))
        for participant in play.get("participants") or []:
            if isinstance(participant, dict):
                team_info = participant.get("team")
                if isinstance(team_info, dict):
                    refs.append(team_info.get("$ref"))
        for ref in refs:
            team_id = self._extract_id_from_ref(ref, "teams")
            if team_id is not None:
                return team_id
        logger.warning("team_id no encontrado; se guardará como None.")
        return None

    def _resolve_athlete_ref(self, play: dict[str, Any]) -> Optional[str]:
        """Resuelve la referencia del atleta si existe en participants."""

        participants = play.get("participants")
        if not isinstance(participants, list):
            return None
        for participant in participants:
            if not isinstance(participant, dict):
                continue
            athlete = participant.get("athlete")
            if isinstance(athlete, dict):
                athlete_ref = athlete.get("$ref")
                if isinstance(athlete_ref, str) and athlete_ref.strip():
                    return athlete_ref.strip()
        return None

    def _get_player_name(self, play: dict[str, Any]) -> Optional[str]:
        """Extrae el nombre del jugador si existe."""

        player_name = self._structured_player_name(play)
        if player_name is None:
            logger.warning("player_name no resuelto de forma estructurada; se guardará como None.")
        return player_name

    def _first_text(self, data: dict[str, Any], paths: Iterable[str]) -> Optional[str]:
        """Devuelve el primer valor textual encontrado en rutas candidatas."""

        for path in paths:
            value = self._dig(data, path)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _parse_clock(self, clock: Any) -> tuple[int, int]:
        """Convierte el clock de ESPN en minuto y segundo.

        El sample real expone `clock.value` como segundos transcurridos.
        """

        if not isinstance(clock, dict):
            logger.warning("clock ausente; minuto y segundo se fijan en 0.")
            return 0, 0
        raw_value = clock.get("value")
        if not isinstance(raw_value, (int, float)):
            logger.warning("clock.value ausente o inválido; minuto y segundo se fijan en 0.")
            return 0, 0
        minute = int(raw_value // 60)
        second = int(raw_value % 60)
        return minute, second

    def _structured_player_name(self, play: dict[str, Any]) -> Optional[str]:
        """Busca un nombre estructurado; si no existe, devuelve None."""

        participants = play.get("participants")
        if not isinstance(participants, list):
            return None
        for participant in participants:
            if not isinstance(participant, dict):
                continue
            athlete = participant.get("athlete")
            if isinstance(athlete, dict):
                name = athlete.get("displayName") or athlete.get("shortName") or athlete.get("fullName")
                if isinstance(name, str) and name.strip():
                    return name.strip()
        # TODO: confirmar si la API expone un campo estructurado adicional para nombres.
        return None

    def _extract_id_from_ref(self, ref: Any, token: str) -> Optional[int]:
        """Extrae un identificador numérico desde un $ref de ESPN."""

        if not isinstance(ref, str):
            return None
        pattern = rf"/{token}/(\d+)"
        match = re.search(pattern, ref)
        if match:
            return int(match.group(1))
        return None

    def _build_report(
        self,
        plays: list[dict[str, Any]],
        parsed_events: list[ParsedEvent],
        discarded_by_type: Counter[str],
    ) -> ParseReport:
        """Construye el reporte de cobertura y filtrado."""

        type_distribution = Counter(event.event_type for event in parsed_events)
        return ParseReport(
            total_events=len(plays),
            events_with_team_id=sum(1 for play in plays if self._resolve_team_id(play, {}) is not None),
            events_with_player_name=sum(1 for event in parsed_events if event.player_name is not None),
            events_without_clock=sum(1 for play in plays if not isinstance(play.get("clock"), dict) or play["clock"].get("value") is None),
            kept_events=len(parsed_events),
            discarded_events=sum(discarded_by_type.values()),
            event_type_distribution=dict(type_distribution),
            discarded_by_type=dict(discarded_by_type),
        )

    def _dig(self, data: dict[str, Any], path: str) -> Any:
        """Accede a rutas anidadas separadas por punto."""

        current: Any = data
        for segment in path.split("."):
            if not isinstance(current, dict):
                return None
            current = current.get(segment)
        return current


def _load_sample_json(sample_path: Path) -> dict[str, Any]:
    """Carga el JSON de ejemplo si existe."""

    if not sample_path.exists():
        raise FileNotFoundError(
            "No existe data/sample_match.json. Descárgalo con el cliente de prueba antes de ejecutar este script."
        )
    with sample_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _print_sample(events: list[dict[str, Any]]) -> None:
    """Imprime una vista resumida de los primeros eventos."""

    for idx, event in enumerate(events[:5], start=1):
        print(
            f"{idx}. {event['minute']:02d}'{event['second']:02d} "
            f"| team_id={event['team_id']} | {event['event_type']} | {event['player_name']} | {event['description']}"
        )


if __name__ == "__main__":
    """Prueba manual del parser con sample_match.json."""

    sample_path = Path("data/sample_match.json")
    try:
        sample_json = _load_sample_json(sample_path)
        parser = ESPNPlayParser()
        parsed = parser.parse(sample_json)
        _print_sample(parsed)
        print("Reporte:", parser.last_report)
        if parser.last_report and parser.last_report.total_events != 480:
            print(
                f"Aviso: el sample actual contiene {parser.last_report.total_events} eventos, "
                "no 480. ESPN cambió el feed en vivo."
            )
        assert isinstance(parsed, list)
        assert parser.last_report is not None
        assert len(parsed) == parser.last_report.kept_events
        assert all("minute" in event and "event_type" in event for event in parsed[:5])
    except FileNotFoundError as exc:
        logger.warning("%s", exc)
        print(str(exc))
