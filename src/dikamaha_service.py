"""Servicio FastAPI local/operativo de inferencia DIKAMAHA v1.

El modo local no abre conexiones externas ni persiste resultados. El modo
operativo de sólo lectura puede resolver fixtures ESPN, sin escribir datos.
La configuración es inmutable durante la vida de la aplicación.

Requirements:
    - fastapi
    - pydantic

Version: 1.6.0
Created: 2026-07-15
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import json
import math
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field as dataclass_field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import quantiles
from typing import Any
from zoneinfo import ZoneInfo

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

try:
    from src.dikamaha_inference import (
        CONTRACT_VERSION as INFERENCE_CONTRACT_VERSION,
        DikamahaInferenceEngine,
        LiveSnapshotInput,
        PreMatchInput,
    )
except ModuleNotFoundError:  # pragma: no cover - ejecucion directa desde src
    from dikamaha_inference import (
        CONTRACT_VERSION as INFERENCE_CONTRACT_VERSION,
        DikamahaInferenceEngine,
        LiveSnapshotInput,
        PreMatchInput,
    )

try:
    from src.prematch_shadow_catalog import build_shadow_observation, load_shadow_catalog
except ModuleNotFoundError:  # pragma: no cover - ejecucion directa desde src
    from prematch_shadow_catalog import build_shadow_observation, load_shadow_catalog

try:
    from src.high_probability_view import HighProbabilityView
except ModuleNotFoundError:  # pragma: no cover - ejecucion directa desde src
    from high_probability_view import HighProbabilityView

try:
    from src.parlay_eligibility_v1 import (
        ParlayEligibilityError, ParlayEligibilityView)
except ModuleNotFoundError:  # pragma: no cover - ejecucion directa desde src
    from parlay_eligibility_v1 import (
        ParlayEligibilityError, ParlayEligibilityView)

try:
    from src.catalog_cache_store import build_store as build_catalog_cache_store
except ModuleNotFoundError:  # pragma: no cover - ejecucion directa desde src
    from catalog_cache_store import build_store as build_catalog_cache_store

try:
    from src.settlement_store import (
        DEFAULT_WINDOW,
        MAXIMUM_WINDOW,
        build_repository as build_settlement_repository,
        track_record,
    )
except ModuleNotFoundError:  # pragma: no cover - ejecucion directa desde src
    from settlement_store import (
        DEFAULT_WINDOW,
        MAXIMUM_WINDOW,
        build_repository as build_settlement_repository,
        track_record,
    )

try:
    from src.high_probability_settlement import (
        build_repository as build_high_probability_pick_repository,
        pick_view,
        prospective_reliability,
    )
except ModuleNotFoundError:  # pragma: no cover - ejecucion directa desde src
    from high_probability_settlement import (
        build_repository as build_high_probability_pick_repository,
        pick_view,
        prospective_reliability,
    )

try:
    from src.universal_prematch import PrematchUnavailableError, UniversalPrematchEngine, UpcomingMatchInput
    from src.espn_fixture_resolver import FixtureLookup, FixtureResolutionError, allocate_fixtures_fairly, connector_for_league, scoreboard_fixtures
    from src.espn_prospective_connector import EspnConnectorConfig, EspnConnectorError, EspnProspectiveConnector
    from src.espn_user_explorer import LEAGUES, EspnFootballDataExplorer, explorer_dates
    from src.espn_fixture_context import default_context_service
    from src.live_prediction_runtime import LivePredictionRuntime, model_inventory
    from src.prematch_snapshot_registry import SnapshotRegistryError, resolve_active_snapshot
    from src.provider_media import ProviderMediaError, fetch_transparent_png
    from src.provider_match_context import ProviderMatchContextService
except ModuleNotFoundError:  # pragma: no cover - ejecucion directa desde src
    from universal_prematch import PrematchUnavailableError, UniversalPrematchEngine, UpcomingMatchInput
    from espn_fixture_resolver import FixtureLookup, FixtureResolutionError, allocate_fixtures_fairly, connector_for_league, scoreboard_fixtures
    from espn_prospective_connector import EspnConnectorConfig, EspnConnectorError, EspnProspectiveConnector
    from espn_user_explorer import LEAGUES, EspnFootballDataExplorer, explorer_dates
    from espn_fixture_context import default_context_service
    from live_prediction_runtime import LivePredictionRuntime, model_inventory
    from prematch_snapshot_registry import SnapshotRegistryError, resolve_active_snapshot
    from provider_media import ProviderMediaError, fetch_transparent_png
    from provider_match_context import ProviderMatchContextService

LOGGER = logging.getLogger(__name__)
SERVICE_VERSION = "dikamaha_local_service_v2.0_high_probability"
MEXICO_TZ = ZoneInfo("America/Mexico_City")
# Cota de fixtures que barre `/v1/high-probability`. Cada uno cuesta una
# inferencia completa; la caché TTL absorbe las repeticiones dentro del día.
HIGH_PROBABILITY_FIXTURES = 30
# Con caché fría (partidos que nadie vio todavía, típicamente los de mañana),
# un barrido secuencial de hasta 30 inferencias monopolizaba el pool de hilos
# compartido el tiempo completo; hasta /v1/models, un diccionario en memoria
# sin E/S, quedaba en cola detrás de eso y tardaba segundos. La concurrencia
# acotada solapa las esperas de E/S sin saturar el pool, y el presupuesto de
# tiempo devuelve resultados parciales en vez de bloquear indefinidamente,
# mismo principio que `daily_partial_failure` del publicador de Fase 101.
HIGH_PROBABILITY_CONCURRENCY = 4
HIGH_PROBABILITY_WALL_CLOCK_BUDGET_SECONDS = 18.0
PARLAY_STATUS = "experimental_shadow_not_promoted"
PARLAY_DISCLOSURE = (
    "Menú experimental sin validación prospectiva. Cada pierna superó un gate "
    "de ventaja, calibración y estabilidad, pero la probabilidad conjunta "
    "declarada se cumple sólo entre el 94% y el 97% de las veces según la "
    "medición fuera de muestra. No es un consejo de apuesta."
)
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
PUBLIC_PATHS = frozenset({"/v1/health", "/v1/readiness"})
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


class RequestGate:
    """Control thread-safe de concurrencia y rate limit local."""

    def __init__(self, max_concurrent: int, rate_limit: int, window_seconds: int) -> None:
        """Inicializa los límites operativos in-memory."""

        self._lock = threading.Lock()
        self._active = 0
        self._max_concurrent = max_concurrent
        self._rate_limit = rate_limit
        self._window_seconds = window_seconds
        self._requests: dict[str, list[float]] = {}

    def enter(self, client_id: str, now: float) -> str | None:
        """Reserva capacidad o devuelve el código del rechazo."""

        with self._lock:
            recent = self._recent(client_id, now)
            if len(recent) >= self._rate_limit:
                return "rate_limit_exceeded"
            if self._active >= self._max_concurrent:
                return "concurrency_limit_exceeded"
            recent.append(now)
            self._requests[client_id] = recent
            self._active += 1
        return None

    def leave(self) -> None:
        """Libera una reserva de concurrencia."""

        with self._lock:
            self._active = max(0, self._active - 1)

    def _recent(self, client_id: str, now: float) -> list[float]:
        """Descarta entradas fuera de la ventana."""

        cutoff = now - self._window_seconds
        return [item for item in self._requests.get(client_id, []) if item >= cutoff]


class MetricsStore:
    """Contadores y latencias acotados en memoria del proceso."""

    def __init__(self, max_samples: int = 1000) -> None:
        """Inicializa almacenamiento thread-safe y acotado."""

        self._lock = threading.Lock()
        self._max_samples = max_samples
        self._requests: dict[str, int] = {}
        self._status: dict[str, int] = {}
        self._latencies: dict[str, list[float]] = {}
        self._counters = {"validation_errors": 0, "pre_match_responses": 0, "live_responses": 0, "hawkes_enabled": 0, "hawkes_disabled": 0, "hawkes_shadow_enabled": 0, "markov_live_enabled": 0, "markov_live_disabled": 0, "combined_live_shadow_enabled": 0, "live_probability_engine_official": 0, "live_probability_engine_fallback": 0, "leakage_rejections": 0, "match_704766_rejections": 0}

    def observe(self, endpoint: str, status: int, duration_ms: float, error_code: str | None) -> None:
        """Registra una request sin almacenar su payload."""

        with self._lock:
            self._requests[endpoint] = self._requests.get(endpoint, 0) + 1
            key = f"{endpoint}:{status}"
            self._status[key] = self._status.get(key, 0) + 1
            samples = self._latencies.setdefault(endpoint, [])
            samples.append(round(duration_ms, 3))
            del samples[:-self._max_samples]
            if endpoint in {"/v1/predict/live", "/v1/predict/live/fixture"} and status < 400:
                self._counters["live_responses"] += 1
            elif endpoint.endswith(("pre-match", "upcoming", "fixture")) and status < 400:
                self._counters["pre_match_responses"] += 1
            if error_code == "contract_validation_error":
                self._counters["validation_errors"] += 1
            if error_code == "temporal_leakage":
                self._counters["leakage_rejections"] += 1
            if error_code == "blocked_match_704766":
                self._counters["match_704766_rejections"] += 1

    def mark_hawkes(self, enabled: bool, shadow_mode: bool) -> None:
        """Registra la bandera Hawkes sin registrar eventos."""

        with self._lock:
            self._counters["hawkes_enabled" if enabled else "hawkes_disabled"] += 1
            if enabled and shadow_mode:
                self._counters["hawkes_shadow_enabled"] += 1

    def mark_live_layers(self, markov_enabled: bool, combined_enabled: bool) -> None:
        """Registra activación de capas live sin almacenar el payload."""

        with self._lock:
            self._counters["markov_live_enabled" if markov_enabled else "markov_live_disabled"] += 1
            if markov_enabled and combined_enabled:
                self._counters["combined_live_shadow_enabled"] += 1

    def mark_live_probability_engine(self, source: str) -> None:
        """Registra promoción o fallback sin conservar el payload."""

        with self._lock:
            key = (
                "live_probability_engine_official"
                if source == "live_probability_engine_v1"
                else "live_probability_engine_fallback"
            )
            self._counters[key] += 1

    def snapshot(self) -> dict[str, Any]:
        """Devuelve métricas serializables y agregadas."""

        with self._lock:
            latency = {key: self._percentiles(values) for key, values in self._latencies.items()}
            return {"requests_by_endpoint": dict(self._requests), "responses_by_status": dict(self._status), "latency_ms": latency, "counters": dict(self._counters)}

    @staticmethod
    def _percentiles(values: list[float]) -> dict[str, float | None]:
        """Calcula p50/p95 sin fallar con muestras pequeñas."""

        if not values:
            return {"p50": None, "p95": None}
        if len(values) == 1:
            return {"p50": values[0], "p95": values[0]}
        points = quantiles(values, n=100, method="inclusive")
        return {"p50": points[49], "p95": points[94]}


class AsyncPredictionCache:
    """Caché con single-flight y refresco en segundo plano por clave.

    Dos umbrales, no uno. Dentro de `ttl_seconds` la entrada se sirve tal
    cual. Entre `ttl_seconds` y `stale_ttl_seconds` se sirve igualmente -al
    instante- y el recálculo se lanza por detrás: quien pregunta recibe un
    dato con unos segundos de edad en vez de esperar el barrido completo.
    Sólo pasado `stale_ttl_seconds` se descarta la entrada y el llamador sí
    espera al cálculo real.

    Existe porque el barrido de catálogo cuesta ~30 s contra 63 ligas x 3
    días mientras la caché sólo vivía 25 s: con el tráfico de un grupo
    privado casi ninguna apertura de la Mini App caía dentro de esa ventana,
    así que en la práctica cada usuario pagaba el barrido entero. Servir el
    dato vencido y refrescarlo detrás saca ese barrido del camino crítico sin
    envejecer el dato más de lo que el propio cliente ya tolera.

    `stamp_age` añade `data_age_seconds` a la respuesta para que la interfaz
    pueda decir la edad real del dato. Es opcional a propósito: la caché de
    predicciones guarda payloads de contrato con hash de integridad, y
    añadirles un campo variable los invalidaría.

    Con un `store` conectado la memoria pasa a ser sólo el primer nivel: en un
    fallo se consulta PostgreSQL antes de recalcular. Sin él la caché muere en
    cada despliegue y el primer usuario posterior paga el barrido entero.
    """

    def __init__(
        self,
        ttl_seconds: float = 300.0,
        max_entries: int = 256,
        stale_ttl_seconds: float | None = None,
        stamp_age: bool = False,
        store: Any | None = None,
        namespace: str = "",
    ) -> None:
        """Inicializa una caché acotada, opcionalmente persistida."""

        self._namespace = namespace
        self._ttl = ttl_seconds
        self._stale_ttl = max(
            ttl_seconds if stale_ttl_seconds is None else stale_ttl_seconds,
            ttl_seconds,
        )
        self._max_entries = max_entries
        self._stamp_age = stamp_age
        self._store = store
        self._values: dict[str, tuple[float, dict[str, Any]]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._refreshing: set[str] = set()
        self._refresh_tasks: set[asyncio.Task[None]] = set()

    async def get_or_compute(self, key: str, factory: Any) -> dict[str, Any]:
        """Devuelve una copia servible o calcula una sola vez por clave."""

        cached, age = self._get(key)
        if cached is not None:
            if age > self._ttl:
                self._schedule_refresh(key, factory)
            return self._stamp(cached, age)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached, age = self._get(key)
            if cached is None:
                cached, age = await self._restore_or_compute(key, factory)
        self._locks.pop(key, None)
        # Una entrada recuperada de PostgreSQL puede venir ya vencida -tras un
        # reinicio, con minutos de antigüedad-: se sirve igual y se refresca
        # detrás, en vez de hacer esperar a quien acaba de abrir la Mini App.
        if age > self._ttl:
            self._schedule_refresh(key, factory)
        return self._stamp(cached, age)

    async def _restore_or_compute(
        self, key: str, factory: Any,
    ) -> tuple[dict[str, Any], float]:
        """Recupera el catálogo persistido o, si no hay, lo calcula de verdad."""

        restored = await self._load_from_store(key)
        if restored is not None:
            value, age = restored
            self._put(key, value, age)
            return value, age
        value = dict(await factory())
        self._put(key, value)
        self._save_to_store(key, value)
        return value, 0.0

    async def _load_from_store(
        self, key: str,
    ) -> tuple[dict[str, Any], float] | None:
        """Lee el nivel persistente fuera del event loop.

        El acceso a PostgreSQL es SQLAlchemy síncrono, así que hacerlo en línea
        bloquearía el bucle y frenaría al resto de peticiones en vuelo.
        """

        if self._store is None:
            return None
        restored = await asyncio.to_thread(self._store.load, self._stored_key(key))
        if restored is None:
            return None
        value, age = restored
        # Más vieja que la ventana servible: no aporta nada sobre recalcular.
        return None if age >= self._stale_ttl else (dict(value), age)

    def _save_to_store(self, key: str, value: dict[str, Any]) -> None:
        """Persiste el catálogo sin que nadie espere a que termine."""

        if self._store is None:
            return
        task = asyncio.create_task(asyncio.to_thread(
            self._store.save, self._stored_key(key), dict(value), self._stale_ttl))
        self._refresh_tasks.add(task)
        task.add_done_callback(self._refresh_tasks.discard)

    def _stored_key(self, key: str) -> str:
        """Separa en la tabla compartida a cachés con claves indistinguibles.

        Los catálogos de próximos y en vivo se identifican por las mismas
        ligas y fecha, así que sin este prefijo el segundo en escribir
        sobrescribiría al primero y la Mini App recibiría fixtures en vivo
        donde esperaba próximos.
        """

        return f"{self._namespace}:{key}" if self._namespace else key

    def _schedule_refresh(self, key: str, factory: Any) -> None:
        """Recalcula por detrás sin bloquear a quien ya recibió el dato."""

        if key in self._refreshing:
            return
        self._refreshing.add(key)
        task = asyncio.create_task(self._refresh(key, factory))
        # asyncio sólo mantiene una referencia débil a las tareas en vuelo, de
        # modo que sin este conjunto el recolector puede cancelar el refresco a
        # medias y la entrada nunca se renovaría.
        self._refresh_tasks.add(task)
        task.add_done_callback(self._refresh_tasks.discard)

    async def _refresh(self, key: str, factory: Any) -> None:
        """Renueva una entrada vencida absorbiendo cualquier fallo.

        Un refresco fallido no puede propagarse: nadie está esperando esta
        tarea y la entrada vencida se sigue sirviendo hasta `stale_ttl`, así
        que el siguiente lector simplemente vuelve a intentarlo.
        """

        try:
            value = dict(await factory())
            self._put(key, value)
            self._save_to_store(key, value)
        except Exception as exc:  # noqa: BLE001 - ver docstring
            LOGGER.warning("Refresco en segundo plano fallido: %s", exc)
        finally:
            self._refreshing.discard(key)

    def _stamp(self, value: dict[str, Any], age: float) -> dict[str, Any]:
        """Copia la entrada y, si procede, publica su edad real."""

        payload = dict(value)
        if self._stamp_age:
            payload["data_age_seconds"] = round(age, 1)
        return payload

    def _get(self, key: str) -> tuple[dict[str, Any] | None, float]:
        """Lee una entrada servible con su edad y elimina las agotadas."""

        row = self._values.get(key)
        if row is None:
            return None, 0.0
        age = time.monotonic() - row[0]
        if age >= self._stale_ttl:
            self._values.pop(key, None)
            return None, 0.0
        return dict(row[1]), age

    def _put(self, key: str, value: dict[str, Any], age: float = 0.0) -> None:
        """Inserta una copia y conserva un número máximo de entradas.

        `age` permite reinsertar en memoria algo recuperado de PostgreSQL sin
        rejuvenecerlo: una entrada calculada hace tres minutos debe seguir
        contando como tal, o el refresco en segundo plano nunca se dispararía.
        """

        if key not in self._values and len(self._values) >= self._max_entries:
            oldest = min(self._values, key=lambda item: self._values[item][0])
            self._values.pop(oldest, None)
        self._values[key] = (time.monotonic() - age, dict(value))


def _request_id(request: Request) -> str:
    """Propaga un request id seguro o genera uno nuevo."""

    candidate = request.headers.get("X-Request-ID", "")
    return candidate if REQUEST_ID_PATTERN.fullmatch(candidate) else uuid.uuid4().hex


def _error_code(detail: str) -> str:
    """Clasifica errores sin registrar detalles sensibles."""

    if "704766" in detail:
        return "blocked_match_704766"
    if "cutoff" in detail or "event_ts" in detail or "snapshot_ts" in detail:
        return "temporal_leakage"
    return "contract_validation_error"


def _structured_log(request: Request, request_id: str, status: int, duration_ms: float, config: ServiceConfig, error_code: str | None) -> str:
    """Construye una línea JSON sin cuerpos ni credenciales."""

    payload = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "request_id": request_id, "endpoint": request.url.path[:128], "method": request.method, "status_code": status, "duration_ms": round(duration_ms, 3), "contract": config.contract_version, "dixon_coles_version": config.dixon_coles_version, "kalman_version": config.kalman_version, "markov_version": config.markov_version, "hawkes_enabled": config.hawkes_enabled, "hawkes_shadow_mode": config.hawkes_shadow_mode}
    if error_code:
        payload["error_code"] = error_code
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class ServiceConfig:
    """Configuración inmutable del servicio local u operativo de sólo lectura."""

    mode: str = "local_dry_run"
    contract_version: str = INFERENCE_CONTRACT_VERSION
    dixon_coles_version: str = "dixon_coles_v1"
    kalman_version: str = "kalman_v2"
    markov_version: str = "markov_v1"
    hawkes_version: str = "hawkes_v1"
    markov_live_version: str = "markov_live_v1_shadow"
    hawkes_live_version: str = "hawkes_live_v2_residual_shadow"
    hawkes_enabled: bool = False
    hawkes_shadow_mode: bool = False
    official_prediction: bool = False
    external_calls_enabled: bool = False
    persistence_enabled: bool = False
    authentication_enabled: bool = False
    api_key: str | None = dataclass_field(default=None, repr=False)
    max_request_bytes: int = 65536
    rate_limit_requests: int = 600
    rate_limit_window_seconds: int = 60
    inference_timeout_seconds: float = 10.0
    max_concurrent_requests: int = 16
    allowed_origins: tuple[str, ...] = ()
    openapi_local_only: bool = True
    prematch_snapshot_id: str | None = None
    prematch_snapshot_root: str | None = None
    live_probability_engine_enabled: bool = True
    live_probability_engine_official: bool = True
    live_monte_carlo_diagnostic: bool = True
    live_monte_carlo_simulations: int = 20_000
    live_engine_refresh_seconds: int = 15
    live_engine_fallback_enabled: bool = True
    # Refresca en segundo plano las claves canónicas de catálogo para que nadie
    # llegue nunca a una caché fría. Por defecto `False` y activado sólo desde
    # `service_config_from_env`: una aplicación construida a mano -tests,
    # scripts- no debe empezar a barrer ESPN por el mero hecho de arrancar.
    catalog_warmer_enabled: bool = False

    def __post_init__(self) -> None:
        """Rechaza configuraciones incompatibles con el alcance local."""

        if self.mode not in {"local_dry_run", "operational_readonly"}:
            raise ValueError("Modo de servicio no permitido.")
        if self.external_calls_enabled and self.mode != "operational_readonly":
            raise ValueError("Las llamadas externas requieren operational_readonly.")
        if self.persistence_enabled:
            raise ValueError("El servicio de inferencia no permite persistencia.")
        if self.hawkes_enabled != self.hawkes_shadow_mode:
            raise ValueError("La configuración Hawkes shadow debe activar ambas banderas.")
        if self.official_prediction and self.hawkes_enabled:
            raise ValueError("Hawkes shadow no puede activarse para predicciones oficiales.")
        if self.authentication_enabled and not self.api_key:
            raise ValueError("La autenticación requiere una API key de runtime.")
        if self.max_request_bytes < 1024 or self.max_concurrent_requests < 1:
            raise ValueError("Los límites de request y concurrencia son inválidos.")
        if self.rate_limit_requests < 1 or self.rate_limit_window_seconds < 1:
            raise ValueError("La configuración de rate limiting es inválida.")
        if self.inference_timeout_seconds <= 0:
            raise ValueError("El timeout de inferencia debe ser positivo.")
        if self.live_probability_engine_official and not self.live_probability_engine_enabled:
            raise ValueError("El motor live oficial requiere habilitar el motor.")
        if not 1_000 <= self.live_monte_carlo_simulations <= 100_000:
            raise ValueError("Las simulaciones Monte Carlo deben estar entre 1000 y 100000.")
        if not 5 <= self.live_engine_refresh_seconds <= 60:
            raise ValueError("El refresco live debe estar entre 5 y 60 segundos.")


def _env_bool(name: str, default: bool) -> bool:
    """Lee una bandera booleana de entorno."""

    value = os.getenv(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes"}


def service_config_from_env() -> ServiceConfig:
    """Construye la configuración inmutable sin exponer secretos."""

    origins = tuple(item for item in os.getenv("DIKAMAHA_ALLOWED_ORIGINS", "").split(",") if item)
    return ServiceConfig(
        mode=os.getenv("DIKAMAHA_MODE", "local_dry_run"),
        external_calls_enabled=_env_bool("DIKAMAHA_EXTERNAL_CALLS_ENABLED", False),
        authentication_enabled=_env_bool("DIKAMAHA_AUTH_ENABLED", False),
        api_key=os.getenv("DIKAMAHA_API_KEY"),
        max_request_bytes=int(os.getenv("DIKAMAHA_MAX_REQUEST_BYTES", "65536")),
        rate_limit_requests=int(os.getenv("DIKAMAHA_RATE_LIMIT_REQUESTS", "600")),
        rate_limit_window_seconds=int(os.getenv("DIKAMAHA_RATE_LIMIT_WINDOW_SECONDS", "60")),
        inference_timeout_seconds=float(os.getenv("DIKAMAHA_INFERENCE_TIMEOUT_SECONDS", "10")),
        max_concurrent_requests=int(os.getenv("DIKAMAHA_MAX_CONCURRENT_REQUESTS", "16")),
        allowed_origins=origins,
        prematch_snapshot_id=os.getenv("DIKAMAHA_PREMATCH_SNAPSHOT_ID") or None,
        prematch_snapshot_root=os.getenv("DIKAMAHA_PREMATCH_SNAPSHOT_ROOT") or None,
        live_probability_engine_enabled=_env_bool(
            "LIVE_PROBABILITY_ENGINE_ENABLED", True),
        live_probability_engine_official=_env_bool(
            "LIVE_PROBABILITY_ENGINE_OFFICIAL", True),
        live_monte_carlo_diagnostic=_env_bool(
            "LIVE_MONTE_CARLO_DIAGNOSTIC", True),
        live_monte_carlo_simulations=int(os.getenv(
            "LIVE_MONTE_CARLO_SIMULATIONS", "20000")),
        live_engine_refresh_seconds=int(os.getenv(
            "LIVE_ENGINE_REFRESH_SECONDS", "15")),
        live_engine_fallback_enabled=_env_bool(
            "LIVE_ENGINE_FALLBACK_ENABLED", True),
        catalog_warmer_enabled=_env_bool(
            "DIKAMAHA_CATALOG_WARMER_ENABLED", True),
    )


def _public_config(config: ServiceConfig) -> dict[str, Any]:
    """Serializa configuración sin incluir la API key."""

    payload = asdict(config)
    payload.pop("api_key", None)
    payload["api_key_configured"] = bool(config.api_key)
    return payload


def _client_id(request: Request) -> str:
    """Obtiene identidad local sin confiar en cabeceras proxy."""

    return request.client.host if request.client else "local"


def _auth_valid(request: Request, config: ServiceConfig) -> bool:
    """Valida una API key sin comparaciones dependientes del contenido."""

    if not config.authentication_enabled or request.url.path in PUBLIC_PATHS:
        return True
    supplied = request.headers.get("X-Dikamaha-Key", "")
    return bool(config.api_key) and hmac.compare_digest(supplied, config.api_key)


def _origin_valid(request: Request, config: ServiceConfig) -> bool:
    """Aplica una política CORS restrictiva."""

    origin = request.headers.get("Origin")
    return origin is None or origin in config.allowed_origins


def _gate_error(code: str, status: int) -> JSONResponse:
    """Construye un rechazo de perímetro sin filtrar información."""

    headers = {"X-Error-Code": code}
    if status in {429, 503}:
        headers["Retry-After"] = "1"
    return JSONResponse(
        status_code=status,
        content={"detail": {"code": code, "message": "Request rechazado por el perímetro local."}},
        headers=headers,
    )


def _body_too_large(request: Request, limit: int) -> bool:
    """Valida Content-Length antes de consumir el cuerpo."""

    value = request.headers.get("content-length")
    if value is None:
        return False
    try:
        return int(value) > limit
    except ValueError:
        return True


def _apply_security_headers(response: Any, request: Request, config: ServiceConfig) -> None:
    """Añade headers defensivos y CORS solo para orígenes permitidos."""

    for key, value in SECURITY_HEADERS.items():
        response.headers[key] = value
    origin = request.headers.get("Origin")
    if origin and origin in config.allowed_origins:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"


class EventRequest(BaseModel):
    """Evento in-play recibido por el endpoint live."""

    model_config = ConfigDict(extra="allow")
    event_id: str
    event_ts: str | None = None
    event_type: str
    team_id: int | None = None
    annulled: bool = False
    is_control: bool = False
    event_type_raw: str | None = None
    period: int | None = Field(default=None, ge=1, le=5)
    match_clock_seconds: float | None = Field(default=None, ge=0.0)
    observed_at: str | None = None
    event_time_quality: str | None = None


class ParlayLegRequest(BaseModel):
    """Pierna que el cliente quiere combinar.

    `extra="forbid"` es deliberado: si el cliente manda un campo que este
    esquema no conoce, es señal de que está usando un contrato distinto del
    que el gate valida, y aceptarlo en silencio combinaría piernas cuyo
    significado no está verificado.
    """

    model_config = ConfigDict(extra="forbid")
    key: str
    probability: float = Field(ge=0.0, le=1.0)
    fixture_key: str


class ParlayQuoteRequest(BaseModel):
    """Selección completa a combinar."""

    model_config = ConfigDict(extra="forbid")
    legs: list[ParlayLegRequest]


class PreMatchRequest(BaseModel):
    """Esquema HTTP compatible con `PreMatchInput`."""

    model_config = ConfigDict(extra="forbid")
    match_id: int
    home_team_id: int
    away_team_id: int
    kickoff_ts: str
    feature_cutoff_ts: str
    competition_id: str
    feature_version: str
    eligible_for_materialization: bool
    history_minimum_met: bool
    league_intercept: float
    home_advantage: float
    dc_attack_home: float
    dc_defense_home: float
    dc_attack_away: float
    dc_defense_away: float
    kalman_attack_home: float
    kalman_defense_home: float
    kalman_attack_away: float
    kalman_defense_away: float
    attack_sum: float = 0.0
    defense_sum: float = 0.0
    tau_dc: float = 0.0
    max_goals: int = Field(default=10, ge=1, le=30)
    source_hash: str = ""


class UpcomingRequest(BaseModel):
    """Solicitud compacta para un partido próximo por IDs ESPN."""

    model_config = ConfigDict(extra="forbid")
    league_slug: str = Field(min_length=1, max_length=64)
    home_team_id: int = Field(gt=0)
    away_team_id: int = Field(gt=0)
    kickoff_ts: str
    match_id: int | None = Field(default=None, gt=0)

    @field_validator("league_slug")
    @classmethod
    def valid_slug(cls, value: str) -> str:
        """Acepta sólo slugs alfanuméricos con puntos o guiones bajos."""

        if not re.fullmatch(r"[A-Za-z0-9._]+", value):
            raise ValueError("league_slug inválido.")
        return value


class FixtureRequest(BaseModel):
    """Criterios de usuario para localizar un fixture futuro en ESPN."""

    model_config = ConfigDict(extra="forbid")
    league_slug: str = Field(min_length=1, max_length=64)
    kickoff_date: str = Field(pattern=r"^\d{8}$")
    match_id: int | None = Field(default=None, gt=0)
    home_team_id: int | None = Field(default=None, gt=0)
    away_team_id: int | None = Field(default=None, gt=0)
    home_team_name: str | None = Field(default=None, min_length=1, max_length=120)
    away_team_name: str | None = Field(default=None, min_length=1, max_length=120)

    @field_validator("league_slug")
    @classmethod
    def fixture_slug(cls, value: str) -> str:
        """Valida el slug ESPN sin permitir rutas o parámetros."""

        if not re.fullmatch(r"[A-Za-z0-9._]+", value):
            raise ValueError("league_slug inválido.")
        return value


class LiveRequest(BaseModel):
    """Esquema HTTP compatible con `LiveSnapshotInput`."""

    model_config = ConfigDict(extra="forbid")
    match_id: int
    home_team_id: int
    away_team_id: int
    kickoff_ts: str
    snapshot_ts: str
    lambda_base_home: float
    lambda_base_away: float
    events: tuple[EventRequest, ...] = ()
    official_prediction: bool = False
    hawkes_enabled: bool = False
    hawkes_shadow_mode: bool = False
    source_hash: str = ""
    league_slug: str = Field(default="", max_length=64)
    provider_event_id: str = Field(default="", max_length=100)
    competition_id: str = Field(default="", max_length=100)
    period: int = Field(default=1, ge=1, le=5)
    match_clock_seconds: float | None = Field(default=None, ge=0.0)
    score_home: int | None = Field(default=None, ge=0)
    score_away: int | None = Field(default=None, ge=0)
    source_fetched_at: str = ""
    markov_live_enabled: bool = False
    markov_live_shadow_mode: bool = False
    hawkes_rho: float | None = Field(default=None, ge=0.0, le=1.0)
    hawkes_rho_goal: float | None = Field(default=None, ge=0.0, le=1.0)
    hawkes_rho_next_event: float | None = Field(default=None, ge=0.0, le=1.0)

    @field_validator("lambda_base_home", "lambda_base_away")
    @classmethod
    def positive_finite(cls, value: float) -> float:
        """Valida que las intensidades sean finitas y positivas."""

        if value <= 0 or not value < float("inf"):
            raise ValueError("La intensidad debe ser positiva y finita.")
        return value

    @field_validator("league_slug")
    @classmethod
    def live_slug(cls, value: str) -> str:
        """Valida el slug cuando el snapshot proviene del follower ESPN."""

        if value and not re.fullmatch(r"[A-Za-z0-9._]+", value):
            raise ValueError("league_slug inválido.")
        return value


class LiveFixtureRequest(BaseModel):
    """Selecciona un fixture activo sin aceptar eventos del cliente."""

    model_config = ConfigDict(extra="forbid")
    league_slug: str = Field(min_length=1, max_length=64)
    match_id: int = Field(gt=0)
    date: str | None = Field(default=None, pattern=r"^\d{8}$")

    @field_validator("league_slug")
    @classmethod
    def valid_slug(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9._]+", value):
            raise ValueError("league_slug inválido.")
        return value


def _pre_input(request: PreMatchRequest) -> PreMatchInput:
    """Convierte el modelo HTTP al contrato matemático."""

    return PreMatchInput(**request.model_dump())


def _live_input(request: LiveRequest) -> LiveSnapshotInput:
    """Convierte el modelo live y sus eventos al contrato matemático."""

    payload = request.model_dump()
    payload["events"] = tuple(payload["events"])
    return LiveSnapshotInput(**payload)


def _upcoming_input(request: UpcomingRequest) -> UpcomingMatchInput:
    """Convierte la solicitud compacta al puerto de inferencia universal."""

    return UpcomingMatchInput(**request.model_dump())


def _fixture_lookup(request: FixtureRequest) -> FixtureLookup:
    """Convierte el request HTTP al puerto del resolver ESPN."""

    if not any((request.match_id, request.home_team_id, request.away_team_id, request.home_team_name, request.away_team_name)):
        raise FixtureResolutionError("fixture_filter_required")
    return FixtureLookup(**request.model_dump())


async def _high_probability_prediction(
    app: FastAPI, engine: Any, config: ServiceConfig,
    fixture: dict[str, Any],
) -> dict[str, Any]:
    """Calcula la predicción de un fixture reutilizando la caché existente."""

    request = UpcomingRequest(
        league_slug=str(fixture["league_slug"]),
        home_team_id=int(fixture["home_team_id"]),
        away_team_id=int(fixture["away_team_id"]),
        kickoff_ts=str(fixture["kickoff_ts"]),
        match_id=int(fixture["match_id"]),
    )
    key = json.dumps(request.model_dump(), sort_keys=True)

    async def calculate() -> dict[str, Any]:
        """Ejecuta la inferencia universal sin bloques de presentación."""

        result = await _infer_with_timeout(
            engine.predict, _upcoming_input(request),
            config.inference_timeout_seconds)
        return asdict(result)

    return await app.state.upcoming_cache.get_or_compute(key, calculate)


async def _high_probability_picks(
    app: FastAPI, engine: Any, config: ServiceConfig,
    view: Any, fixtures: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int, int]:
    """Predice fixtures con concurrencia acotada y presupuesto de tiempo.

    Reemplaza un bucle secuencial sin límites que podía encadenar hasta
    `HIGH_PROBABILITY_FIXTURES` inferencias completas una tras otra con
    caché fría, monopolizando el pool de hilos compartido con el resto del
    servicio durante todo ese tramo.
    """

    semaphore = asyncio.Semaphore(HIGH_PROBABILITY_CONCURRENCY)
    deadline = time.monotonic() + HIGH_PROBABILITY_WALL_CLOCK_BUDGET_SECONDS
    picks: list[dict[str, Any]] = []
    scanned = 0
    skipped = 0

    async def _one(fixture: dict[str, Any]) -> None:
        """Predice un fixture si el presupuesto de tiempo aún lo permite."""

        nonlocal scanned, skipped
        async with semaphore:
            if time.monotonic() >= deadline:
                return
            scanned += 1
            try:
                payload = await _high_probability_prediction(
                    app, engine, config, fixture)
            except (PrematchUnavailableError, ValueError, OverflowError,
                    FloatingPointError, HTTPException) as error:
                LOGGER.info(
                    "Fixture sin predicción utilizable %s: %s",
                    fixture.get("match_id"), error)
                skipped += 1
                return
            picks.extend(
                {**pick, "fixture": _high_probability_fixture(fixture)}
                for pick in view.picks(payload))

    await asyncio.gather(*(_one(fixture) for fixture in fixtures))
    return picks, scanned, skipped


async def _parlay_legs(
    app: FastAPI, engine: Any, config: ServiceConfig,
    view: Any, fixtures: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int, int]:
    """Aplica el gate de parlays al catálogo del día.

    Mismo barrido acotado que `_high_probability_picks` -concurrencia y
    presupuesto de tiempo compartidos-, porque el costo dominante es idéntico:
    una inferencia completa por fixture. Lo único que cambia es qué se extrae
    de la predicción.
    """

    semaphore = asyncio.Semaphore(HIGH_PROBABILITY_CONCURRENCY)
    deadline = time.monotonic() + HIGH_PROBABILITY_WALL_CLOCK_BUDGET_SECONDS
    matches: list[dict[str, Any]] = []
    scanned = 0
    skipped = 0

    async def _one(fixture: dict[str, Any]) -> None:
        """Evalúa un fixture si el presupuesto de tiempo aún lo permite."""

        nonlocal scanned, skipped
        async with semaphore:
            if time.monotonic() >= deadline:
                return
            scanned += 1
            try:
                payload = await _high_probability_prediction(
                    app, engine, config, fixture)
            except (PrematchUnavailableError, ValueError, OverflowError,
                    FloatingPointError, HTTPException) as error:
                LOGGER.info(
                    "Fixture sin predicción utilizable %s: %s",
                    fixture.get("match_id"), error)
                skipped += 1
                return
            legs = view.legs(payload)
            if not legs:
                return
            identity = _high_probability_fixture(fixture)
            matches.append({
                **identity,
                # `fixture_key` es la misma clave que compone el publicador y
                # que usa `prediction_settlements`, de modo que una pierna
                # congelada aquí se pueda liquidar después sin traducciones.
                "fixture_key": (
                    f"{identity['league_slug']}:{identity['match_id']}"),
                "legs": legs,
            })

    await asyncio.gather(*(_one(fixture) for fixture in fixtures))
    matches.sort(key=lambda row: (str(row["kickoff_ts"]), int(row["match_id"])))
    return matches, scanned, skipped


def _high_probability_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    """Reduce el fixture a la identidad visible del menú."""

    return {
        "match_id": int(fixture["match_id"]),
        "league_slug": str(fixture["league_slug"]),
        "kickoff_ts": str(fixture["kickoff_ts"]),
        "home_team_id": int(fixture["home_team_id"]),
        "away_team_id": int(fixture["away_team_id"]),
        "home_team_name": str(fixture.get("home_team_name") or ""),
        "away_team_name": str(fixture.get("away_team_name") or ""),
        "home_team_logo": fixture.get("home_team_logo"),
        "away_team_logo": fixture.get("away_team_logo"),
    }


def _select_by_fixture(
    picks: list[dict[str, Any]], max_fixtures: int,
) -> tuple[list[dict[str, Any]], int]:
    """Agrupa los picks por partido y acota por partido, no por pick suelto.

    Antes `limit` ordenaba todos los picks del catálogo (de todos los
    partidos y todos los mercados) por tasa observada y cortaba los primeros
    N: un partido con varias líneas fuertes podía llenar el menú entero,
    dejando fuera los mercados de los demás partidos -el usuario veía "un
    solo mercado" en vez de la escalera completa por partido-. Ahora se
    eligen partidos (orden cronológico) y cada uno aporta todos sus picks.
    """

    grouped: dict[int, list[dict[str, Any]]] = {}
    for pick in picks:
        grouped.setdefault(int(pick["fixture"]["match_id"]), []).append(pick)
    ordered = sorted(
        grouped.items(), key=lambda item: str(item[1][0]["fixture"]["kickoff_ts"]))
    selected = ordered[:max_fixtures]
    flattened = [
        pick
        for _, fixture_picks in selected
        for pick in sorted(
            fixture_picks,
            key=lambda pick: (-pick["observed_rate"], pick["market"]))
    ]
    return flattened, len(grouped)


def _upcoming_catalog(
    payload: tuple[str, int, str | None],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Obtiene fixtures programados próximos desde scoreboards ESPN.

    Args:
        payload: Slugs, máximo de partidos y fecha opcional.

    Returns:
        Fixtures ordenados por kickoff UTC tras el reparto justo por liga
        (`allocate_fixtures_fairly`), y la lista de ligas con más fixtures
        de los que el cupo alcanzó a mostrar.
    """

    leagues, limit, selected_date = payload
    now = datetime.now(timezone.utc)
    slugs = [item.strip() for item in leagues.split(",") if item.strip()]
    dates = _upcoming_dates(now, selected_date)
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(slugs)))) as pool:
        batches = list(pool.map(
            lambda slug: _league_upcoming(slug, dates, now), slugs[:64]))
    rows = [row for batch in batches for row in batch]
    unique = {int(row["match_id"]): row for row in rows}
    return allocate_fixtures_fairly(list(unique.values()), limit)


# Tope duro que ya aplicaban `/v1/upcoming` y `/v1/live` a su parámetro
# `limit`. Cachear siempre la misma profundidad y recortar por petición evita
# que dos clientes que sólo difieren en cuántos partidos quieren -la Mini App
# pide 4 en el panel y 20 en la vista live- disparen dos barridos completos e
# independientes del mismo catálogo.
CATALOG_MAX_LIMIT = 20
# El barrido cuesta lo mismo pida 4 partidos o 30: recorre igualmente todas las
# ligas y fechas, y `limit` sólo recorta la lista ya ordenada al final. Cachear
# a la profundidad del consumidor más hambriento (`/v1/high-probability`) deja
# que los tres endpoints compartan una única entrada en vez de barrer ESPN una
# vez por cada profundidad distinta.
CATALOG_SWEEP_DEPTH = max(CATALOG_MAX_LIMIT, HIGH_PROBABILITY_FIXTURES)
# Cadencia del warmer. Con partidos en curso iguala al `refetchInterval` del
# cliente (20s); sin ninguno baja a 5 min, porque refrescar el catálogo cada
# 20s de madrugada, sin fixtures ni usuarios, sería gasto puro.
CATALOG_WARM_ACTIVE_SECONDS = 20.0
CATALOG_WARM_IDLE_SECONDS = 300.0


def _slice_catalog(payload: dict[str, Any], limit: int) -> dict[str, Any]:
    """Recorta un catálogo cacheado sin recalcularlo.

    Sólo `fixtures` y `count` dependen de `limit`; `league_count`,
    `date_count` y `partial_failure_count` describen el barrido completo y
    se conservan intactos. `truncated`/`leagues_with_hidden_fixtures` sí se
    recalculan: el barrido ya repartió el cupo con justicia
    (`allocate_fixtures_fairly`), pero un `limit` de cliente más chico que
    `CATALOG_SWEEP_DEPTH` puede volver a dejar fuera ligas que sí entraron
    al barrido -sin este recálculo, el aviso de truncamiento describiría el
    barrido completo, no lo que el cliente realmente recibió-.
    """

    fixtures = list(payload.get("fixtures", []))[:limit]
    shown_leagues = {str(row.get("league_slug")) for row in fixtures}
    all_leagues = {
        str(row.get("league_slug")) for row in payload.get("fixtures", [])}
    newly_hidden = all_leagues - shown_leagues
    hidden = sorted(set(payload.get("leagues_with_hidden_fixtures", [])) | newly_hidden)
    return {
        **payload, "fixtures": fixtures, "count": len(fixtures),
        "truncated": bool(hidden), "leagues_with_hidden_fixtures": hidden,
    }


def _daily_track_record_date(value: str) -> date:
    """Valida la fecha `YYYYMMDD` del resumen diario de aciertos."""

    if not value:
        raise _error("track_record_date_required")
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError as error:
        raise _error("track_record_date_invalid") from error


def _upcoming_dates(
    now: datetime, selected_date: str | None,
) -> tuple[str, ...]:
    """Valida fecha explícita o crea ventana futura de catorce días."""

    if selected_date is not None:
        try:
            datetime.strptime(selected_date, "%Y%m%d")
        except ValueError as error:
            raise ValueError("upcoming_date_invalid") from error
        return (selected_date,)
    return tuple(
        (now.date() + timedelta(days=offset)).strftime("%Y%m%d")
        for offset in range(14))


def _global_team_search(
    explorer: EspnFootballDataExplorer, query: str,
) -> list[dict[str, Any]]:
    """Busca equipos en todas las ligas con fallos parciales aislados."""

    if len(query.strip()) < 2:
        raise ValueError("team_search_requires_two_characters")
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(explorer.teams, slug, query) for slug, _ in LEAGUES]
        rows: list[dict[str, Any]] = []
        for future in futures:
            try:
                rows.extend(future.result())
            except (EspnConnectorError, OSError) as error:
                LOGGER.warning("Búsqueda global de equipos parcial: %s", error)
    unique = {
        (str(row.get("league_slug")), str(row.get("id"))): row
        for row in rows
    }
    return sorted(unique.values(), key=lambda row: str(row.get("name", "")))


def _league_upcoming(
    slug: str, dates: tuple[str, ...], now: datetime,
) -> list[dict[str, Any]]:
    """Consulta una liga y tolera fallos parciales auditables."""

    provider = EspnProspectiveConnector(EspnConnectorConfig(league=slug))
    rows: list[dict[str, Any]] = []
    try:
        date_range = dates[0] if len(dates) == 1 else f"{dates[0]}-{dates[-1]}"
        fixtures = scoreboard_fixtures(provider.scoreboard(date_range), slug)
        rows.extend(
            asdict(row) for row in fixtures
            if _future_fixture(row, now))
    except EspnConnectorError as error:
        LOGGER.warning("Catálogo próximo parcial %s: %s", slug, error)
    return rows


def _future_fixture(fixture: Any, now: datetime) -> bool:
    """Acepta sólo fixtures programados posteriores al instante actual."""

    kickoff = datetime.fromisoformat(fixture.kickoff_ts)
    return (
        kickoff > now
        and fixture.provider_status not in {"post", "final", "completed"}
    )


def _error(detail: str) -> HTTPException:
    """Construye una respuesta HTTP uniforme para errores de contrato."""

    code = _error_code(detail)
    return HTTPException(status_code=422, detail={"code": code, "message": detail}, headers={"X-Error-Code": code})


def _settlement_store() -> Any | None:
    """Conecta el historial verificado sólo si hay base de datos configurada.

    La API operó sin base de datos hasta Fase 118; su ausencia degrada el
    endpoint a `unavailable` en vez de impedir el arranque del servicio.
    """

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        return None
    try:
        return build_settlement_repository(database_url)
    except Exception as exc:  # noqa: BLE001 - la API no debe caer por el historial
        LOGGER.warning(json.dumps({
            "event": "settlement_store_unavailable",
            "error_type": type(exc).__name__,
        }, sort_keys=True))
        return None


def _high_probability_unavailable() -> dict[str, Any]:
    """Bloque degradado del menú de mayor probabilidad dentro de Aciertos."""

    return {
        "status": "unavailable",
        "picks": [],
        "summary": {"hits": 0, "settled": 0, "pending": 0, "total": 0},
    }


def _high_probability_recent_block(
    hp_store: Any, window: int,
    fixture_names: dict[str, tuple[str | None, str | None]],
) -> dict[str, Any]:
    """Arma el bloque `high_probability` de la ventana por conteo.

    Sólo picks ya liquidados -mismo principio que `store.recent`, que
    tampoco expone predicciones todavía pendientes de kickoff+3h-.
    """

    settled_records = hp_store.settled_recent(window)
    frozen_by_key = hp_store.frozen_for([row.pick_key for row in settled_records])
    frozen = [
        frozen_by_key[row.pick_key] for row in settled_records
        if row.pick_key in frozen_by_key
    ]
    settled_by_key = {row.pick_key: row for row in settled_records}
    block = pick_view(frozen, settled_by_key, fixture_names)
    block["status"] = "available"
    return block


def _high_probability_reliability_unavailable() -> dict[str, Any]:
    """Bloque degradado del diagrama de fiabilidad prospectivo."""

    return {
        "status": "unavailable", "cells": [],
        "total_frozen": 0, "total_settled": 0,
    }


def _high_probability_reliability_block(hp_store: Any, window: int) -> dict[str, Any]:
    """Compara confianza declarada contra tasa observada, por tramo (Fase 123).

    Mismo par frozen/settled que ya arma `_high_probability_recent_block` para
    `pick_view` -reutilizado tal cual, sin una segunda consulta a la base-,
    pasado a `prospective_reliability` en vez de a `pick_view`.
    """

    settled_records = hp_store.settled_recent(window)
    frozen_by_key = hp_store.frozen_for([row.pick_key for row in settled_records])
    frozen = [
        frozen_by_key[row.pick_key] for row in settled_records
        if row.pick_key in frozen_by_key
    ]
    block = prospective_reliability(frozen, settled_records)
    block["status"] = "available"
    return block


def _high_probability_daily_block(
    hp_store: Any, target: date,
    fixture_names: dict[str, tuple[str | None, str | None]],
) -> dict[str, Any]:
    """Arma el bloque `high_probability` de la ventana por fecha local.

    Incluye picks todavía sin liquidar (`status: "pending"`): DEC-158/161
    exigen una ventana íntegra, no sólo lo ya resuelto.
    """

    frozen = hp_store.frozen_on_date(target, MEXICO_TZ)
    settled_by_key = hp_store.settlements_for([row.pick_key for row in frozen])
    block = pick_view(frozen, settled_by_key, fixture_names)
    block["status"] = "available"
    return block


def _high_probability_pick_store() -> Any | None:
    """Conecta el repositorio de picks de Fase 123 sólo si hay base de datos.

    Mismo patrón que `_settlement_store`: su ausencia degrada el bloque
    `high_probability` de la ventana de Aciertos a `unavailable` en vez de
    impedir el arranque del servicio.
    """

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        return None
    try:
        return build_high_probability_pick_repository(database_url)
    except Exception as exc:  # noqa: BLE001 - la API no debe caer por esto
        LOGGER.warning(json.dumps({
            "event": "high_probability_pick_store_unavailable",
            "error_type": type(exc).__name__,
        }, sort_keys=True))
        return None


def _catalog_cache_store() -> Any | None:
    """Conecta el nivel persistente de la caché si hay base de datos.

    Su ausencia no degrada ninguna respuesta: la caché en memoria es
    autosuficiente y sólo se pierde la capacidad de sobrevivir a un
    despliegue, así que un fallo aquí nunca debe impedir el arranque.
    """

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        return None
    try:
        return build_catalog_cache_store(database_url)
    except Exception as exc:  # noqa: BLE001 - la API no debe caer por la caché
        LOGGER.warning(json.dumps({
            "event": "catalog_cache_store_unavailable",
            "error_type": type(exc).__name__,
        }, sort_keys=True))
        return None


def create_app(
    config: ServiceConfig | None = None,
    fixture_resolver: Any | None = None,
    live_runtime: Any | None = None,
    provider_context: Any | None = None,
    settlement_store: Any | None = None,
    high_probability_pick_store: Any | None = None,
) -> FastAPI:
    """Crea la aplicación local con dependencias inyectadas.

    Args:
        config: Configuración inmutable del servicio.

    Returns:
        Aplicación FastAPI sin recursos externos.
    """

    effective = config or service_config_from_env()
    engine = DikamahaInferenceEngine()
    try:
        snapshot_path = resolve_active_snapshot(
            snapshot_id=effective.prematch_snapshot_id,
            root=Path(effective.prematch_snapshot_root) if effective.prematch_snapshot_root else None,
        )
    except SnapshotRegistryError as error:
        raise PrematchUnavailableError("active_snapshot_unavailable") from error
    upcoming_engine = UniversalPrematchEngine(snapshot_path)
    live_engine = live_runtime or LivePredictionRuntime(
        upcoming_engine,
        engine,
        probability_engine_enabled=effective.live_probability_engine_enabled,
        probability_engine_official=effective.live_probability_engine_official,
        monte_carlo_enabled=effective.live_monte_carlo_diagnostic,
        monte_carlo_simulations=effective.live_monte_carlo_simulations,
        fallback_enabled=effective.live_engine_fallback_enabled,
        refresh_seconds=effective.live_engine_refresh_seconds,
    )
    shadow_catalog = load_shadow_catalog()

    @asynccontextmanager
    async def lifespan(scope: FastAPI):
        """Arranca el warmer de catálogos y libera lo que esta app posee."""

        warmers = [
            asyncio.create_task(loop())
            for loop in getattr(scope.state, "catalog_warmers", ())
        ]
        try:
            yield
        finally:
            for task in warmers:
                task.cancel()
            if warmers:
                await asyncio.gather(*warmers, return_exceptions=True)
            if live_runtime is None and hasattr(live_engine, "close"):
                live_engine.close()

    app = FastAPI(
        title="DIKAMAHA Local Inference Service",
        version=SERVICE_VERSION,
        lifespan=lifespan,
    )
    app.state.service_config = effective
    app.state.inference_engine = engine
    app.state.upcoming_engine = upcoming_engine
    app.state.fixture_resolver = fixture_resolver
    app.state.data_explorer = EspnFootballDataExplorer()
    app.state.fixture_context = default_context_service()
    app.state.provider_context = provider_context or ProviderMatchContextService()
    app.state.live_runtime = live_engine
    app.state.shadow_catalog = shadow_catalog
    app.state.settlement_store = (
        settlement_store if settlement_store is not None else _settlement_store())
    app.state.high_probability_pick_store = (
        high_probability_pick_store if high_probability_pick_store is not None
        else _high_probability_pick_store())
    app.state.metrics = MetricsStore()
    app.state.upcoming_cache = AsyncPredictionCache()
    # TTL corto y por debajo del refresco de cliente documentado en Fase 115
    # (Mini App: 60s para próximos, 20s para en vivo), así que cachear no
    # envejece el dato más de lo que el usuario ya tolera. Existe porque un
    # pico real de CPU al 100% de los 8 vCPUs del contenedor coincidió con
    # varios clientes (Mini App, bot, worker) recalculando el mismo barrido de
    # 63 ligas en ESPN al mismo tiempo tras un despliegue, y esa contención
    # hizo que predicciones sin relación agotaran su timeout de 30s.
    #
    # El segundo umbral (`stale_ttl_seconds`) es lo que saca el barrido del
    # camino crítico: pasado el TTL la respuesta se sigue sirviendo al
    # instante mientras se recalcula por detrás. Sin él, con el tráfico de un
    # grupo privado casi ninguna apertura de la Mini App caía dentro de la
    # ventana de 25-45s y cada usuario pagaba los ~30s completos. La lista de
    # próximos partidos apenas cambia, así que tolera una ventana mucho más
    # larga (15 min) que el catálogo live (3 min).
    #
    # El nivel persistente es lo que hace que un despliegue deje de dejar la
    # caché en frío: sin él, el contenedor arranca sin nada y el primer usuario
    # posterior a cada publicación vuelve a pagar el barrido completo.
    catalog_store = _catalog_cache_store()
    app.state.catalog_cache_store = catalog_store
    app.state.upcoming_catalog_cache = AsyncPredictionCache(
        ttl_seconds=60.0, stale_ttl_seconds=900.0, stamp_age=True,
        store=catalog_store, namespace="upcoming")
    # 25s, no 15s: la Mini App refresca cada 20s (`refetchInterval` en
    # `live/page.tsx`). Con TTL=15s casi cada refresco periódico expiraba la
    # caché primero y pagaba de nuevo el barrido completo (~30s medidos en
    # frío contra las 63 ligas x 3 días) en vez de servir la respuesta ya
    # calculada. Ver DEC-181.
    app.state.live_catalog_cache = AsyncPredictionCache(
        ttl_seconds=25.0, stale_ttl_seconds=180.0, stamp_age=True,
        store=catalog_store, namespace="live")
    app.state.high_probability_view = HighProbabilityView()
    app.state.parlay_view = ParlayEligibilityView()
    app.state.request_gate = RequestGate(
        effective.max_concurrent_requests,
        effective.rate_limit_requests,
        effective.rate_limit_window_seconds,
    )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        """Normaliza errores Pydantic sin devolver payloads en logs."""

        return JSONResponse(status_code=422, content={"detail": {"code": "contract_validation_error", "message": "Request inválido."}}, headers={"X-Error-Code": "contract_validation_error"})

    @app.middleware("http")
    async def perimeter_middleware(request: Request, call_next: Any) -> Any:
        """Aplica perímetro, timeout, métricas y logging sin leer payloads."""

        request_id = _request_id(request)
        started = time.perf_counter()
        reserved = False
        response = _preflight_response(request, effective)
        response = response or _perimeter_rejection(request, effective)
        if response is None and request.url.path not in PUBLIC_PATHS:
            code = app.state.request_gate.enter(_client_id(request), time.monotonic())
            response = _gate_error(code, 429 if code == "rate_limit_exceeded" else 503) if code else None
            reserved = code is None
        try:
            if response is None:
                response = await _call_with_timeout(request, call_next, effective)
        finally:
            if reserved:
                app.state.request_gate.leave()
        duration_ms = (time.perf_counter() - started) * 1000.0
        error_code = response.headers.get("X-Error-Code")
        app.state.metrics.observe(request.url.path, response.status_code, duration_ms, error_code)
        response.headers["X-Request-ID"] = request_id
        _apply_security_headers(response, request, effective)
        LOGGER.info(_structured_log(request, request_id, response.status_code, duration_ms, effective, error_code))
        return response

    @app.get("/v1/health", tags=["health"])
    def health() -> dict[str, Any]:
        """Devuelve estado y flags activos del servicio."""

        return {"status": "ok", "service_version": SERVICE_VERSION, **_public_config(effective)}

    @app.get("/v1/readiness", tags=["health"])
    def readiness() -> dict[str, Any]:
        """Indica si la configuración local permite recibir requests."""

        valid = _configuration_ready(effective)
        return {"status": "ready" if valid else "not_ready", "ready": valid, "contract_version": effective.contract_version, "hawkes_enabled": effective.hawkes_enabled, "hawkes_shadow_mode": effective.hawkes_shadow_mode, "shadow_catalog_ready": True, "live_models_ready": True, "reason": None if valid else "invalid_service_configuration"}

    async def _cached_upcoming_catalog(
        selected: str, selected_date: str | None,
    ) -> dict[str, Any]:
        """Sirve el catálogo de próximos compartido por todos sus consumidores.

        La clave depende sólo de ligas y fecha, nunca de `limit`: el barrido
        recorre las mismas ligas y fechas pida cuatro partidos o treinta, así
        que `/v1/upcoming` y `/v1/high-probability` comparten una sola entrada
        y cada uno recorta lo que necesita.
        """

        async def calculate() -> dict[str, Any]:
            """Ejecuta el barrido ESPN real sólo cuando la caché expira."""

            fixtures, hidden_leagues = await _infer_with_timeout(
                _upcoming_catalog, (selected, CATALOG_SWEEP_DEPTH, selected_date),
                effective.inference_timeout_seconds * 5
            )
            return {
                "fixtures": fixtures, "count": len(fixtures), "status": "ok",
                "league_count": len([slug for slug in selected.split(",") if slug.strip()]),
                "date_count": len(
                    _upcoming_dates(datetime.now(timezone.utc), selected_date)),
                "truncated": bool(hidden_leagues),
                "leagues_with_hidden_fixtures": hidden_leagues,
            }

        key = json.dumps(
            {"leagues": selected, "date": selected_date}, sort_keys=True)
        return await app.state.upcoming_catalog_cache.get_or_compute(
            key, calculate)

    @app.get("/v1/upcoming", tags=["inference"])
    async def upcoming_catalog(
        limit: int = 8, leagues: str | None = None,
        date: str | None = None,
    ) -> dict[str, Any]:
        """Lista partidos futuros para navegación sin IDs manuales."""

        if not effective.external_calls_enabled:
            raise _error("external_calls_disabled")
        selected = leagues or ",".join(slug for slug, _ in LEAGUES)
        bounded = min(max(int(limit), 1), CATALOG_MAX_LIMIT)
        try:
            return _slice_catalog(
                await _cached_upcoming_catalog(selected, date), bounded)
        except (ValueError, TimeoutError, OSError) as exc:
            LOGGER.warning("Rechazo catálogo upcoming: %s", exc)
            raise _error("upcoming_catalog_unavailable") from exc

    @app.get("/v1/high-probability", tags=["inference"])
    async def high_probability(
        date: str | None = None, limit: int = 10,
        leagues: str | None = None,
    ) -> dict[str, Any]:
        """Publica los picks del día cuya fiabilidad histórica está probada.

        Un pick de gol sólo aparece si el par (mercado, tramo de confianza)
        al que pertenece superó el gate de Fase 122; uno de equipo, si su
        mercado está cubierto por la escalera auditada -ver
        `src/ladder_pick_selection.py`-. La cifra publicada es la tasa
        observada, no la probabilidad del modelo.

        `limit` acota **partidos**, no picks sueltos: cada partido incluido
        aporta todos sus mercados de equipo disponibles (hasta ~18: córners,
        córners 1ª mitad, tiros, tiros a puerta, tarjetas y tarjetas 1ª
        mitad, por lado), no sólo el más fuerte. Antes de esto el límite
        acotaba picks individuales ordenados globalmente por tasa observada,
        de modo que un solo partido con varias líneas fuertes podía
        desplazar del todo a los demás -el usuario veía "un solo mercado" en
        vez de la escalera completa por partido-.
        """

        if not effective.external_calls_enabled:
            raise _error("external_calls_disabled")
        view = app.state.high_probability_view
        provenance = view.provenance()
        if not provenance["goal_markets_gate_available"] and not provenance[
                "team_markets_sha256"]:
            # Las dos fuentes están caídas -no sólo el gate de gol, que por sí
            # solo ya no vacía el menú: los mercados de equipo de la escalera
            # auditada siguen siendo independientes-. Evita el barrido real.
            return {
                "status": "unavailable",
                "reason": "high_probability_sources_unavailable",
                "picks": [], "count": 0, "fixtures_scanned": 0,
                "provenance": provenance,
            }
        selected = leagues or ",".join(slug for slug, _ in LEAGUES)
        bounded = min(max(int(limit), 1), 50)
        try:
            # Misma entrada de caché que `/v1/upcoming`: antes este endpoint
            # tenía su propia clave y barría ESPN por separado, aunque el
            # barrido es idéntico y sólo cambiaba cuántos fixtures conservaba
            # al final. `CATALOG_SWEEP_DEPTH` ya cubre `HIGH_PROBABILITY_FIXTURES`.
            cached = await _cached_upcoming_catalog(selected, date)
        except (ValueError, TimeoutError, OSError) as exc:
            LOGGER.warning("Rechazo catálogo mayor probabilidad: %s", exc)
            raise _error("upcoming_catalog_unavailable") from exc
        fixtures = cached["fixtures"]

        picks, scanned, skipped = await _high_probability_picks(
            app, upcoming_engine, effective, view, fixtures)
        selected_picks, fixtures_with_picks = _select_by_fixture(picks, bounded)
        return {
            "status": "ok",
            "classification": "experimental_shadow_not_promoted",
            "picks": selected_picks,
            "count": len(selected_picks),
            "fixtures_with_picks": fixtures_with_picks,
            "total_candidates": len(picks),
            "fixtures_scanned": scanned,
            "fixtures_catalog_size": len(fixtures),
            "fixtures_without_prediction": skipped,
            "provenance": provenance,
        }

    @app.get("/v1/parlay/menu", tags=["inference"])
    async def parlay_menu(
        date: str | None = None, limit: int = 20,
        leagues: str | None = None,
    ) -> dict[str, Any]:
        """Publica las piernas del día que superan el gate de Fase 135.

        Un mercado sólo aparece si pasó los tres filtros -ventaja sobre su
        propia referencia, calibración cierta en el tramo donde se usa, y
        estabilidad entre ligas- y además su probabilidad alcanza el umbral
        congelado de ese mercado. La regla no es "los más altos": «Ambos
        marcan» declara 0.88 y entrega 0.51, así que ordenar por probabilidad
        seleccionaría justamente los peores mercados (DEC-222).

        `limit` acota **partidos**, no piernas: un partido incluido aporta
        todas sus piernas elegibles.
        """

        if not effective.external_calls_enabled:
            raise _error("external_calls_disabled")
        view = app.state.parlay_view
        if not view.available():
            return {
                "status": "unavailable",
                "reason": "parlay_criteria_unavailable",
                "classification": PARLAY_STATUS,
                "matches": [], "legs": 0, "fixtures_scanned": 0,
            }
        config = view._load()  # noqa: SLF001 - gate ya validado por `available`
        selected = leagues or ",".join(slug for slug, _ in LEAGUES)
        bounded = min(max(int(limit), 1), 50)
        try:
            cached = await _cached_upcoming_catalog(selected, date)
        except (ValueError, TimeoutError, OSError) as exc:
            LOGGER.warning("Rechazo catálogo de parlays: %s", exc)
            raise _error("upcoming_catalog_unavailable") from exc
        fixtures = cached["fixtures"]
        matches, scanned, skipped = await _parlay_legs(
            app, upcoming_engine, effective, view, fixtures)
        shown = matches[:bounded]
        return {
            "status": "ok",
            "classification": PARLAY_STATUS,
            "matches": shown,
            "legs": sum(len(row["legs"]) for row in shown),
            "matches_with_legs": len(matches),
            "min_legs": config["min_legs"],
            "max_legs": config["max_legs"],
            "max_legs_per_match": config["max_legs_per_match"],
            "criteria_version": config["version"],
            "criteria_sha256": config["sha256"],
            "delivery": {
                legs: value["ratio"]
                for legs, value in config["delivery"].items()},
            "fixtures_scanned": scanned,
            "fixtures_catalog_size": len(fixtures),
            "fixtures_without_prediction": skipped,
            "disclosure": PARLAY_DISCLOSURE,
        }

    @app.post("/v1/parlay/quote", tags=["inference"])
    def parlay_quote(request: ParlayQuoteRequest) -> dict[str, Any]:
        """Combina piernas ya elegibles en una probabilidad conjunta.

        Revalida el gate sobre cada pierna en vez de confiar en lo que llega:
        un cliente puede mandar cualquier cosa, y multiplicar un mercado
        excluido devolvería un número con la apariencia de estar respaldado.
        Publica el ratio de entrega junto a la probabilidad porque la conjunta
        declarada, sola, ya se sabe optimista.
        """

        view = app.state.parlay_view
        if not view.available():
            raise _error("parlay_criteria_unavailable")
        try:
            return view.build([leg.model_dump() for leg in request.legs])
        except ParlayEligibilityError as error:
            raise _error(str(error)) from error

    def _live_catalog_selection(
        limit: int, leagues: str | None,
    ) -> tuple[str, int]:
        """Resuelve las ligas y el tope compartidos por catálogo y progreso."""

        selected = leagues or os.getenv("DIKAMAHA_LIVE_LEAGUES")
        if not selected:
            selected = ",".join(
                str(row["slug"]) for row in app.state.data_explorer.leagues())
        return selected, min(max(int(limit), 1), CATALOG_MAX_LIMIT)

    def _live_catalog_key(selected: str, date: str | None) -> str:
        """Clave compartida por la caché del catálogo y el progreso del barrido.

        Deliberadamente sin `limit`: el barrido recorre las mismas ligas y
        fechas pida cuatro fixtures o veinte. Con `limit` dentro, el panel
        (`limit=4`) y la vista live (`limit=20`) eran dos claves distintas y
        abrir la Mini App y tocar "Ver partidos en vivo" pagaba dos barridos
        completos del mismo catálogo. Como efecto secundario deseable,
        `/v1/live/progress` pasa a reportar el avance del barrido compartido.
        """

        return json.dumps({"leagues": selected, "date": date}, sort_keys=True)

    @app.get("/v1/live", tags=["inference"])
    async def live_catalog(
        limit: int = 12, leagues: str | None = None,
        date: str | None = None,
    ) -> dict[str, Any]:
        """Descubre fixtures ESPN activos para navegación Telegram."""

        if not effective.external_calls_enabled:
            raise _error("external_calls_disabled")
        selected, bounded = _live_catalog_selection(limit, leagues)
        key = _live_catalog_key(selected, date)

        async def calculate() -> dict[str, Any]:
            """Ejecuta el descubrimiento live real sólo cuando la caché expira."""

            return await _infer_with_timeout(
                lambda values: app.state.live_runtime.list_active(*values),
                (selected, CATALOG_MAX_LIMIT, date, key),
                effective.inference_timeout_seconds * 6,
            )

        try:
            return _slice_catalog(
                await app.state.live_catalog_cache.get_or_compute(key, calculate),
                bounded,
            )
        except (EspnConnectorError, PrematchUnavailableError, ValueError, OSError) as exc:
            LOGGER.warning("Rechazo catálogo live: %s", exc)
            raise _error("live_catalog_unavailable") from exc

    @app.get("/v1/live/progress", tags=["inference"])
    def live_scan_progress(
        limit: int = 12, leagues: str | None = None, date: str | None = None,
    ) -> dict[str, Any]:
        """Avance en tiempo real del barrido live que gobierna estos filtros.

        Lectura en memoria, sin llamadas externas: no necesita
        `external_calls_enabled` ni participa del presupuesto de tiempo del
        barrido real. `status` es `"idle"` si nunca se inició un barrido con
        esta combinación exacta de filtros (por ejemplo, la caché del
        catálogo todavía está vigente y no hizo falta escanear), `"scanning"`
        mientras corre, y `"done"` con la cifra final una vez termina.
        """

        selected, _ = _live_catalog_selection(limit, leagues)
        key = _live_catalog_key(selected, date)
        return app.state.live_runtime.scan_progress.snapshot(key)

    @app.get("/v1/models", tags=["inference"])
    def models() -> dict[str, Any]:
        """Lista sólo modelos realmente ejecutados y su clasificación."""

        return model_inventory(app.state.live_runtime.policy)

    @app.get("/v1/track-record", tags=["inference"])
    def track_record_view(window: int = DEFAULT_WINDOW) -> dict[str, Any]:
        """Publica el desempeño verificado sobre la cola cronológica completa.

        DEC-101 prohíbe seleccionar partidos por desempeño, de modo que no hay
        ningún parámetro que permita pedir sólo aciertos: la ventana es
        estrictamente cronológica e incluye los fallos.
        """

        store = getattr(app.state, "settlement_store", None)
        if store is None:
            return {
                "status": "unavailable",
                "reason": "settlement_store_not_configured",
                "window": {"requested": int(window), "available": 0},
                "official": {}, "shadow": {"markets": {}}, "matches": [],
                "high_probability": _high_probability_unavailable(),
                "high_probability_reliability": _high_probability_reliability_unavailable(),
            }
        requested = max(1, min(int(window), MAXIMUM_WINDOW))
        records = store.recent(requested)
        payload = track_record(records)
        payload["status"] = "available"
        payload["window"]["requested"] = requested
        hp_store = getattr(app.state, "high_probability_pick_store", None)
        if hp_store is None:
            payload["high_probability"] = _high_probability_unavailable()
            payload["high_probability_reliability"] = _high_probability_reliability_unavailable()
        else:
            fixture_names = {
                row.fixture_key: (row.home_team_name, row.away_team_name)
                for row in records
            }
            payload["high_probability"] = _high_probability_recent_block(
                hp_store, requested, fixture_names)
            payload["high_probability_reliability"] = _high_probability_reliability_block(
                hp_store, requested)
        return payload

    @app.get("/v1/track-record/daily", tags=["inference"])
    def track_record_daily_view(date: str) -> dict[str, Any]:
        """Publica el desempeño verificado de un día calendario completo.

        Mismo principio que `/v1/track-record`: no hay parámetro para pedir
        sólo aciertos. Agrupa por fecha local (Ciudad de México) del kickoff
        en vez de por ventana de conteo. `date` es obligatorio en formato
        `YYYYMMDD` para no depender del reloj del servidor.
        """

        target = _daily_track_record_date(date)
        store = getattr(app.state, "settlement_store", None)
        if store is None:
            return {
                "status": "unavailable",
                "reason": "settlement_store_not_configured",
                "date": target.isoformat(),
                "official": {}, "shadow": {"markets": {}}, "matches": [],
                "high_probability": _high_probability_unavailable(),
            }
        records = store.on_date(target, MEXICO_TZ)
        payload = track_record(records)
        payload["status"] = "available"
        payload["date"] = target.isoformat()
        hp_store = getattr(app.state, "high_probability_pick_store", None)
        if hp_store is None:
            payload["high_probability"] = _high_probability_unavailable()
        else:
            fixture_names = {
                row.fixture_key: (row.home_team_name, row.away_team_name)
                for row in records
            }
            payload["high_probability"] = _high_probability_daily_block(
                hp_store, target, fixture_names)
        return payload

    @app.get("/v1/media/image", tags=["explorer"])
    async def provider_media(url: str) -> Response:
        """Entrega sólo PNG transparente permitido, sin exponer credenciales."""

        try:
            payload = await _infer_with_timeout(
                fetch_transparent_png, url, effective.inference_timeout_seconds)
        except (ProviderMediaError, requests.RequestException, TimeoutError) as exc:
            LOGGER.warning("Medio visual rechazado: %s", type(exc).__name__)
            raise _error("provider_media_unavailable") from exc
        return Response(payload, media_type="image/png", headers={
            "Content-Disposition": "inline",
            "Cache-Control": "public, max-age=86400, immutable",
        })

    @app.get("/v1/explorer/leagues", tags=["explorer"])
    async def explorer_leagues() -> dict[str, Any]:
        """Lista ligas disponibles para menús de usuario."""

        rows = app.state.data_explorer.leagues()
        return {"leagues": rows, "count": len(rows)}

    @app.get("/v1/explorer/dates", tags=["explorer"])
    async def explorer_date_catalog(
        mode: str = "past", days: int = 8,
    ) -> dict[str, Any]:
        """Genera un calendario compacto pasado o futuro."""

        if mode not in {"past", "future"}:
            raise _error("invalid_explorer_date_mode")
        rows = explorer_dates(mode, min(max(days, 1), 14))
        return {"dates": rows, "count": len(rows), "mode": mode}

    @app.get("/v1/explorer/fixtures", tags=["explorer"])
    async def explorer_fixtures(
        league: str, date: str,
    ) -> dict[str, Any]:
        """Lista partidos ESPN de la liga y fecha seleccionadas."""

        rows = await _explorer_call(
            app.state.data_explorer.fixtures, (league, date), effective)
        return {"fixtures": rows, "count": len(rows)}

    @app.get("/v1/explorer/fixture/context", tags=["explorer"])
    async def explorer_fixture_context(league: str, event_id: str) -> dict[str, Any]:
        """Devuelve sólo contexto visual desde snapshots raw-first ya capturados."""

        return await _infer_with_timeout(
            lambda values: app.state.fixture_context.context(*values), (league, event_id),
            effective.inference_timeout_seconds)

    @app.get("/v1/provider/predictor", tags=["inference"])
    async def provider_predictor(
        league: str, event_id: str, scope: str = "pre_match",
    ) -> dict[str, Any]:
        """Expone el predictor externo como benchmark de presentación aislado."""

        if not effective.external_calls_enabled:
            raise _error("external_calls_disabled")
        try:
            return await _infer_with_timeout(
                lambda values: app.state.provider_context.fetch(*values),
                (league, event_id, scope),
                effective.inference_timeout_seconds * 2,
            )
        except (EspnConnectorError, ValueError, OSError) as exc:
            LOGGER.warning("Predictor externo no disponible: %s", type(exc).__name__)
            raise _error(str(exc)) from exc

    @app.get("/v1/provider/markets", tags=["explorer"])
    async def provider_markets(league: str, date: str) -> dict[str, Any]:
        """Expone apertura/cierre/live como presentación financiera aislada."""

        if not effective.external_calls_enabled:
            raise _error("external_calls_disabled")
        try:
            return await _infer_with_timeout(
                lambda values: app.state.provider_context.markets(*values),
                (league, date), effective.inference_timeout_seconds * 2,
            )
        except (EspnConnectorError, ValueError, OSError) as exc:
            LOGGER.warning("Cinta de mercado no disponible: %s", type(exc).__name__)
            raise _error(str(exc)) from exc

    @app.get("/v1/explorer/match/plays", tags=["explorer"])
    async def explorer_match_plays(
        league: str, match_id: str, competition_id: str,
        scope: str = "key",
    ) -> dict[str, Any]:
        """Devuelve play-by-play paginado y normalizado."""

        if scope not in {"key", "all"}:
            raise _error("invalid_play_scope")
        return await _explorer_call(
            app.state.data_explorer.plays,
            (league, match_id, competition_id, scope), effective)

    @app.get("/v1/explorer/match/statistics", tags=["explorer"])
    async def explorer_match_statistics(
        league: str, match_id: str, competition_id: str,
    ) -> dict[str, Any]:
        """Devuelve eventos 1T/2T/total y boxscore final."""

        return await _explorer_call(
            app.state.data_explorer.statistics,
            (league, match_id, competition_id), effective)

    @app.get("/v1/explorer/teams", tags=["explorer"])
    async def explorer_teams(
        league: str | None = None, query: str = "",
    ) -> dict[str, Any]:
        """Lista o busca equipos por texto."""

        if league:
            rows = await _explorer_call(
                app.state.data_explorer.teams, (league, query), effective)
        else:
            try:
                rows = await _infer_with_timeout(
                    lambda values: _global_team_search(*values),
                    (app.state.data_explorer, query),
                    effective.inference_timeout_seconds * 4,
                )
            except ValueError as exc:
                raise _error(str(exc)) from exc
        return {"teams": rows[:50], "count": len(rows)}

    @app.get("/v1/explorer/team/roster", tags=["explorer"])
    async def explorer_team_roster(
        league: str, team_id: str,
    ) -> dict[str, Any]:
        """Devuelve plantilla actual y estadísticas disponibles."""

        return await _explorer_call(
            app.state.data_explorer.roster, (league, team_id), effective)

    @app.get("/v1/explorer/player", tags=["explorer"])
    async def explorer_player(
        league: str, team_id: str, player_id: str,
    ) -> dict[str, Any]:
        """Devuelve perfil y acumulados del jugador."""

        return await _explorer_call(
            app.state.data_explorer.player,
            (league, team_id, player_id), effective)

    @app.get("/v1/metrics", tags=["observability"])
    def metrics() -> dict[str, Any]:
        """Devuelve métricas agregadas del proceso sin datos de requests."""

        return {"service_version": SERVICE_VERSION, "contract_version": effective.contract_version, **app.state.metrics.snapshot()}

    @app.post("/v1/predict/pre-match", tags=["inference"])
    async def predict_pre_match(request: PreMatchRequest) -> dict[str, Any]:
        """Ejecuta inferencia pre-match del contrato vigente."""

        try:
            result = await _infer_with_timeout(
                engine.predict_pre_match, _pre_input(request), effective.inference_timeout_seconds
            )
        except (ValueError, OverflowError, FloatingPointError) as exc:
            LOGGER.warning("Rechazo pre-match: %s", exc)
            raise _error(str(exc)) from exc
        payload = asdict(result)
        payload["shadow_catalog"] = build_shadow_observation(app.state.shadow_catalog)
        return payload

    @app.post("/v1/predict/upcoming", tags=["inference"])
    async def predict_upcoming(request: UpcomingRequest) -> dict[str, Any]:
        """Predice un partido próximo desde el snapshot histórico local."""

        try:
            key = json.dumps(request.model_dump(), sort_keys=True)

            async def calculate() -> dict[str, Any]:
                """Calcula una respuesta sin bloques de presentación."""

                result = await _infer_with_timeout(
                    upcoming_engine.predict, _upcoming_input(request),
                    effective.inference_timeout_seconds)
                return asdict(result)

            payload = await app.state.upcoming_cache.get_or_compute(
                key, calculate)
        except (PrematchUnavailableError, ValueError, OverflowError, FloatingPointError) as exc:
            LOGGER.warning("Rechazo upcoming: %s", exc)
            raise _error(str(exc)) from exc
        payload["shadow_catalog"] = build_shadow_observation(app.state.shadow_catalog)
        return payload

    @app.post("/v1/predict/fixture", tags=["inference"])
    async def predict_fixture(request: FixtureRequest) -> dict[str, Any]:
        """Resuelve un fixture ESPN y ejecuta la predicción compacta."""

        if not effective.external_calls_enabled:
            raise _error("external_calls_disabled")
        try:
            resolver = app.state.fixture_resolver or connector_for_league(request.league_slug)
            fixture = await _infer_with_timeout(resolver.resolve, _fixture_lookup(request), effective.inference_timeout_seconds)
            input_data = UpcomingMatchInput(fixture.league_slug, fixture.home_team_id, fixture.away_team_id, fixture.kickoff_ts, fixture.match_id)
            prediction = await _infer_with_timeout(upcoming_engine.predict, input_data, effective.inference_timeout_seconds)
        except (FixtureResolutionError, PrematchUnavailableError, ValueError, OverflowError, FloatingPointError) as exc:
            LOGGER.warning("Rechazo fixture: %s", exc)
            raise _error(str(exc)) from exc
        payload = asdict(prediction)
        payload["fixture"] = asdict(fixture)
        payload["shadow_catalog"] = build_shadow_observation(app.state.shadow_catalog)
        return payload

    @app.post("/v1/predict/live/fixture", tags=["inference"])
    async def predict_live_fixture(
        request: LiveFixtureRequest,
    ) -> dict[str, Any]:
        """Captura ESPN y ejecuta el motor probabilístico live oficial."""

        if not effective.external_calls_enabled:
            raise _error("external_calls_disabled")
        try:
            result = await _infer_with_timeout(
                lambda payload: app.state.live_runtime.predict_fixture(*payload),
                (request.league_slug, request.match_id, request.date),
                effective.inference_timeout_seconds * 4,
            )
        except (EspnConnectorError, PrematchUnavailableError, ValueError, OSError) as exc:
            LOGGER.warning("Rechazo fixture live: %s", exc)
            raise _error(str(exc)) from exc
        app.state.metrics.mark_hawkes(True, True)
        app.state.metrics.mark_live_layers(True, True)
        app.state.metrics.mark_live_probability_engine(
            str(result.get("official_source") or ""))
        result["shadow_catalog"] = build_shadow_observation(
            app.state.shadow_catalog)
        return result

    @app.post("/v1/predict/live", tags=["inference"])
    async def predict_live(request: LiveRequest) -> dict[str, Any]:
        """Ejecuta el contrato legado y promueve el motor live si hay snapshot."""

        try:
            if request.official_prediction and (
                request.hawkes_enabled or request.hawkes_shadow_mode
                or request.markov_live_enabled or request.markov_live_shadow_mode
            ):
                raise ValueError("Las capas live shadow no pueden activarse para predicciones oficiales.")
            app.state.metrics.mark_hawkes(request.hawkes_enabled, request.hawkes_shadow_mode)
            app.state.metrics.mark_live_layers(
                request.markov_live_enabled,
                request.markov_live_enabled and request.hawkes_enabled,
            )
            live_input = _live_input(request)
            result = await _infer_with_timeout(
                engine.predict_live, live_input, effective.inference_timeout_seconds
            )
        except (ValueError, OverflowError, FloatingPointError) as exc:
            LOGGER.warning("Rechazo live: %s", exc)
            raise _error(str(exc)) from exc
        payload = asdict(result)
        if result.experimental_markov_live is not None:
            payload.update(app.state.live_runtime.officialize(result, live_input))
            app.state.metrics.mark_live_probability_engine(
                str(payload.get("official_source") or ""))
        return payload

    async def _warm_live_catalog() -> None:
        """Mantiene caliente el catálogo live con cadencia adaptativa.

        Cada vuelta llama al mismo endpoint que la Mini App, de modo que
        comparte clave, caché y ventana de refresco sin duplicar lógica.

        La cadencia depende de si hay partidos: refrescar cada 20 s a las
        cuatro de la mañana, sin nadie conectado y sin un solo fixture activo,
        sería quemar llamadas a ESPN y CPU de Railway para nada. Con partidos
        en curso el ritmo iguala al `refetchInterval` del cliente.
        """

        while True:
            active = 0
            try:
                payload = await live_catalog(limit=CATALOG_MAX_LIMIT)
                active = int(payload.get("count") or 0)
            except Exception as exc:  # noqa: BLE001 - un fallo no puede cortar el bucle
                LOGGER.warning("Warmer live sin resultado: %s", exc)
            await asyncio.sleep(
                CATALOG_WARM_ACTIVE_SECONDS if active else CATALOG_WARM_IDLE_SECONDS)

    async def _warm_upcoming_catalog() -> None:
        """Mantiene caliente el catálogo de próximos partidos."""

        while True:
            try:
                await upcoming_catalog(limit=CATALOG_MAX_LIMIT)
            except Exception as exc:  # noqa: BLE001 - un fallo no puede cortar el bucle
                LOGGER.warning("Warmer upcoming sin resultado: %s", exc)
            await asyncio.sleep(CATALOG_WARM_IDLE_SECONDS)

    # Sin llamadas externas no hay barrido que adelantar, y el warmer sólo
    # conseguiría llenar los logs de rechazos.
    app.state.catalog_warmers = (
        (_warm_live_catalog, _warm_upcoming_catalog)
        if effective.catalog_warmer_enabled and effective.external_calls_enabled
        else ()
    )

    return app


def _configuration_ready(config: ServiceConfig) -> bool:
    """Comprueba invariantes necesarias para aceptar tráfico."""

    return (
        config.mode in {"local_dry_run", "operational_readonly"}
        and (not config.external_calls_enabled or config.mode == "operational_readonly")
        and not config.persistence_enabled
        and config.hawkes_enabled == config.hawkes_shadow_mode
        and not (config.official_prediction and config.hawkes_enabled)
        and (not config.authentication_enabled or bool(config.api_key))
    )


def _perimeter_rejection(request: Request, config: ServiceConfig) -> JSONResponse | None:
    """Evalúa autenticación, origen y tamaño declarado."""

    if not _origin_valid(request, config):
        return _gate_error("origin_not_allowed", 403)
    if not _auth_valid(request, config):
        return _gate_error("authentication_required", 401)
    if _body_too_large(request, config.max_request_bytes):
        return _gate_error("request_too_large", 413)
    return None


def _preflight_response(request: Request, config: ServiceConfig) -> Response | None:
    """Responde preflight exclusivamente para orígenes permitidos."""

    if request.method != "OPTIONS":
        return None
    if not _origin_valid(request, config) or not request.headers.get("Origin"):
        return _gate_error("origin_not_allowed", 403)
    response = Response(status_code=204)
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Dikamaha-Key, X-Request-ID"
    response.headers["Access-Control-Max-Age"] = "600"
    return response


async def _call_with_timeout(request: Request, call_next: Any, config: ServiceConfig) -> Any:
    """Ejecuta una request con timeout operativo."""

    try:
        multiplier = 7.0 if request.url.path in {
            "/v1/live", "/v1/upcoming", "/v1/explorer/teams",
            "/v1/high-probability",
        } else 4.0 if request.url.path in {
            "/v1/predict/live/fixture", "/v1/provider/predictor",
            "/v1/provider/markets",
        } else 1.0
        return await asyncio.wait_for(
            call_next(request),
            timeout=config.inference_timeout_seconds * multiplier + 1.0,
        )
    except TimeoutError:
        return _gate_error("inference_timeout", 504)


async def _infer_with_timeout(function: Any, payload: Any, timeout: float) -> Any:
    """Limita la ejecución matemática sin modificar su resultado."""

    try:
        return await asyncio.wait_for(asyncio.to_thread(function, payload), timeout=timeout)
    except TimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail={"code": "inference_timeout", "message": "Inference timeout."},
            headers={"X-Error-Code": "inference_timeout"},
        ) from exc


async def _explorer_call(
    function: Any, arguments: tuple[Any, ...], config: ServiceConfig,
) -> Any:
    """Ejecuta una consulta ESPN read-only con errores sanitizados."""

    if not config.external_calls_enabled:
        raise _error("external_calls_disabled")
    try:
        return await _infer_with_timeout(
            lambda values: function(*values), arguments,
            config.inference_timeout_seconds)
    except (EspnConnectorError, ValueError, OSError) as exc:
        LOGGER.warning("Explorador ESPN rechazado: %s", exc)
        raise _error("explorer_resource_unavailable") from exc


app = create_app()


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host="127.0.0.1", port=8000)

# Version: 1.6.0
# Created: 2026-07-15; updated: 2026-08-08
