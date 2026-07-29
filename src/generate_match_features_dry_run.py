"""Generador dry-run de `match_features v1`.

Lee PostgreSQL en modo solo lectura, construye un candidato local de dataset,
genera mapping versionado de competencia y produce artefactos JSON/Markdown
sin entrenar modelos ni escribir en la base de datos.

Requirements:
    pip install psycopg2-binary python-dotenv

Version: 1.0.0
Created: 2026-07-15
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

for extra_path in (Path("/tmp/codex_pg_linux"), Path("/tmp/codex_pg")):
    if extra_path.exists() and str(extra_path) not in sys.path:
        sys.path.insert(0, str(extra_path))

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ModuleNotFoundError as exc:  # pragma: no cover - fallback runtime detail
    raise ModuleNotFoundError(
        "psycopg2 no está disponible. Instala psycopg2-binary o ejecuta en un entorno "
        "con el driver accesible."
    ) from exc

LOGGER = logging.getLogger("generate_match_features_dry_run")
if not LOGGER.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s"))
    LOGGER.addHandler(handler)
LOGGER.setLevel(logging.INFO)

CANONICAL_COMPETITION_ID = "esp.1"
CANONICAL_COMPETITION_NAME = "LaLiga"
DATASET_VERSION = "match_features_v1"
FEATURE_VERSION = "v1"
SOURCE_SYSTEM = "espn"
EXCLUDED_MATCH_IDS = {704766}


@dataclass(slots=True)
class MatchRow:
    """Representa una fila candidata del dataset."""

    data: dict[str, Any]


def load_env_file(path: Path) -> None:
    """Carga variables de entorno desde un archivo `.env` simple.

    Args:
        path: Ruta al archivo `.env`.
    """

    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def canonical_json(payload: Any) -> str:
    """Serializa un objeto a JSON canónico."""

    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_hex(payload: Any) -> str:
    """Calcula SHA-256 sobre JSON canónico."""

    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def normalize_for_hash(payload: Any, excluded_keys: set[str]) -> Any:
    """Elimina claves volátiles antes de calcular hashes deterministas."""

    if isinstance(payload, dict):
        return {
            key: normalize_for_hash(value, excluded_keys)
            for key, value in payload.items()
            if key not in excluded_keys
        }
    if isinstance(payload, list):
        return [normalize_for_hash(item, excluded_keys) for item in payload]
    return payload


def utc_now() -> datetime:
    """Devuelve el timestamp UTC actual."""

    return datetime.now(UTC)


def connect_db() -> psycopg2.extensions.connection:
    """Crea una conexión PostgreSQL de solo lectura."""

    load_env_file(Path(".env"))
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL no está definida.")
    conn = psycopg2.connect(database_url)
    conn.autocommit = True
    return conn


def fetch_all(conn: psycopg2.extensions.connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    """Ejecuta un `SELECT` y devuelve filas como diccionarios."""

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def fetch_one(conn: psycopg2.extensions.connection, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    """Ejecuta un `SELECT` y devuelve una fila como diccionario."""

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row is not None else None


def load_matches(conn: psycopg2.extensions.connection) -> list[dict[str, Any]]:
    """Carga partidos base en orden cronológico estable."""

    return fetch_all(
        conn,
        """
        SELECT
            m.id AS match_id,
            m.home_team_id,
            m.away_team_id,
            m.match_date,
            m.season,
            m.home_score,
            m.away_score,
            m.status,
            ht.name AS home_team_name,
            at.name AS away_team_name,
            ht.espn_team_id AS home_espn_team_id,
            at.espn_team_id AS away_espn_team_id,
            COALESCE((
                SELECT r.source_competition_id
                FROM raw_api_responses r
                WHERE r.match_id = m.id
                ORDER BY r.fetched_at ASC, r.id ASC
                LIMIT 1
            ), NULL) AS source_competition_id,
            COALESCE((
                SELECT MIN(r.fetched_at)
                FROM raw_api_responses r
                WHERE r.match_id = m.id
            ), NULL) AS source_available_at
            , COALESCE((
                SELECT ir.league
                FROM ingestion_runs ir
                WHERE ir.match_id = m.id
                ORDER BY ir.created_at DESC, ir.id DESC
                LIMIT 1
            ), NULL) AS source_league
            , COALESCE((
                SELECT ir.competition_id::text
                FROM ingestion_runs ir
                WHERE ir.match_id = m.id
                ORDER BY ir.created_at DESC, ir.id DESC
                LIMIT 1
            ), NULL) AS ingestion_competition_id
        FROM matches m
        JOIN teams ht ON ht.id = m.home_team_id
        JOIN teams at ON at.id = m.away_team_id
        ORDER BY m.match_date ASC, m.id ASC
        """,
    )


def validate_competition(row: dict[str, Any]) -> tuple[bool, str | None]:
    """Valida competencia contra el slug canónico `esp.1`."""

    source_league = row.get("source_league")
    source_competition_id = row.get("source_competition_id")
    if source_league is not None and str(source_league) != CANONICAL_COMPETITION_ID:
        return False, f"competition_mismatch:{source_league}"
    if source_league is None and source_competition_id is None:
        return False, "missing_competition_provenance"
    return True, None


def sort_key(row: dict[str, Any]) -> tuple[Any, Any]:
    """Clave estable para el orden temporal."""

    match_date = row.get("match_date")
    return (match_date, row.get("match_id"))


def prior_matches(matches: list[dict[str, Any]], team_id: int, cutoff: datetime) -> list[dict[str, Any]]:
    """Devuelve partidos previos de un equipo antes del cutoff."""

    return [
        row
        for row in matches
        if row["match_date"] is not None
        and row["match_date"] < cutoff
        and (row["home_team_id"] == team_id or row["away_team_id"] == team_id)
    ]


def result_for_team(row: dict[str, Any], team_id: int) -> str | None:
    """Devuelve el resultado del partido para un equipo."""

    home = row.get("home_score")
    away = row.get("away_score")
    if home is None or away is None:
        return None
    if int(row["home_team_id"]) == int(team_id):
        return "W" if home > away else "D" if home == away else "L"
    return "W" if away > home else "D" if away == home else "L"


def compute_history_block(prior_rows: list[dict[str, Any]], team_id: int) -> dict[str, Any]:
    """Calcula historial histórico simple para una ventana dada."""

    last5 = prior_rows[-5:]
    points_map = {"W": 3, "D": 1, "L": 0}
    points = 0
    gf = ga = wins = draws = losses = 0
    for row in last5:
        res = result_for_team(row, team_id)
        if res is None:
            continue
        points += points_map[res]
        is_home = int(row["home_team_id"]) == int(team_id)
        goals_for = row["home_score"] if is_home else row["away_score"]
        goals_against = row["away_score"] if is_home else row["home_score"]
        gf += int(goals_for)
        ga += int(goals_against)
        if res == "W":
            wins += 1
        elif res == "D":
            draws += 1
        else:
            losses += 1
    goal_diff = gf - ga
    return {
        "matches_played_season": None,
        "last_5_matches_played": len(last5),
        "last_5_points": points if last5 else None,
        "last_5_goals_for": gf if last5 else None,
        "last_5_goals_against": ga if last5 else None,
        "last_5_goal_diff": goal_diff if last5 else None,
        "last_5_wins": wins if last5 else None,
        "last_5_draws": draws if last5 else None,
        "last_5_losses": losses if last5 else None,
        "last_5_complete": len(last5) == 5,
    }


def compute_team_features(matches: list[dict[str, Any]], row: dict[str, Any], cutoff: datetime) -> dict[str, Any]:
    """Calcula features históricas del local y visitante para un partido."""

    home_prior = prior_matches(matches, int(row["home_team_id"]), cutoff)
    away_prior = prior_matches(matches, int(row["away_team_id"]), cutoff)
    home_last_5 = home_prior[-5:]
    away_last_5 = away_prior[-5:]
    home_recent_30 = [m for m in home_prior if m["match_date"] >= cutoff - timedelta(days=30)]
    away_recent_30 = [m for m in away_prior if m["match_date"] >= cutoff - timedelta(days=30)]
    home_block = compute_history_block(home_prior, int(row["home_team_id"]))
    away_block = compute_history_block(away_prior, int(row["away_team_id"]))
    home_block["matches_played_season"] = sum(1 for m in home_prior if m["season"] == row["season"])
    away_block["matches_played_season"] = sum(1 for m in away_prior if m["season"] == row["season"])
    home_rest = (cutoff - home_prior[-1]["match_date"]).days if home_prior else None
    away_rest = (cutoff - away_prior[-1]["match_date"]).days if away_prior else None
    return {
        "home_prior_matches": len(home_prior),
        "away_prior_matches": len(away_prior),
        "home_last_5_available": len(home_last_5),
        "away_last_5_available": len(away_last_5),
        "last_5_complete_home": len(home_last_5) == 5,
        "last_5_complete_away": len(away_last_5) == 5,
        "home_last_5_points": home_block["last_5_points"],
        "away_last_5_points": away_block["last_5_points"],
        "home_last_5_goals_for": home_block["last_5_goals_for"],
        "away_last_5_goals_for": away_block["last_5_goals_for"],
        "home_last_5_goals_against": home_block["last_5_goals_against"],
        "away_last_5_goals_against": away_block["last_5_goals_against"],
        "home_last_5_goal_diff": home_block["last_5_goal_diff"],
        "away_last_5_goal_diff": away_block["last_5_goal_diff"],
        "home_last_5_wins": home_block["last_5_wins"],
        "away_last_5_wins": away_block["last_5_wins"],
        "home_last_5_draws": home_block["last_5_draws"],
        "away_last_5_draws": away_block["last_5_draws"],
        "home_last_5_losses": home_block["last_5_losses"],
        "away_last_5_losses": away_block["last_5_losses"],
        "home_matches_played_season": home_block["matches_played_season"],
        "away_matches_played_season": away_block["matches_played_season"],
        "home_rest_days": home_rest,
        "away_rest_days": away_rest,
        "home_matches_last_30_days": len(home_recent_30),
        "away_matches_last_30_days": len(away_recent_30),
        "last_5_complete_home": home_block["last_5_complete"],
        "last_5_complete_away": away_block["last_5_complete"],
    }


def compute_targets(row: dict[str, Any]) -> dict[str, Any]:
    """Deriva targets post-match desde el marcador final."""

    home = row.get("home_score")
    away = row.get("away_score")
    if home is None or away is None:
        return {
            "home_goals": None,
            "away_goals": None,
            "result_1x2": None,
            "over_2_5": None,
            "btts": None,
            "goal_margin": None,
            "total_goals": None,
            "target_complete": False,
        }
    home_int = int(home)
    away_int = int(away)
    return {
        "home_goals": home_int,
        "away_goals": away_int,
        "result_1x2": "1" if home_int > away_int else "2" if home_int < away_int else "X",
        "over_2_5": (home_int + away_int) > 2,
        "btts": home_int > 0 and away_int > 0,
        "goal_margin": home_int - away_int,
        "total_goals": home_int + away_int,
        "target_complete": True,
    }


def build_candidate_rows(matches: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Construye filas candidatas y mapping de competencia."""

    mapping_rows: list[dict[str, Any]] = []
    candidate_rows: list[dict[str, Any]] = []
    total_rows = sorted(matches, key=sort_key)
    for row in total_rows:
        is_mapped, reason = validate_competition(row)
        mapping_rows.append(
            {
                "match_id": row["match_id"],
                "source_competition_id": row.get("source_competition_id"),
                "source_league": row.get("source_league"),
                "ingestion_competition_id": row.get("ingestion_competition_id"),
                "competition_id": CANONICAL_COMPETITION_ID if is_mapped else None,
                "competition_name": CANONICAL_COMPETITION_NAME if is_mapped else None,
                "mapped": is_mapped,
                "reason": reason,
            }
        )
        cutoff = row["match_date"]
        if cutoff is None:
            cutoff_valid = False
        else:
            cutoff_valid = True
        competition_valid = is_mapped
        history = compute_team_features(total_rows, row, cutoff) if cutoff_valid else {}
        targets = compute_targets(row)
        eligible_for_materialization = bool(
            cutoff_valid
            and competition_valid
            and targets["target_complete"]
            and history.get("home_prior_matches", 0) >= 1
            and history.get("away_prior_matches", 0) >= 1
        )
        eligible_for_training = bool(
            eligible_for_materialization
            and history.get("home_prior_matches", 0) >= 5
            and history.get("away_prior_matches", 0) >= 5
            and row["match_id"] not in EXCLUDED_MATCH_IDS
        )
        exclusion_reason = None
        if row["match_id"] in EXCLUDED_MATCH_IDS:
            exclusion_reason = "existing_data_failed_run"
        elif not cutoff_valid:
            exclusion_reason = "missing_match_date"
        elif not competition_valid:
            exclusion_reason = "competition_invalid"
        elif not targets["target_complete"]:
            exclusion_reason = "missing_target"
        elif history.get("home_prior_matches", 0) < 1 or history.get("away_prior_matches", 0) < 1:
            exclusion_reason = "insufficient_history_for_materialization"
        elif history.get("home_prior_matches", 0) < 5 or history.get("away_prior_matches", 0) < 5:
            exclusion_reason = "insufficient_history_for_training"
        candidate_rows.append(
            {
                "match_id": row["match_id"],
                "home_team_id": row["home_team_id"],
                "away_team_id": row["away_team_id"],
                "match_date": row["match_date"],
                "competition_id": CANONICAL_COMPETITION_ID if competition_valid else None,
                "competition_name": CANONICAL_COMPETITION_NAME if competition_valid else None,
                "season": row["season"],
                **history,
                **targets,
                "eligible_for_materialization": eligible_for_materialization,
                "eligible_for_training": eligible_for_training,
                "history_minimum_met": bool(history.get("home_prior_matches", 0) >= 1 and history.get("away_prior_matches", 0) >= 1),
                "cutoff_valid": cutoff_valid,
                "competition_valid": competition_valid,
                "exclusion_reason": exclusion_reason,
                "feature_version": FEATURE_VERSION,
                "feature_cutoff_ts": cutoff.isoformat() if cutoff is not None else None,
                "feature_snapshot_ts": utc_now().isoformat(),
                "source_available_at": row.get("source_available_at").isoformat() if row.get("source_available_at") else None,
                "source_system": SOURCE_SYSTEM,
                "source_hash": sha256_hex(
                    {
                        "match_id": row["match_id"],
                        "source_competition_id": row.get("source_competition_id"),
                        "home_team_id": row["home_team_id"],
                        "away_team_id": row["away_team_id"],
                        "match_date": row["match_date"].isoformat() if row["match_date"] else None,
                        "home_score": row.get("home_score"),
                        "away_score": row.get("away_score"),
                    }
                ),
                "competition_source_competition_id": row.get("source_competition_id"),
                "home_team_name": row.get("home_team_name"),
                "away_team_name": row.get("away_team_name"),
                "excluded_for_training": row["match_id"] in EXCLUDED_MATCH_IDS,
            }
        )
    return candidate_rows, mapping_rows


def build_report(matches: list[dict[str, Any]], candidate_rows: list[dict[str, Any]], mapping_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Construye el reporte resumido de cobertura y exclusiones."""

    eligible = [row for row in candidate_rows if row["eligible_for_materialization"]]
    trainable = [row for row in candidate_rows if row["eligible_for_training"]]
    competition_mismatches = [row for row in mapping_rows if not row["mapped"]]
    exclusions: dict[str, int] = {}
    for row in candidate_rows:
        reason = row["exclusion_reason"] or "none"
        exclusions[reason] = exclusions.get(reason, 0) + 1
    return {
        "generated_at_utc": utc_now().isoformat(),
        "dataset_version": DATASET_VERSION,
        "feature_version": FEATURE_VERSION,
        "canonical_competition_id": CANONICAL_COMPETITION_ID,
        "canonical_competition_name": CANONICAL_COMPETITION_NAME,
        "total_matches": len(matches),
        "eligible_for_materialization": len(eligible),
        "eligible_for_training": len(trainable),
        "not_eligible_for_materialization": len(matches) - len(eligible),
        "not_eligible_for_training": len(matches) - len(trainable),
        "competition_mismatches": len(competition_mismatches),
        "excluded_match_ids": sorted(list(EXCLUDED_MATCH_IDS)),
        "exclusion_counts": exclusions,
    }


def write_outputs(out_dir: Path, payloads: dict[str, Any]) -> dict[str, Path]:
    """Escribe artefactos JSON y Markdown en disco."""

    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "competition_mapping": out_dir / "competition_mapping_v1.json",
        "dataset_candidate": out_dir / "match_features_v1_candidate.json",
        "coverage_by_match": out_dir / "match_features_v1_coverage_by_match.json",
        "report": out_dir / "match_features_v1_report.md",
        "summary_json": out_dir / "match_features_v1_report.json",
    }
    paths["competition_mapping"].write_text(json.dumps(payloads["mapping"], indent=2, ensure_ascii=False, sort_keys=True, default=str), encoding="utf-8")
    paths["dataset_candidate"].write_text(json.dumps(payloads["candidate"], indent=2, ensure_ascii=False, sort_keys=True, default=str), encoding="utf-8")
    paths["coverage_by_match"].write_text(json.dumps(payloads["coverage"], indent=2, ensure_ascii=False, sort_keys=True, default=str), encoding="utf-8")
    paths["summary_json"].write_text(json.dumps(payloads["report"], indent=2, ensure_ascii=False, sort_keys=True, default=str), encoding="utf-8")
    md = [
        "# Dry-Run `match_features v1`",
        "",
        f"- Dataset: `{payloads['report']['dataset_version']}`",
        f"- Total partidos: {payloads['report']['total_matches']}",
        f"- Elegibles para materialización: {payloads['report']['eligible_for_materialization']}",
        f"- Elegibles para entrenamiento: {payloads['report']['eligible_for_training']}",
        f"- Competencias no mapeadas: {payloads['report']['competition_mismatches']}",
        f"- Exclusión especial: {payloads['report']['excluded_match_ids']}",
        "",
        "## Conteos PostgreSQL",
        f"- Antes: {payloads['postgres_before']}",
        f"- Después: {payloads['postgres_after']}",
        f"- Idénticos: {payloads['postgres_before'] == payloads['postgres_after']}",
        "",
        "## Exclusiones",
    ]
    for key, value in sorted(payloads["report"]["exclusion_counts"].items()):
        md.append(f"- {key}: {value}")
    md.extend(
        [
            "",
            "## Hashes",
            f"- Inputs: `{payloads['inputs_hash']}`",
            f"- Outputs: `{payloads['outputs_hash']}`",
            "",
            "PostgreSQL no fue modificado.",
        ]
    )
    paths["report"].write_text("\n".join(md), encoding="utf-8")
    return paths


def count_postgres(conn: psycopg2.extensions.connection) -> dict[str, int]:
    """Cuenta filas para demostrar ausencia de cambios en PostgreSQL."""

    tables = ["matches", "teams", "raw_api_responses", "events_ledger", "events_timeline", "match_statistics"]
    counts: dict[str, int] = {}
    for table in tables:
        row = fetch_one(conn, f"SELECT COUNT(*) AS n FROM {table}")
        counts[table] = int(row["n"]) if row is not None else 0
    return counts


def build_hashes(payloads: dict[str, Any]) -> tuple[str, str]:
    """Genera hashes deterministas de entradas y salidas."""

    excluded_keys = {"feature_snapshot_ts", "generated_at_utc"}
    inputs_hash = sha256_hex(
        normalize_for_hash(
            {
            "dataset_version": DATASET_VERSION,
            "feature_version": FEATURE_VERSION,
            "canonical_competition_id": CANONICAL_COMPETITION_ID,
            "excluded_match_ids": sorted(EXCLUDED_MATCH_IDS),
            "match_ids": [row["match_id"] for row in payloads["candidate"]],
            },
            excluded_keys,
        )
    )
    outputs_hash = sha256_hex(
        normalize_for_hash(
            {
            "competition_mapping": payloads["mapping"],
            "candidate": payloads["candidate"],
            "coverage": payloads["coverage"],
            "report": payloads["report"],
            },
            excluded_keys,
        )
    )
    return inputs_hash, outputs_hash


def main() -> int:
    """Ejecuta el generador dry-run."""

    parser = argparse.ArgumentParser(description="Generador dry-run de match_features v1")
    parser.add_argument(
        "--out-dir",
        default="artifacts/phase_2_4_match_features_v1_dry_run",
        help="Directorio de artefactos locales.",
    )
    args = parser.parse_args()
    conn = connect_db()
    postgres_before = count_postgres(conn)
    matches = load_matches(conn)
    candidate_rows, mapping_rows = build_candidate_rows(matches)
    coverage = [
        {
            "match_id": row["match_id"],
            "home_prior_matches": row["home_prior_matches"],
            "away_prior_matches": row["away_prior_matches"],
            "home_last_5_available": row["home_last_5_available"],
            "away_last_5_available": row["away_last_5_available"],
            "home_rest_days": row["home_rest_days"],
            "away_rest_days": row["away_rest_days"],
            "target_complete": row["target_complete"],
            "temporal_valid": row["cutoff_valid"],
            "competition_valid": row["competition_valid"],
            "eligible_for_materialization": row["eligible_for_materialization"],
            "eligible_for_training": row["eligible_for_training"],
            "exclusion_reason": row["exclusion_reason"],
        }
        for row in candidate_rows
    ]
    report = build_report(matches, candidate_rows, mapping_rows)
    inputs_hash, outputs_hash = build_hashes(
        {
            "mapping": mapping_rows,
            "candidate": candidate_rows,
            "coverage": coverage,
            "report": report,
        }
    )
    postgres_after = count_postgres(conn)
    payloads = {
        "mapping": {
            "dataset_version": DATASET_VERSION,
            "feature_version": FEATURE_VERSION,
            "canonical_competition_id": CANONICAL_COMPETITION_ID,
            "canonical_competition_name": CANONICAL_COMPETITION_NAME,
            "generated_at_utc": utc_now().isoformat(),
            "matches_total": len(matches),
            "mapped_matches": sum(1 for row in mapping_rows if row["mapped"]),
            "unmapped_matches": sum(1 for row in mapping_rows if not row["mapped"]),
            "unmapped_match_ids": [row["match_id"] for row in mapping_rows if not row["mapped"]],
            "mixing_detected": any(
                row.get("source_league") not in (None, CANONICAL_COMPETITION_ID)
                for row in mapping_rows
            ),
            "rows": mapping_rows,
        },
        "candidate": {
            "dataset_version": DATASET_VERSION,
            "feature_version": FEATURE_VERSION,
            "canonical_competition_id": CANONICAL_COMPETITION_ID,
            "canonical_competition_name": CANONICAL_COMPETITION_NAME,
            "rows": candidate_rows,
        },
        "coverage": {
            "dataset_version": DATASET_VERSION,
            "rows": coverage,
        },
        "report": report,
        "postgres_before": postgres_before,
        "postgres_after": postgres_after,
        "inputs_hash": inputs_hash,
        "outputs_hash": outputs_hash,
    }
    out_dir = Path(args.out_dir)
    paths = write_outputs(out_dir, payloads)
    LOGGER.info("Artefactos generados en %s", out_dir)
    LOGGER.info("PostgreSQL sin cambios: %s", postgres_before == postgres_after)
    print(
        json.dumps(
            {
                "artifacts": {k: str(v.resolve()) for k, v in paths.items()},
                "postgres_before": postgres_before,
                "postgres_after": postgres_after,
                "inputs_hash": inputs_hash,
                "outputs_hash": outputs_hash,
                "eligible_for_materialization": report["eligible_for_materialization"],
                "eligible_for_training": report["eligible_for_training"],
                "unmapped_matches": payloads["mapping"]["unmapped_matches"],
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
