"""Conector ESPN restringido a endpoints documentados para ingesta prospectiva.

No descubre URLs externas ni imprime payloads o secretos. La red se habilita
únicamente desde el runner de Fase 7.15.

Requirements:
    requests
    tenacity

Version: 1.2.0
Created: 2026-07-16
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

LOGGER = logging.getLogger(__name__)
SITE_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
SITE_FALLBACK_BASE = "https://site.web.api.espn.com/apis/site/v2/sports/soccer"
SITE_STANDINGS_BASE = "https://site.api.espn.com/apis/v2/sports/soccer"
CORE_BASE = "https://sports.core.api.espn.com/v2/sports/soccer"
CORE_V3_BASE = "https://sports.core.api.espn.com/v3/sports/soccer"
ALLOWED_HOSTS = {
    "site.api.espn.com", "site.web.api.espn.com", "sports.core.api.espn.com",
}
PAGINATED_RESOURCES = frozenset({
    "active_athletes", "core_athletes", "core_standings", "core_teams",
    "leaders", "rankings", "season_athletes", "season_freeagents",
    "season_leaders", "seasons", "venues",
})


class EspnConnectorError(RuntimeError):
    """Error controlado de red, circuito o respuesta ESPN."""


class EspnResourceUnavailable(ValueError):
    """Recurso documentado que ESPN no publica para una liga concreta."""


@dataclass(frozen=True, slots=True)
class EspnConnectorConfig:
    """Configuración inmutable de red, caché y circuito."""

    league: str = "esp.1"
    connect_timeout_seconds: int = 5
    read_timeout_seconds: int = 20
    max_failures: int = 3
    cache_ttl_seconds: int = 300
    cache_dir: Path = Path("data/cache/espn_prospective_v1")
    user_agent: str = "dikamaha-prospective-ingestion/1.0"
    site_403_fallback_enabled: bool = True


@dataclass(frozen=True, slots=True)
class EspnRequest:
    """Solicitud ESPN normalizada antes de ejecutar transporte."""

    resource: str
    url: str
    params: dict[str, Any]


@dataclass(frozen=True, slots=True)
class EspnFetchResult:
    """Respuesta de transporte con timestamp causal del payload."""

    payload: dict[str, Any]
    http_status: int
    source_fetched_at: datetime
    from_cache: bool
    source_url: str = ""


def payload_hash(payload: Any) -> str:
    """Genera hash estable sin registrar el contenido crudo."""

    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _merge_play_pages(pages: list[dict[str, Any]]) -> dict[str, Any]:
    """Fusiona páginas sin descartar ninguna respuesta raw."""

    if not pages:
        raise EspnConnectorError("empty_plays_pages")
    items = [item for page in pages for item in page.get("items", [])]
    return {**pages[0], "items": items, "count": len(items),
            "pageIndex": 1, "pageCount": 1,
            "_sourcePageCount": len(pages), "_sourcePages": pages}


def _merge_collection_pages(pages: list[dict[str, Any]]) -> dict[str, Any]:
    """Fusiona una colección paginada y conserva cada página de origen."""

    if not pages:
        raise EspnConnectorError("empty_collection_pages")
    items = [item for page in pages for item in page.get("items", [])]
    return {
        **pages[0], "items": items, "count": len(items), "pageIndex": 1,
        "pageCount": 1, "_sourcePageCount": len(pages),
        "_sourcePages": pages,
    }


def _summary_play_payload(
    summary: dict[str, Any], core_source_pages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Convierte commentary documentado al contrato Core conservando raw."""

    teams = _summary_team_map(summary)
    items = []
    for row in summary.get("commentary", []):
        play = row.get("play") if isinstance(row, dict) else None
        if isinstance(play, dict):
            items.append(_with_team_identity(play, teams))
    core_pages = list(core_source_pages or [])
    return {
        "items": items, "count": len(items), "pageIndex": 1, "pageCount": 1,
        "_sourcePageCount": 0, "_sourcePages": [],
        "_coreSourcePageCount": len(core_pages), "_coreSourcePages": core_pages,
        "_fallbackEndpoint": "summary_commentary",
        "_fallbackSummary": summary,
    }


def _summary_team_map(summary: dict[str, Any]) -> dict[str, str]:
    """Indexa nombres ESPN a IDs declarados en el header."""

    header = summary.get("header") or {}
    competitions = header.get("competitions") or []
    competitors = competitions[0].get("competitors", []) if competitions else []
    output: dict[str, str] = {}
    for competitor in competitors:
        team = competitor.get("team") or {}
        team_id = str(team.get("id") or "")
        for key in ("displayName", "shortDisplayName", "name", "location"):
            if team_id and team.get(key):
                output[str(team[key]).casefold()] = team_id
    return output


def _with_team_identity(
    play: dict[str, Any],
    teams: dict[str, str],
) -> dict[str, Any]:
    """Completa el equipo desde la identidad incluida en el mismo summary."""

    team = play.get("team")
    if not isinstance(team, dict) or team.get("id"):
        return play
    names = [team.get(key) for key in (
        "displayName", "shortDisplayName", "name", "location"
    )]
    team_id = next(
        (teams[str(name).casefold()] for name in names
         if name and str(name).casefold() in teams),
        None,
    )
    return {**play, "team": {**team, "id": team_id}} if team_id else play


class EspnProspectiveConnector:
    """Cliente con allowlist, caché, reintentos y circuito de fallos."""

    def __init__(
        self,
        config: EspnConnectorConfig | None = None,
        session: requests.Session | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Inicializa un cliente local sin leer credenciales ni conectarse aún."""

        self.config = config or EspnConnectorConfig()
        self.session = session or requests.Session()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.session.headers.update({"User-Agent": self.config.user_agent, "Accept": "application/json"})
        self.failures = 0
        self.config.cache_dir.mkdir(parents=True, exist_ok=True)

    def scoreboard(self, date: str) -> dict[str, Any]:
        """Consulta una fecha o rango acotado con el parámetro ESPN ``dates``."""

        if not _valid_scoreboard_dates(date):
            raise EspnConnectorError("invalid_scoreboard_date")
        return self._get(f"{SITE_BASE}/{self.config.league}/scoreboard", {"dates": date})

    def scoreboard_fetch_result(self, date: str, *, use_cache: bool = True) -> EspnFetchResult:
        """Consulta scoreboard conservando metadata y permitiendo captura fresca live."""

        if len(date) != 8 or not date.isdigit():
            raise EspnConnectorError("invalid_scoreboard_date")
        return self._get_result(
            f"{SITE_BASE}/{self.config.league}/scoreboard",
            {"dates": date},
            use_cache=use_cache,
        )

    def calendar(self) -> dict[str, Any]:
        """Consulta el calendario Core documentado de la competición."""

        return self._get(f"{CORE_BASE}/leagues/{self.config.league}/calendar", {})

    def event(self, match_id: str) -> dict[str, Any]:
        """Consulta detalle de un partido mediante el endpoint Core documentado."""

        return self._get(f"{CORE_BASE}/leagues/{self.config.league}/events/{match_id}", {})

    def event_fetch_result(self, match_id: str, *, use_cache: bool = True) -> EspnFetchResult:
        """Consulta un evento conservando metadata de transporte."""

        _required_id({"match_id": match_id}, "match_id")
        return self._get_result(
            f"{CORE_BASE}/leagues/{self.config.league}/events/{match_id}",
            {},
            use_cache=use_cache,
        )

    def plays(self, match_id: str, competition_id: str) -> dict[str, Any]:
        """Consulta todas las páginas de play-by-play y conserva raw provenance."""

        return self.plays_fetch_result(match_id, competition_id).payload

    def plays_fetch_result(
        self, match_id: str, competition_id: str, *, use_cache: bool = True,
    ) -> EspnFetchResult:
        """Consulta plays paginados conservando metadata de transporte."""

        path = f"{CORE_BASE}/leagues/{self.config.league}/events/{match_id}/competitions/{competition_id}/plays"
        first = self._get_result(path, {"limit": 300, "page": 1}, use_cache=use_cache)
        page_count = int(first.payload.get("pageCount") or 1)
        if page_count < 1 or page_count > 20:
            raise EspnConnectorError("invalid_plays_page_count")
        results = [first]
        for page in range(2, page_count + 1):
            results.append(self._get_result(
                path, {"limit": 300, "page": page}, use_cache=use_cache))
        merged = _merge_play_pages([item.payload for item in results])
        if merged["items"]:
            return EspnFetchResult(
                merged, 200, max(item.source_fetched_at for item in results),
                all(item.from_cache for item in results), first.source_url)
        summary = self._get_result(
            f"{SITE_BASE}/{self.config.league}/summary",
            {"event": str(match_id)}, use_cache=use_cache)
        return EspnFetchResult(
            _summary_play_payload(
                summary.payload, [item.payload for item in results],
            ), summary.http_status,
            summary.source_fetched_at, summary.from_cache, summary.source_url)

    def summary(self, event_id: str) -> dict[str, Any]:
        """Consulta el resumen documentado del evento mediante ``summary?event``."""

        if not str(event_id).isdigit():
            raise EspnConnectorError("invalid_summary_event_id")
        return self._get(f"{SITE_BASE}/{self.config.league}/summary", {"event": str(event_id)})

    def summary_fetch_result(
        self, event_id: str, *, use_cache: bool = True,
        include_predictor: bool = False, preserve_raw: bool = False,
    ) -> EspnFetchResult:
        """Consulta summary conservando metadata de transporte."""

        if not str(event_id).isdigit():
            raise EspnConnectorError("invalid_summary_event_id")
        url = f"{SITE_BASE}/{self.config.league}/summary"
        params = {
            "event": str(event_id),
            **({"ocp": 1} if include_predictor else {}),
        }
        result = self._get_result(url, params, use_cache=use_cache)
        if preserve_raw and not use_cache:
            self._store_cache(url, params, result)
        return result

    def resource_request(self, resource: str, **identifiers: str) -> EspnRequest:
        """Construye una solicitud documentada para recursos pre-match."""

        request = self._site_resource(resource, identifiers)
        if request is not None:
            return request
        request = self._core_resource(resource, identifiers)
        if request is not None:
            return request
        raise EspnConnectorError(f"unsupported_prematch_resource:{resource}")

    def fetch_request(self, request: EspnRequest) -> dict[str, Any]:
        """Ejecuta una solicitud previamente normalizada."""

        return self._get(request.url, request.params)

    def fetch_request_result(self, request: EspnRequest, *, use_cache: bool = True) -> EspnFetchResult:
        """Ejecuta una solicitud conservando metadatos causales."""

        return self._get_result(request.url, request.params, use_cache=use_cache)

    def fetch_all_pages_result(self, request: EspnRequest, *, use_cache: bool = True) -> EspnFetchResult:
        """Obtiene una colección completa sin perder la proveniencia por página."""

        first = self._get_result(request.url, request.params, use_cache=use_cache)
        pages = self._collection_pages(request, first, use_cache=use_cache)
        merged = _merge_collection_pages([item.payload for item in pages])
        return EspnFetchResult(
            merged, 200, max(item.source_fetched_at for item in pages),
            all(item.from_cache for item in pages),
            first.source_url,
        )

    def _collection_pages(
        self, request: EspnRequest, first: EspnFetchResult, *, use_cache: bool = True,
    ) -> list[EspnFetchResult]:
        """Solicita páginas restantes con límite defensivo documentado."""

        page_count = int(first.payload.get("pageCount") or 1)
        if page_count == 0:
            page_count = 1
        if page_count < 1 or page_count > 100:
            raise EspnConnectorError("invalid_collection_page_count")
        pages = [first]
        for page in range(2, page_count + 1):
            params = {**request.params, "page": page}
            pages.append(self._get_result(request.url, params, use_cache=use_cache))
        return pages

    def _site_resource(
        self,
        resource: str,
        identifiers: dict[str, str],
    ) -> EspnRequest | None:
        """Resuelve endpoints Site API pre-match."""

        if resource == "standings":
            url = f"{SITE_STANDINGS_BASE}/{self.config.league}/standings"
            return EspnRequest(resource, url, {})
        if resource == "scoreboard":
            date = _required_date(identifiers, "date")
            url = f"{SITE_BASE}/{self.config.league}/scoreboard"
            return EspnRequest(resource, url, {"dates": date})
        if resource == "summary":
            event = _required_id(identifiers, "event_id")
            return EspnRequest(resource, f"{SITE_BASE}/{self.config.league}/summary", {"event": event})
        if resource == "news":
            return self._request(resource, SITE_BASE, "news")
        if resource == "teams":
            return self._request(resource, SITE_BASE, "teams")
        suffixes = {"team": "", "roster": "roster", "team_schedule": "schedule", "injuries": "injuries"}
        if resource in suffixes:
            team = _required_id(identifiers, "team_id")
            tail = suffixes[resource]
            suffix = f"teams/{team}/{tail}".rstrip("/")
            return self._request(resource, SITE_BASE, suffix)
        return None

    def _core_resource(
        self,
        resource: str,
        identifiers: dict[str, str],
    ) -> EspnRequest | None:
        """Resuelve endpoints Core API v2/v3 pre-match."""

        special = self._event_or_season_resource(resource, identifiers)
        if special is not None:
            return special
        suffixes = {
            "calendar": "calendar",
            "seasons": "seasons",
            "core_teams": "teams",
            "core_athletes": "athletes",
            "core_standings": "standings",
            "rankings": "rankings",
            "venues": "venues",
            "leaders": "leaders",
        }
        if resource == "active_athletes":
            url = f"{CORE_V3_BASE}/{self.config.league}/athletes"
            return EspnRequest(resource, url, {"limit": 100, "active": "true"})
        suffix = suffixes.get(resource)
        params = {} if resource == "leaders" else {"limit": 100}
        return self._request(resource, CORE_BASE, suffix, params) if suffix else None

    def _event_or_season_resource(
        self,
        resource: str,
        identifiers: dict[str, str],
    ) -> EspnRequest | None:
        """Resuelve recursos Core anidados de temporada o evento."""

        league = self.config.league
        if resource == "event":
            return self._request(resource, CORE_BASE, f"events/{_required_id(identifiers, 'event_id')}")
        if resource in {"season_athletes", "season_freeagents", "season_leaders"}:
            season = _required_id(identifiers, "season")
            name = resource.removeprefix("season_")
            return self._request(resource, CORE_BASE, f"seasons/{season}/{name}", {"limit": 100})
        if resource == "athlete":
            return self._request(resource, CORE_BASE, f"athletes/{_required_id(identifiers, 'athlete_id')}")
        if resource in {
            "competition", "odds", "officials", "broadcasts", "situation",
            "probabilities",
        }:
            event = _required_id(identifiers, "event_id")
            competition = _required_id(identifiers, "competition_id")
            suffix = f"events/{event}/competitions/{competition}"
            suffix = suffix if resource == "competition" else f"{suffix}/{resource}"
            return self._request(resource, CORE_BASE, suffix, {"limit": 100})
        if resource == "plays":
            event = _required_id(identifiers, "event_id")
            competition = _required_id(identifiers, "competition_id")
            suffix = f"events/{event}/competitions/{competition}/plays"
            return self._request(
                resource, CORE_BASE, suffix, {"limit": 300, "page": 1})
        return None

    def _request(
        self,
        resource: str,
        base: str,
        suffix: str,
        params: dict[str, Any] | None = None,
    ) -> EspnRequest:
        """Construye una solicitud bajo la liga configurada."""

        url = f"{base}/leagues/{self.config.league}/{suffix}" if base == CORE_BASE else f"{base}/{self.config.league}/{suffix}"
        return EspnRequest(resource, url, params or {})

    def _cache_path(self, url: str, params: dict[str, Any]) -> Path:
        """Construye un path de caché que no expone URL en el nombre."""

        key = payload_hash({"url": url, "params": params})
        return self.config.cache_dir / f"{key}.json"

    def _cached_result(
        self,
        url: str,
        params: dict[str, Any],
    ) -> EspnFetchResult | None:
        """Lee caché preservando el instante original de descarga."""

        path = self._cache_path(url, params)
        if not path.exists() or datetime.now(timezone.utc).timestamp() - path.stat().st_mtime > self.config.cache_ttl_seconds:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            return None
        return _decode_cache(value, path)

    def _store_cache(self, url: str, params: dict[str, Any], result: EspnFetchResult) -> None:
        """Guarda payload y timestamp causal en caché local."""

        value = {
            "_espn_cache_meta": {
                "http_status": result.http_status,
                "source_fetched_at": result.source_fetched_at.isoformat(),
                "source_url": result.source_url,
            },
            "payload": result.payload,
        }
        self._cache_path(url, params).write_text(json.dumps(value, sort_keys=True), encoding="utf-8")

    def _get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        """Ejecuta GET compatible y devuelve sólo el payload."""

        return self._get_result(url, params).payload

    @retry(retry=retry_if_exception_type((requests.RequestException, EspnConnectorError)), wait=wait_exponential(min=1, max=10), stop=stop_after_attempt(3), reraise=True)
    def _get_result(
        self, url: str, params: dict[str, Any], *, use_cache: bool = True,
    ) -> EspnFetchResult:
        """Ejecuta GET y conserva status/timestamp del origen o caché."""

        if self.failures >= self.config.max_failures:
            raise EspnConnectorError("circuit_breaker_open")
        if urlparse(url).hostname not in ALLOWED_HOSTS:
            raise EspnConnectorError("unauthorized_espn_domain")
        if use_cache:
            cached = self._cached_result(url, params)
            if cached is not None:
                return cached
        try:
            effective_url = url
            response = self.session.get(
                effective_url, params=params,
                timeout=(self.config.connect_timeout_seconds, self.config.read_timeout_seconds),
            )
            fallback_url = _site_fallback_url(url)
            if (
                response.status_code == 403
                and fallback_url is not None
                and self.config.site_403_fallback_enabled
            ):
                effective_url = fallback_url
                response = self.session.get(
                    effective_url, params=params,
                    timeout=(self.config.connect_timeout_seconds, self.config.read_timeout_seconds),
                )
            if response.status_code == 429:
                raise EspnConnectorError("espn_rate_limited")
            if 400 <= response.status_code < 500:
                raise EspnResourceUnavailable(
                    f"espn_resource_unavailable:{response.status_code}")
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise EspnConnectorError("espn_json_not_object")
        except EspnResourceUnavailable:
            raise
        except (requests.RequestException, ValueError, EspnConnectorError):
            self.failures += 1
            raise
        self.failures = 0
        result = EspnFetchResult(
            payload, response.status_code, _utc(self.clock()), False, effective_url,
        )
        if use_cache:
            self._store_cache(url, params, result)
        return result


def scoreboard_references(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extrae referencias de scoreboard sin asumir eventos malformados."""

    events = payload.get("events")
    if not isinstance(events, list):
        raise EspnConnectorError("malformed_scoreboard_events")
    refs = []
    for event in events:
        competitions = event.get("competitions") if isinstance(event, dict) else None
        if not isinstance(competitions, list) or not competitions or not isinstance(competitions[0], dict):
            continue
        event_id, competition_id = extract_event_id(event), competitions[0].get("id")
        if event_id is not None and competition_id is not None:
            refs.append({"provider_match_id": str(event_id), "competition_id": str(competition_id)})
    return sorted(refs, key=lambda row: (row["provider_match_id"], row["competition_id"]))


def extract_event_id(event: dict[str, Any]) -> str | None:
    """Extrae ``event.id`` o su referencia ESPN sin aceptar IDs inventados."""

    value = event.get("id")
    if value is not None and str(value).strip().isdigit():
        return str(value).strip()
    reference = event.get("$ref") or event.get("ref")
    if isinstance(reference, str):
        parsed = urlparse(reference)
        segments = [segment for segment in parsed.path.split("/") if segment]
        marker = segments.index("events") if "events" in segments else -1
        candidate = segments[marker + 1] if marker >= 0 and marker + 1 < len(segments) else ""
        return candidate if candidate.isdigit() else None
    return None


def sanitized_endpoint_config(config: EspnConnectorConfig) -> dict[str, Any]:
    """Expone sólo configuración operacional, nunca cabeceras o secretos."""

    return {"provider": "espn_unofficial", "league": config.league, "site_base": SITE_BASE,
            "site_fallback_base": SITE_FALLBACK_BASE, "core_base": CORE_BASE,
            "allowed_hosts": sorted(ALLOWED_HOSTS), "timeouts": {"connect": config.connect_timeout_seconds, "read": config.read_timeout_seconds},
            "max_failures": config.max_failures, "user_agent_configured": bool(config.user_agent),
            "site_403_fallback_enabled": config.site_403_fallback_enabled, "api_key_used": False}


def _required_id(identifiers: dict[str, str], name: str) -> str:
    """Valida identificadores ESPN usados dentro de rutas."""

    value = str(identifiers.get(name, "")).strip()
    if not value or not all(character.isalnum() or character in "._-" for character in value):
        raise EspnConnectorError(f"invalid_identifier:{name}")
    return value


def _required_date(identifiers: dict[str, str], name: str) -> str:
    """Valida una fecha ESPN compacta."""

    value = str(identifiers.get(name, "")).strip()
    if len(value) != 8 or not value.isdigit():
        raise EspnConnectorError(f"invalid_identifier:{name}")
    return value


def _valid_scoreboard_dates(value: str) -> bool:
    """Acepta fecha única o rango inclusivo de hasta 31 días."""

    parts = value.split("-")
    if len(parts) not in {1, 2} or any(len(part) != 8 or not part.isdigit() for part in parts):
        return False
    try:
        dates = [datetime.strptime(part, "%Y%m%d").date() for part in parts]
    except ValueError:
        return False
    return len(dates) == 1 or dates[0] <= dates[1] <= dates[0] + timedelta(days=30)


def _decode_cache(value: dict[str, Any], path: Path) -> EspnFetchResult | None:
    """Decodifica caché v1.1 y admite payloads legacy por mtime."""

    metadata = value.get("_espn_cache_meta")
    payload = value.get("payload")
    if isinstance(metadata, dict) and isinstance(payload, dict):
        fetched_at = datetime.fromisoformat(str(metadata["source_fetched_at"]))
        return EspnFetchResult(
            payload, int(metadata["http_status"]), fetched_at, True,
            str(metadata.get("source_url") or ""),
        )
    legacy_time = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return EspnFetchResult(value, 200, legacy_time, True)


def _site_fallback_url(url: str) -> str | None:
    """Conserva cualquier path Site HTTPS y cambia sólo al host alternativo."""

    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "site.api.espn.com":
        return None
    fallback = parsed._replace(netloc="site.web.api.espn.com").geturl()
    if urlparse(fallback).hostname not in ALLOWED_HOSTS:
        raise EspnConnectorError("unauthorized_espn_fallback_domain")
    return fallback


def _utc(value: datetime) -> datetime:
    """Normaliza timestamps de transporte a UTC."""

    if value.tzinfo is None:
        raise EspnConnectorError("timezone_required")
    return value.astimezone(timezone.utc)


# Version: 1.2.0
# Created: 2026-07-16; updated: 2026-07-28
