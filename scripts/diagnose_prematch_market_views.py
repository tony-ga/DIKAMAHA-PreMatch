"""Inspecciona qué mercados pre-match publica realmente el runtime servido.

Diagnóstico de sólo lectura para el reporte "en predicciones pre-match aún no
aparecen córners y la escalera auditada no tiene segundo tiempo". El backend
calcula dos vistas independientes con reglas de supresión distintas y hasta
ahora nadie las había contrastado lado a lado contra el runtime real:

- `audited_market_ladder_view` (escalera auditada) aplica tres filtros por
  métrica -`absent`, cobertura medida, `model_weights > 0`- y sólo cubre
  primera mitad y partido completo por diseño.
- `bounded_market_grid_view` (rejilla adaptativa) viene del modelo Markov de
  Fase 88 y sí cubre segundo tiempo y córners, pero desaparece por completo
  si `_safe_markov` degrada.

Las dos hipótesis que este script separa:

  H1  la liga consultada tiene córners suprimidos por el mapa de cobertura;
      la rejilla conserva el resto de métricas y los tres periodos.
  H2  `_safe_markov` está fallando, así que la rejilla queda reducida a las
      tres filas de `shots_on_target` de Fase 84A -todas `full_match`-, lo
      que explicaría la ausencia simultánea de córners y de segundo tiempo.

El discriminante es `provenance.team_market_markov.status`, que se imprime
junto al conteo de filas por periodo.

No escribe artefactos ni toca la base de datos: construye la misma
`UpcomingMatchInput` que sirve la API y lee la salida.

# Requirements:
#   joblib>=1.4
#   numpy>=2.0

Version: 1.0.0
Created: 2026-08-13
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.metric_coverage import MetricCoverage  # noqa: E402
from src.prematch_snapshot_registry import resolve_active_snapshot  # noqa: E402
from src.universal_prematch import (  # noqa: E402
    UniversalPrematchEngine,
    UpcomingMatchInput,
    _load_windows,
    _matches,
)

LOGGER = logging.getLogger(__name__)
DEFAULT_LEAGUES = ("esp.1", "eng.1", "ita.1")
# Fuera del rango de cualquier partido del snapshot: el runtime excluye el
# partido objetivo por corte causal, así que una fecha futura garantiza que
# todo el historial de ambos equipos entra al ajuste.
FUTURE_KICKOFF = "2030-01-10T20:00:00+00:00"
# El sidecar de conteos exige `match_id` entero para excluir el partido
# objetivo del historial; un `None` degrada la vista shadow completa.
DIAGNOSTIC_MATCH_ID = 9_900_124


def _parser() -> argparse.ArgumentParser:
    """Define el conjunto de ligas a inspeccionar."""

    parser = argparse.ArgumentParser(
        description="Diagnóstico de vistas de mercado pre-match")
    parser.add_argument(
        "--league", action="append", dest="leagues", default=None,
        help="Liga a inspeccionar; repetible. Por defecto: "
             + ", ".join(DEFAULT_LEAGUES))
    parser.add_argument(
        "--json", type=Path, default=None,
        help="Vuelca el diagnóstico completo a un archivo JSON")
    return parser


def _sample_fixture(
    matches: list[dict[str, Any]], league: str,
) -> tuple[int, int] | None:
    """Elige el enfrentamiento más reciente de esa liga en el snapshot.

    Se toma del propio snapshot para que ambos equipos tengan historial
    causal real; inventar identificadores produciría un fallback de
    cobertura que no dice nada sobre el defecto que se investiga.
    """

    rows = [
        row for row in matches
        if str(row.get("league_slug")) == league
        and row.get("home_team_id") is not None
        and row.get("away_team_id") is not None
    ]
    if not rows:
        return None
    latest = max(rows, key=lambda row: str(row["match_date"]))
    return int(latest["home_team_id"]), int(latest["away_team_id"])


def _group_rows(rows: Any) -> dict[tuple[str, str], int]:
    """Cuenta filas de una vista por (métrica, periodo)."""

    counts: dict[tuple[str, str], int] = Counter()
    if not isinstance(rows, list):
        return counts
    for row in rows:
        if not isinstance(row, dict):
            continue
        counts[(str(row.get("metric")), str(row.get("period")))] += 1
    return counts


def _render(title: str, counts: dict[tuple[str, str], int]) -> list[str]:
    """Formatea el conteo por métrica y periodo en bloque legible."""

    if not counts:
        return [f"  {title}: VACÍA"]
    by_metric: dict[str, list[str]] = defaultdict(list)
    for (metric, period), total in sorted(counts.items()):
        by_metric[metric].append(f"{period}={total}")
    lines = [f"  {title}: {sum(counts.values())} filas"]
    lines.extend(
        f"    {metric:<24} {'  '.join(periods)}"
        for metric, periods in sorted(by_metric.items()))
    return lines


def _diagnose(league: str, matches: list[dict[str, Any]],
              coverage: MetricCoverage) -> dict[str, Any]:
    """Ejecuta una predicción real y resume ambas vistas de mercado."""

    teams = _sample_fixture(matches, league)
    if teams is None:
        return {"league_slug": league, "error": "no_fixture_in_snapshot"}
    home, away = teams
    request = UpcomingMatchInput(
        league_slug=league, home_team_id=home, away_team_id=away,
        kickoff_ts=FUTURE_KICKOFF, match_id=DIAGNOSTIC_MATCH_ID)
    try:
        prediction = UniversalPrematchEngine().predict(request)
    except Exception as error:  # noqa: BLE001 - el diagnóstico nunca aborta
        return {"league_slug": league, "error": f"{type(error).__name__}: {error}"}

    shadow = prediction.experimental_team_markets or {}
    provenance = shadow.get("provenance") or {}
    markov = provenance.get("team_market_markov") or {}
    grid = _group_rows(shadow.get("bounded_market_grid_view"))
    ladder = _group_rows(shadow.get("audited_market_ladder_view"))
    return {
        "league_slug": league,
        "home_team_id": home,
        "away_team_id": away,
        "shadow_status": str(shadow.get("status")),
        "markov_status": str(markov.get("status") or "missing"),
        "markov_reason": str(markov.get("reason") or ""),
        "grid": {f"{m}|{p}": n for (m, p), n in grid.items()},
        "ladder": {f"{m}|{p}": n for (m, p), n in ladder.items()},
        "grid_periods": sorted({p for _, p in grid}),
        "grid_has_corners": any(m == "corners" for m, _ in grid),
        "ladder_has_corners": any(m == "corners" for m, _ in ladder),
        "coverage_corners": _coverage_verdict(coverage, league, "corners"),
        "coverage_corners_first_half": _coverage_verdict(
            coverage, league, "corners_first_half"),
    }


def _coverage_verdict(
    coverage: MetricCoverage, league: str, metric: str,
) -> str:
    """Traduce el mapa de cobertura al veredicto de las tres vías.

    `absent` suprime en las dos vistas, `covered` no suprime en ninguna, y
    `unmapped`/`insufficient_evidence` es el hueco que sólo cierra la
    escalera auditada -la rejilla los deja pasar, ver §3.1 del plan-.
    """

    if metric in coverage.absent_metrics(league):
        return "absent"
    return "covered" if coverage.is_covered(league, metric) else "unmapped_or_insufficient"


def _verdict(reports: list[dict[str, Any]]) -> str:
    """Concluye H1 o H2 a partir del estado real del Markov."""

    usable = [row for row in reports if "error" not in row]
    if not usable:
        return "INDETERMINADO: ninguna liga produjo predicción"
    broken = [row for row in usable if row["markov_status"] != "available"]
    if broken:
        reasons = sorted({row["markov_reason"] for row in broken})
        return (
            f"H2: `_safe_markov` degradado en {len(broken)}/{len(usable)} ligas "
            f"(motivo: {', '.join(reasons) or 'sin reportar'}). La rejilla "
            "pierde córners y segundo tiempo a la vez porque pierde toda la "
            "vista distribucional de Fase 88.")
    without = [row for row in usable if not row["grid_has_corners"]]
    if not without:
        return (
            "NINGUNA: la rejilla sí publica córners en todas las ligas "
            "probadas. El defecto reportado es específico de la liga que el "
            "usuario consultó, o del despliegue, no del runtime.")
    return (
        f"H1: Markov disponible en todas, pero {len(without)}/{len(usable)} "
        "ligas no publican córners en la rejilla; el mapa de cobertura es la "
        "causa (ver `coverage_corners`).")


def main() -> int:
    """Inspecciona cada liga y publica el veredicto."""

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _parser().parse_args()
    leagues = args.leagues or list(DEFAULT_LEAGUES)

    snapshot = resolve_active_snapshot()
    LOGGER.info("snapshot activo: %s", snapshot)
    matches = _matches(_load_windows(str(snapshot)))
    coverage = MetricCoverage()

    reports = [_diagnose(league, matches, coverage) for league in leagues]
    for report in reports:
        LOGGER.info("")
        LOGGER.info("=== %s ===", report["league_slug"])
        if "error" in report:
            LOGGER.info("  ERROR: %s", report["error"])
            continue
        LOGGER.info(
            "  fixture=%s vs %s  shadow=%s  markov=%s %s",
            report["home_team_id"], report["away_team_id"],
            report["shadow_status"], report["markov_status"],
            report["markov_reason"])
        LOGGER.info(
            "  cobertura córners=%s  córners 1T=%s",
            report["coverage_corners"], report["coverage_corners_first_half"])
        for line in _render(
                "rejilla adaptativa",
                Counter({tuple(k.split("|")): v
                         for k, v in report["grid"].items()})):
            LOGGER.info(line)
        for line in _render(
                "escalera auditada",
                Counter({tuple(k.split("|")): v
                         for k, v in report["ladder"].items()})):
            LOGGER.info(line)

    verdict = _verdict(reports)
    LOGGER.info("")
    LOGGER.info("VEREDICTO: %s", verdict)

    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(
            {"snapshot": str(snapshot), "verdict": verdict,
             "leagues": reports},
            indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        LOGGER.info("escrito %s", args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# Version: 1.0.0
# Created: 2026-08-13
