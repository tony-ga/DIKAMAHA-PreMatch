"""Cliente para consumir la API no oficial de ESPN para fútbol.

Base URL identificada en la documentación local:
- Core API v2: https://sports.core.api.espn.com/v2/sports/soccer/

Autenticación:
- No requiere autenticación según la documentación.

Endpoints relevantes:
- Detalle de evento: /events/{event}
- Play-by-play: /events/{event}/competitions/{competition}/plays?limit=300
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import requests
from dotenv import load_dotenv
from requests import Response, Session
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

load_dotenv()

logger = logging.getLogger("espn_client")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)


class ESPNClientError(RuntimeError):
    """Error base del cliente ESPN."""


class ESPNAPIError(ESPNClientError):
    """Error lanzado cuando la API responde con un estado no exitoso."""


class ESPNNotFoundError(ESPNClientError):
    """Error lanzado cuando ESPN responde 404 para un recurso inexistente."""


@dataclass(slots=True)
class CacheEntry:
    """Representa un valor cacheado en disco."""

    fetched_at: str
    payload: Any


class ESPNClient:
    """Cliente HTTP con retry y caché para la API de ESPN."""

    def __init__(
        self,
        league: str,
        cache_dir: str | Path | None = None,
        cache_ttl_seconds: int = 3600,
        timeout_seconds: int = 20,
    ) -> None:
        self.league = league
        self.base_url = f"https://sports.core.api.espn.com/v2/sports/soccer/leagues/{league}"
        self.cache_ttl_seconds = cache_ttl_seconds
        self.timeout_seconds = timeout_seconds
        self.cache_dir = Path(cache_dir or os.getenv("CACHE_DIR", "./data/cache")) / "espn"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.user_agent = os.getenv("USER_AGENT", os.getenv("ESPN_USER_AGENT", "futbol-predictor/0.1"))
        self.api_key = os.getenv("API_KEY")
        self.session = Session()
        self.session.headers.update(self._build_headers())

    def _build_headers(self) -> dict[str, str]:
        """Construye las cabeceras HTTP.

        Returns:
            dict[str, str]: Cabeceras con User-Agent y API key opcional.
        """

        headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    def get_event(self, event_id: str) -> dict[str, Any]:
        """Obtiene el detalle de un evento.

        Args:
            event_id: Identificador del evento.

        Returns:
            dict[str, Any]: JSON del evento.
        """

        url = f"{self.base_url}/events/{event_id}"
        return self._get_json(url)

    def get_play_by_play(self, event_id: str, competition_id: str, limit: int = 300) -> dict[str, Any]:
        """Obtiene el play-by-play de un partido.

        Args:
            event_id: Identificador del evento.
            competition_id: Identificador de la competencia del evento.
            limit: Cantidad máxima de jugadas a solicitar.

        Returns:
            dict[str, Any]: JSON con la cronología del partido.
        """

        url = f"{self.base_url}/events/{event_id}/competitions/{competition_id}/plays?limit={limit}"
        return self._get_json(url)

    def get_play_by_play_all(self, event_id: str, competition_id: str, limit: int = 300) -> dict[str, Any]:
        """Obtiene todas las páginas del play-by-play y consolida los items.

        Args:
            event_id: Identificador del evento.
            competition_id: Identificador de la competencia del evento.
            limit: Tamaño de página solicitado a ESPN.

        Returns:
            dict[str, Any]: Respuesta consolidada con todos los `items`.
        """

        first_page = self.get_play_by_play(event_id, competition_id, limit=limit)
        page_count = int(first_page.get("pageCount") or 1)
        items = list(first_page.get("items") or [])
        for page in range(2, page_count + 1):
            page_payload = self._get_json(
                f"{self.base_url}/events/{event_id}/competitions/{competition_id}/plays?limit={limit}&page={page}"
            )
            items.extend(page_payload.get("items") or [])
        first_page["items"] = items
        first_page["pageCount"] = page_count
        first_page["count"] = first_page.get("count", len(items))
        logger.info("Play-by-play consolidado: count=%s items=%s pageCount=%s", first_page["count"], len(items), page_count)
        return first_page

    def _cache_key(self, url: str) -> Path:
        """Genera la ruta del archivo de caché para una URL."""

        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def _load_cache(self, url: str) -> Optional[Any]:
        """Carga una respuesta cacheada si sigue vigente."""

        cache_file = self._cache_key(url)
        if not cache_file.exists():
            return None
        age_seconds = datetime.now(timezone.utc).timestamp() - cache_file.stat().st_mtime
        if age_seconds > self.cache_ttl_seconds:
            return None
        with cache_file.open("r", encoding="utf-8") as handle:
            cached = json.load(handle)
        return cached.get("payload")

    def _save_cache(self, url: str, payload: Any) -> None:
        """Guarda una respuesta en caché."""

        cache_file = self._cache_key(url)
        entry = CacheEntry(fetched_at=datetime.now(timezone.utc).isoformat(), payload=payload)
        with cache_file.open("w", encoding="utf-8") as handle:
            json.dump(asdict(entry), handle, ensure_ascii=False, indent=2)

    @retry(
        retry=retry_if_exception_type((requests.RequestException, ESPNAPIError)),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def _get_json(self, url: str) -> dict[str, Any]:
        """Ejecuta un GET con retry y caché."""

        cached = self._load_cache(url)
        if cached is not None:
            logger.info("Cache hit: %s", url)
            return cached
        logger.info("GET ESPN: %s", url)
        response = self.session.get(url, timeout=self.timeout_seconds)
        self._raise_for_status(response)
        payload = response.json()
        self._save_cache(url, payload)
        return payload

    def _raise_for_status(self, response: Response) -> None:
        """Valida la respuesta HTTP y levanta un error claro si falla."""

        if response.ok:
            return
        message = f"ESPN API respondió {response.status_code} para {response.url}"
        logger.error(message)
        if response.status_code == 404:
            raise ESPNNotFoundError(message)
        raise ESPNAPIError(message)


def _extract_competition_id(event_payload: dict[str, Any]) -> Optional[str]:
    """Extrae de forma defensiva el competition id.

    TODO: confirmar estructura exacta del JSON en la documentación.
    """

    competitions = event_payload.get("competitions")
    if not isinstance(competitions, list) or not competitions:
        return None
    first_competition = competitions[0]
    if not isinstance(first_competition, dict):
        return None
    identifier = first_competition.get("id")
    return str(identifier) if identifier is not None else None


if __name__ == "__main__":
    """Prueba básica de conexión con un evento de ejemplo."""

    client = ESPNClient(league="eng.1")
    sample_event_id = "123456"
    try:
        event = client.get_event(sample_event_id)
        competition_id = _extract_competition_id(event)
        print(json.dumps(event, ensure_ascii=False, indent=2)[:1000])
        if competition_id:
            plays = client.get_play_by_play(sample_event_id, competition_id)
            print(json.dumps(plays, ensure_ascii=False, indent=2)[:1000])
        else:
            print("# TODO: confirmar estructura en la documentación para obtener competition_id.")
    except ESPNNotFoundError as exc:
        print(f"Evento de ejemplo no encontrado: {exc}")
    except Exception as exc:  # pragma: no cover - prueba manual con manejo elegante
        logger.error("Prueba ESPNClient falló para event_id=%s: %s", sample_event_id, exc)
        print(f"Prueba controlada falló: {exc}")
