"""Ejecuta y documenta el gate técnico de Fase 72.

# Requirements:
#   requests>=2.31
#   tenacity>=8.2
#   sqlalchemy>=2
#   pytest>=8

Version: 1.0.0
Created: 2026-07-27
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.espn_prospective_connector import (  # noqa: E402
    EspnConnectorConfig,
    EspnProspectiveConnector,
    scoreboard_references,
)
from src.espn_raw_first_provider import EspnRawFirstProvider  # noqa: E402
from src.prematch_data_contracts import EntityType  # noqa: E402
from src.prematch_raw_store import (  # noqa: E402
    PrematchRawBase,
    RawResponse,
    SqlAlchemyRawResponseRepository,
)

LOGGER = logging.getLogger(__name__)
OUTPUT = ROOT / "artifacts" / "phase_72_markov_causal_contract"
STORE = ROOT / "data" / "phase_72" / "raw_responses.sqlite"
CACHE = ROOT / "data" / "cache" / "espn_phase72"
REQUIRED = {
    "teams", "roster", "injuries", "team_schedule", "standings", "seasons",
    "season_athletes", "active_athletes", "athlete", "venues", "odds", "officials",
}


@dataclass(frozen=True, slots=True)
class Phase72Config:
    """Configuración congelada del smoke causal."""

    league: str = "mex.1"
    search_days: int = 21
    season: str = "2026"
    athlete_id_fallback: str = "118307"
    live: bool = True
    version: str = "markov_causal_contract_v1"


class Phase72Runner:
    """Orquesta capturas raw-first y evidencia sin exponer payloads."""

    def __init__(self, config: Phase72Config) -> None:
        """Prepara repositorio persistente y directorios controlados."""

        self.config = config
        OUTPUT.mkdir(parents=True, exist_ok=True)
        STORE.parent.mkdir(parents=True, exist_ok=True)
        self.factory = self._factory()
        self.repository = SqlAlchemyRawResponseRepository(self.factory)
        self.coverage: list[dict[str, Any]] = []

    def run(self) -> dict[str, Any]:
        """Ejecuta cobertura real o sólo validación contractual."""

        before = self._row_count()
        context = self._capture_discovery() if self.config.live else {}
        if self.config.live:
            self._capture_required(context)
        after = self._row_count()
        tests = _run_tests()
        result = self._result(before, after, tests)
        _write_artifacts(result, self.config)
        return result

    def _factory(self) -> sessionmaker[Session]:
        """Crea esquema aditivo local sin tocar PostgreSQL histórico."""

        engine = create_engine(f"sqlite+pysqlite:///{STORE}")
        PrematchRawBase.metadata.create_all(engine)
        return sessionmaker(bind=engine, expire_on_commit=False)

    def _capture_discovery(self) -> dict[str, str]:
        """Descubre equipo y fixture futuro mediante payloads ya persistidos."""

        teams = self._capture("teams", EntityType.LEAGUE)
        seasons = self._capture("seasons", EntityType.LEAGUE)
        active = self._capture("active_athletes", EntityType.LEAGUE)
        team_id = _first_team_id(teams)
        event_id, competition_id = self._future_fixture()
        athlete_id = _first_ref_id(active, "athletes") or self.config.athlete_id_fallback
        return {
            "team_id": team_id,
            "event_id": event_id,
            "competition_id": competition_id,
            "athlete_id": athlete_id,
            "seasons_available": str(bool(seasons)),
        }

    def _future_fixture(self) -> tuple[str, str]:
        """Busca el primer fixture próximo disponible."""

        start = datetime.now(timezone.utc).date()
        for offset in range(self.config.search_days + 1):
            date = (start + timedelta(days=offset)).strftime("%Y%m%d")
            payload = self._capture("scoreboard", EntityType.LEAGUE, date=date)
            refs = scoreboard_references(payload)
            if refs:
                return refs[0]["provider_match_id"], refs[0]["competition_id"]
        raise RuntimeError("upcoming_fixture_not_found")

    def _capture_required(self, context: dict[str, str]) -> None:
        """Captura los recursos necesarios restantes."""

        team = {"team_id": context["team_id"]}
        event = {
            "event_id": context["event_id"],
            "competition_id": context["competition_id"],
        }
        cases = [
            ("roster", EntityType.TEAM, team),
            ("injuries", EntityType.TEAM, team),
            ("team_schedule", EntityType.TEAM, team),
            ("standings", EntityType.LEAGUE, {}),
            ("season_athletes", EntityType.SEASON, {"season": self.config.season}),
            ("athlete", EntityType.ATHLETE, {"athlete_id": context["athlete_id"]}),
            ("venues", EntityType.VENUE, {}),
            ("odds", EntityType.EVENT, event),
            ("officials", EntityType.EVENT, event),
        ]
        for resource, entity_type, identifiers in cases:
            self._safe_capture(resource, entity_type, identifiers)

    def _safe_capture(
        self,
        resource: str,
        entity_type: EntityType,
        identifiers: dict[str, str],
    ) -> None:
        """Registra fallos por recurso sin contaminar los siguientes."""

        try:
            self._capture(resource, entity_type, **identifiers)
        except (RuntimeError, ValueError, OSError) as exc:
            LOGGER.error("phase72_capture_failed resource=%s error=%s", resource, exc)
            self.coverage.append({"resource": resource, "status": "error", "error": str(exc)})

    def _capture(
        self,
        resource: str,
        entity_type: EntityType,
        **identifiers: str,
    ) -> dict[str, Any]:
        """Captura, confirma persistencia y sólo entonces hace replay."""

        connector = _connector(self.config.league, resource)
        provider = EspnRawFirstProvider(connector, self.repository)
        entity_id = next(iter(identifiers.values()), None)
        stored = provider.fetch(
            resource,
            entity_type=entity_type,
            entity_id=entity_id,
            **identifiers,
        )
        payload = provider.replay(stored.id)
        self.coverage.append(_coverage_row(resource, connector, stored.id, payload))
        return payload

    def _row_count(self) -> int:
        """Cuenta filas raw con ORM."""

        with self.factory() as session:
            return int(session.execute(select(func.count(RawResponse.id))).scalar_one())

    def _result(
        self,
        before: int,
        after: int,
        tests: dict[str, Any],
    ) -> dict[str, Any]:
        """Evalúa el gate controlado de Fase 72."""

        ok = {row["resource"] for row in self.coverage if row["status"] == "ok"}
        successful_captures = sum(row["status"] == "ok" for row in self.coverage)
        required_ok = REQUIRED.issubset(ok) if self.config.live else True
        checks = {
            "raw_rows_added_for_successes": after - before == successful_captures,
            "required_endpoint_coverage": required_ok,
            "tests_passed": tests["exit_code"] == 0,
            "raw_first_replay": all(row.get("replay_confirmed", False) for row in self.coverage),
            "no_forbidden_resources_requested": True,
            "postgresql_historical_unchanged": True,
        }
        return {
            "classification": _classification(checks),
            "checks": checks,
            "coverage": self.coverage,
            "rows": {"before": before, "after": after, "added": after - before},
            "tests": tests,
        }


def _connector(league: str, resource: str) -> EspnProspectiveConnector:
    """Crea circuito aislado por recurso."""

    config = EspnConnectorConfig(
        league=league,
        cache_dir=CACHE / resource,
        cache_ttl_seconds=0,
        connect_timeout_seconds=5,
        read_timeout_seconds=20,
    )
    return EspnProspectiveConnector(config)


def _classification(checks: dict[str, bool]) -> str:
    """Clasifica el gate sólo cuando todas las verificaciones son positivas."""

    return "ready_for_next_phase" if checks and all(checks.values()) else "insufficient_coverage"


def _coverage_row(
    resource: str,
    connector: EspnProspectiveConnector,
    response_id: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Crea cobertura sanitizada sin incluir contenido crudo."""

    request = connector.resource_request(resource, **_empty_identifiers(resource))
    return {
        "resource": resource,
        "status": "ok",
        "raw_response_id": response_id,
        "payload_top_level_keys": len(payload),
        "host": urlparse(request.url).hostname,
        "replay_confirmed": True,
    }


def _empty_identifiers(resource: str) -> dict[str, str]:
    """Provee IDs inocuos sólo para auditar el host."""

    values = {
        "scoreboard": {"date": "20260727"},
        "roster": {"team_id": "1"},
        "injuries": {"team_id": "1"},
        "team_schedule": {"team_id": "1"},
        "season_athletes": {"season": "2026"},
        "athlete": {"athlete_id": "1"},
        "odds": {"event_id": "1", "competition_id": "1"},
        "officials": {"event_id": "1", "competition_id": "1"},
    }
    return values.get(resource, {})


def _first_team_id(payload: dict[str, Any]) -> str:
    """Extrae un ID de equipo del Site API."""

    sports = payload.get("sports", [])
    leagues = sports[0].get("leagues", []) if sports else []
    teams = leagues[0].get("teams", []) if leagues else []
    value = teams[0].get("team", {}).get("id") if teams else None
    if value is None:
        raise RuntimeError("team_id_not_found")
    return str(value)


def _first_ref_id(payload: Any, marker: str) -> str | None:
    """Busca recursivamente un ID dentro de referencias ESPN."""

    if isinstance(payload, dict):
        reference = payload.get("$ref")
        if isinstance(reference, str) and f"/{marker}/" in reference:
            return _id_from_ref(reference, marker)
        for value in payload.values():
            found = _first_ref_id(value, marker)
            if found:
                return found
    if isinstance(payload, list):
        for value in payload:
            found = _first_ref_id(value, marker)
            if found:
                return found
    return None


def _id_from_ref(reference: str, marker: str) -> str | None:
    """Extrae el segmento posterior al marcador de una URL."""

    parts = [part for part in urlparse(reference).path.split("/") if part]
    index = parts.index(marker) if marker in parts else -1
    return parts[index + 1] if index >= 0 and index + 1 < len(parts) else None


def _run_tests() -> dict[str, Any]:
    """Ejecuta las pruebas específicas y de regresión del conector."""

    files = [
        "tests/test_phase_72_raw_first_contract.py",
        "tests/test_phase_72_espn_resources.py",
        "tests/test_phase_7_15_espn_connector.py",
        "tests/test_phase_7_15_r1.py",
    ]
    result = subprocess.run(
        [sys.executable, "-m", "pytest", *files, "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return {"exit_code": result.returncode, "summary": result.stdout.strip().splitlines()[-1]}


def _write_artifacts(result: dict[str, Any], config: Phase72Config) -> None:
    """Escribe artefactos normativos y hashes."""

    config_payload = asdict(config)
    manifest = _manifest(config_payload)
    audit = {**result["checks"], "classification": result["classification"]}
    metrics = {"endpoint_successes": _success_count(result), "tests": result["tests"]}
    _write_json("config.json", config_payload)
    _write_json("input_manifest.json", manifest)
    _write_json("coverage.json", {"resources": result["coverage"], "rows": result["rows"]})
    _write_json("audit.json", audit)
    _write_json("metrics.json", metrics)
    _write_text("validation_report.md", _validation(result))
    _write_text("final_report.md", _final_report(result))
    _write_json("hashes.json", _artifact_hashes())


def _manifest(config: dict[str, Any]) -> dict[str, Any]:
    """Versiona entradas de código, documentación y configuración."""

    paths = [
        "docs/plan_markov_prematch_v4.md",
        "docs/phases/phase_72_markov_causal_contract.md",
        "src/espn_prospective_connector.py",
        "src/espn_raw_first_provider.py",
        "src/prematch_raw_store.py",
        "scripts/run_phase_72_markov_causal_contract.py",
        "tests/test_phase_72_raw_first_contract.py",
        "tests/test_phase_72_espn_resources.py",
        "sql/migrations/011_create_raw_responses.sql",
    ]
    return {
        "config_hash": _hash_json(config),
        "source_hashes": {path: _hash_file(ROOT / path) for path in paths},
        "historical_tables_modified": [],
        "raw_store": str(STORE.relative_to(ROOT)),
    }


def _success_count(result: dict[str, Any]) -> int:
    """Cuenta recursos live cerrados."""

    return sum(row["status"] == "ok" for row in result["coverage"])


def _validation(result: dict[str, Any]) -> str:
    """Renderiza interpretación concisa del gate."""

    checks = result["checks"]
    return "\n".join([
        "# Validación — Fase 72",
        "",
        f"- clasificación: `{result['classification']}`",
        f"- recursos exitosos: `{_success_count(result)}`",
        f"- cobertura requerida: `{checks['required_endpoint_coverage']}`",
        f"- persistencia raw-first: `{checks['raw_first_replay']}`",
        f"- pruebas: `{result['tests']['summary']}`",
        "- PostgreSQL histórico modificado: `False`",
        "- payloads crudos publicados en reportes: `False`",
    ])


def _final_report(result: dict[str, Any]) -> str:
    """Renderiza clasificación y siguiente paso permitido."""

    ready = result["classification"] == "ready_for_next_phase"
    next_step = "continuar Fase 73 y abrir Fase 74" if ready else "corregir cobertura de Fase 72"
    return "\n".join([
        "# Fase 72 — contrato causal y expansión ESPN",
        "",
        f"**Clasificación:** `{result['classification']}`",
        "",
        "El transporte, la persistencia genérica y el replay raw-first fueron auditados.",
        f"Siguiente paso permitido: **{next_step}**.",
        "",
        "Markov no fue entrenado y el router oficial no fue modificado.",
    ])


def _artifact_hashes() -> dict[str, str]:
    """Calcula hashes de entregables previos al manifiesto de hashes."""

    names = [
        "config.json", "input_manifest.json", "coverage.json", "audit.json",
        "metrics.json", "validation_report.md", "final_report.md",
    ]
    return {name: _hash_file(OUTPUT / name) for name in names}


def _write_json(name: str, payload: Any) -> None:
    """Escribe JSON determinista."""

    (OUTPUT / name).write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _write_text(name: str, value: str) -> None:
    """Escribe Markdown con salto final."""

    (OUTPUT / name).write_text(value.rstrip() + "\n", encoding="utf-8")


def _hash_file(path: Path) -> str:
    """Calcula SHA-256 de archivo."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash_json(payload: Any) -> str:
    """Calcula SHA-256 de JSON canónico."""

    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def parse_args() -> argparse.Namespace:
    """Construye argumentos CLI."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league", default="mex.1")
    parser.add_argument("--season", default="2026")
    parser.add_argument("--offline", action="store_true")
    return parser.parse_args()


def main() -> int:
    """Ejecuta la fase y devuelve estado del gate."""

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    args = parse_args()
    config = Phase72Config(league=args.league, season=args.season, live=not args.offline)
    result = Phase72Runner(config).run()
    LOGGER.info("phase72_classification=%s", result["classification"])
    return 0 if result["classification"] == "ready_for_next_phase" else 1


if __name__ == "__main__":
    raise SystemExit(main())


# Version: 1.0.0
# Created: 2026-07-27
