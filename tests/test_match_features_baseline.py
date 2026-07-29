"""Pruebas de baseline para `match_features v1`.

Estas pruebas validan contrato, anti-leakage, reproducibilidad y regresión
del generador dry-run aprobado sin escribir en PostgreSQL.

Requirements:
    None beyond the standard library.

Version: 1.0.0
Created: 2026-07-15
"""

from __future__ import annotations

import json
import unittest
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
BASE_DIR = ROOT / "artifacts" / "phase_2_4_match_features_v1_dry_run"
BASELINE_DIR = ROOT / "artifacts" / "phase_2_5_match_features_v1_baseline"
pytestmark = pytest.mark.historical


def read_json(path: Path) -> Any:
    """Carga un JSON local."""

    return json.loads(path.read_text(encoding="utf-8"))


def parse_dt(value: str) -> datetime:
    """Parses an ISO-like timestamp or date string."""

    return datetime.fromisoformat(value)


def normalize_rows(rows: list[dict[str, Any]], excluded_keys: set[str]) -> list[dict[str, Any]]:
    """Elimina claves volátiles de una lista de filas."""

    return [{key: value for key, value in row.items() if key not in excluded_keys} for row in rows]


class MatchFeaturesBaselineTests(unittest.TestCase):
    """Pruebas de contrato y regresión del baseline."""

    @classmethod
    def setUpClass(cls) -> None:
        """Carga artefactos congelados sin regenerar el dataset histórico."""

        cls.artifact_snapshot = read_json(BASE_DIR / "match_features_v1_candidate.json")
        cls.coverage_snapshot = read_json(BASE_DIR / "match_features_v1_coverage_by_match.json")
        cls.report_snapshot = read_json(BASE_DIR / "match_features_v1_report.json")
        cls.mapping_snapshot = read_json(BASE_DIR / "competition_mapping_v1.json")
        cls.candidate = read_json(BASELINE_DIR / "match_features_v1_candidate.json")
        cls.coverage = read_json(BASELINE_DIR / "match_features_v1_coverage_by_match.json")
        cls.report = read_json(BASELINE_DIR / "match_features_v1_report.json")
        cls.mapping = read_json(BASELINE_DIR / "competition_mapping_v1.json")
        cls.manifest = read_json(BASELINE_DIR / "match_features_v1_baseline_manifest.json")
        cls.test_results = read_json(BASELINE_DIR / "test_results.json")

    def test_contract_counts(self) -> None:
        """Verifica conteos y unicidad del contrato."""

        rows = self.candidate["rows"]
        self.assertEqual(len(rows), 381)
        self.assertEqual(len({row["match_id"] for row in rows}), 381)
        self.assertEqual(self.report["eligible_for_materialization"], 371)
        self.assertEqual(self.report["eligible_for_training"], 331)
        self.assertEqual(self.report["exclusion_counts"]["insufficient_history_for_materialization"], 10)
        self.assertEqual(self.report["exclusion_counts"]["insufficient_history_for_training"], 40)
        self.assertIn(704766, self.report["excluded_match_ids"])
        self.assertEqual(self.report["competition_mismatches"], 0)
        self.assertEqual(self.mapping["unmapped_matches"], 0)
        self.assertEqual(self.report["total_matches"], 381)
        self.assertEqual(self.report["not_eligible_for_materialization"], 10)
        self.assertEqual(self.report["not_eligible_for_training"], 50)
        self.assertEqual(self.mapping["mapped_matches"], 381)
        self.assertEqual(self.report["canonical_competition_id"], "esp.1")

    def test_flag_names_match_contract(self) -> None:
        """Comprueba que los flags existen con nombres alineados al contrato."""

        required = {
            "eligible_for_materialization",
            "eligible_for_training",
            "history_minimum_met",
            "last_5_complete_home",
            "last_5_complete_away",
            "cutoff_valid",
            "competition_valid",
            "target_complete",
            "exclusion_reason",
        }
        row_keys = set(self.candidate["rows"][0].keys())
        self.assertTrue(required.issubset(row_keys))
        self.assertNotIn("last_5_complete_flag", row_keys)
        self.assertNotIn("home_last_5_complete", row_keys)
        self.assertNotIn("away_last_5_complete", row_keys)

    def test_targets_are_consistent(self) -> None:
        """Verifica targets y su derivación desde el marcador final."""

        allowed = {
            "home_goals",
            "away_goals",
            "result_1x2",
            "over_2_5",
            "btts",
            "goal_margin",
            "total_goals",
        }
        row = self.candidate["rows"][0]
        feature_like = {key for key in row if key in allowed}
        self.assertSetEqual(feature_like, allowed)
        for item in self.candidate["rows"]:
            self.assertTrue(item["target_complete"])
            self.assertEqual(item["goal_margin"], item["home_goals"] - item["away_goals"])
            self.assertEqual(item["total_goals"], item["home_goals"] + item["away_goals"])
            expected_1x2 = "1" if item["home_goals"] > item["away_goals"] else "2" if item["home_goals"] < item["away_goals"] else "X"
            self.assertEqual(item["result_1x2"], expected_1x2)
            self.assertEqual(item["over_2_5"], (item["home_goals"] + item["away_goals"]) > 2)
            self.assertEqual(item["btts"], item["home_goals"] > 0 and item["away_goals"] > 0)
            for blocked in ["match_statistics", "events_timeline", "Markov", "Hawkes", "clima", "viaje", "Elo", "corners", "tarjetas", "odds", "player_props"]:
                self.assertNotIn(blocked, item)

    def test_competition_mapping(self) -> None:
        """Valida mapping canónico de competencia."""

        rows = self.mapping["rows"]
        self.assertEqual(len(rows), 381)
        self.assertTrue(all(row["competition_id"] == "esp.1" for row in rows))
        self.assertTrue(all(row["source_league"] in (None, "esp.1") for row in rows))
        self.assertFalse(self.mapping["mixing_detected"])
        self.assertTrue(all(row["reason"] is None for row in rows))

    def test_anti_leakage_rules(self) -> None:
        """Valida reglas anti-leakage y ventanas temporales."""

        rows = self.candidate["rows"]
        for row in rows:
            self.assertTrue(row["cutoff_valid"])
            self.assertLessEqual(parse_dt(row["feature_cutoff_ts"]), parse_dt(row["match_date"].replace(" ", "T")))
            self.assertTrue(row["competition_valid"])
            if row["home_prior_matches"] >= 1:
                self.assertIsNotNone(row["home_rest_days"])
            if row["away_prior_matches"] >= 1:
                self.assertIsNotNone(row["away_rest_days"])
            if row["home_prior_matches"] < 1 or row["away_prior_matches"] < 1:
                self.assertFalse(row["eligible_for_materialization"])
            self.assertLessEqual(row["home_prior_matches"], 380)
            self.assertLessEqual(row["away_prior_matches"], 380)
            self.assertEqual(row["feature_version"], "v1")
            self.assertEqual(row["source_system"], "espn")

    def test_windows_and_nulls(self) -> None:
        """Verifica ventanas parciales, NULLs y ausencia de imputaciones inválidas."""

        first_rows = [row for row in self.candidate["rows"] if row["home_prior_matches"] == 0 and row["away_prior_matches"] == 0]
        self.assertGreater(len(first_rows), 0)
        for row in first_rows[:5]:
            self.assertFalse(row["eligible_for_materialization"])
            self.assertIsNone(row["home_rest_days"])
            self.assertIsNone(row["away_rest_days"])
            self.assertIsNone(row["home_last_5_points"])
            self.assertIsNone(row["away_last_5_points"])
            self.assertEqual(row["home_last_5_available"], 0)
            self.assertEqual(row["away_last_5_available"], 0)
            self.assertFalse(row["last_5_complete_home"])
            self.assertFalse(row["last_5_complete_away"])

    def test_reproducibility(self) -> None:
        """Valida hashes y equivalencia con el dry-run congelado."""

        self.assertTrue(self.test_results["reproducible"])
        self.assertEqual(self.manifest["hashes"]["inputs"], self.test_results["inputs_hash"])
        self.assertEqual(self.manifest["hashes"]["outputs"], self.test_results["outputs_hash"])
        self.assertEqual([row["match_id"] for row in self.candidate["rows"]], [row["match_id"] for row in self.candidate["rows"]])
        self.assertEqual(
            normalize_rows(self.candidate["rows"], {"feature_snapshot_ts"}),
            normalize_rows(self.artifact_snapshot["rows"], {"feature_snapshot_ts"}),
        )
        self.assertEqual(
            normalize_rows(self.mapping["rows"], {"generated_at_utc"}),
            normalize_rows(self.mapping_snapshot["rows"], {"generated_at_utc"}),
        )
        self.assertEqual(self.coverage["rows"], self.coverage_snapshot["rows"])
        self.assertEqual(
            {k: v for k, v in self.report.items() if k != "generated_at_utc"},
            {k: v for k, v in self.report_snapshot.items() if k != "generated_at_utc"},
        )

    def test_regression_manifest(self) -> None:
        """Valida el manifiesto existente sin reescribir artefactos históricos."""

        baseline_manifest = {
            "dataset_version": self.manifest["dataset_version"],
            "feature_version": self.manifest["feature_version"],
            "canonical_competition_id": self.manifest["canonical_competition_id"],
            "counts": {
                "rows": len(self.candidate["rows"]),
                "materializable": self.report["eligible_for_materialization"],
                "trainable": self.report["eligible_for_training"],
            },
            "hashes": {
                "inputs": self.manifest["hashes"]["inputs"],
                "outputs": self.manifest["hashes"]["outputs"],
            },
            "schema_keys": sorted(self.candidate["rows"][0].keys()),
            "blocked_features_absent": all(
                blocked not in self.candidate["rows"][0]
                for blocked in [
                    "Markov",
                    "Hawkes",
                    "clima",
                    "viaje",
                    "Elo",
                    "corners",
                    "tarjetas",
                    "odds",
                    "player_props",
                ]
            ),
        }
        loaded = self.manifest
        self.assertEqual(loaded, baseline_manifest)
        self.assertEqual(loaded["canonical_competition_id"], "esp.1")
        self.assertEqual(loaded["counts"]["rows"], 381)
        self.assertEqual(loaded["counts"]["materializable"], 371)
        self.assertEqual(loaded["counts"]["trainable"], 331)
        self.assertTrue(loaded["blocked_features_absent"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
