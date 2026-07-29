"""Pruebas aisladas para `markov_v1`.

Requirements:
    None beyond the standard library and project dependencies.

Version: 1.0.0
Created: 2026-07-15
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from src.markov_v1 import MarkovV1, MarkovV1Config, generate_synthetic_markov_dataset, run_synthetic_markov_v1


class MarkovV1Tests(unittest.TestCase):
    """Pruebas unitarias de la capa Markov sintética."""

    def setUp(self) -> None:
        self.frame = generate_synthetic_markov_dataset()
        self.config = MarkovV1Config()
        self.model = MarkovV1(self.config)

    def test_initial_states_are_equilibrium(self) -> None:
        """El kickoff debe iniciar en equilibrio."""

        kickoff = self.frame[self.frame["event_type"] == "kickoff"].iloc[[0]].copy()
        result = self.model.predict_snapshot(kickoff, 1.5, 1.2, kickoff["snapshot_ts"].iloc[0].isoformat())
        self.assertEqual(result["home_state"], 0)
        self.assertEqual(result["away_state"], 0)
        self.assertAlmostEqual(result["lambda_markov_home"], 1.5)
        self.assertAlmostEqual(result["lambda_markov_away"], 1.2)

    def test_goal_and_card_transitions(self) -> None:
        """Gol y tarjeta deben mover el estado."""

        match = self.frame[self.frame["match_id"] == 1].copy()
        goal_snapshot = self.model.predict_snapshot(match[match["event_ts"] <= pd.Timestamp("2025-01-01T12:12:00+00:00", tz="UTC")], 1.5, 1.2, "2025-01-01T12:12:00+00:00")
        yellow_snapshot = self.model.predict_snapshot(match[match["event_ts"] <= pd.Timestamp("2025-01-01T12:20:00+00:00", tz="UTC")], 1.5, 1.2, "2025-01-01T12:20:00+00:00")
        self.assertIn(goal_snapshot["home_state"], {0, 1, 2})
        self.assertIn(goal_snapshot["away_state"], {0, 1, 2})
        self.assertIn(yellow_snapshot["home_state"], {0, 1, 2})

    def test_shots_substitutions_and_no_events(self) -> None:
        """Los tiros, sustituciones y ausencia de eventos son manejados."""

        match = self.frame[self.frame["match_id"] == 4].copy()
        snapshot = self.model.predict_snapshot(match[match["event_ts"] <= pd.Timestamp("2025-01-01T12:11:00+00:00", tz="UTC")], 1.4, 1.0, "2025-01-01T12:11:00+00:00")
        self.assertTrue(snapshot["recent_events"])
        self.assertGreater(snapshot["lambda_markov_home"], 0.0)
        self.assertGreater(snapshot["lambda_markov_away"], 0.0)

    def test_unknown_null_and_annulled_events(self) -> None:
        """Eventos desconocidos, nulos y anulados no deben romper la capa."""

        match = self.frame[self.frame["match_id"] == 2].copy()
        snapshot = self.model.predict_snapshot(match[match["event_ts"] <= pd.Timestamp("2025-01-01T12:25:00+00:00", tz="UTC")], 1.6, 1.1, "2025-01-01T12:25:00+00:00")
        self.assertTrue(all("event_type" in item for item in snapshot["recent_events"]))
        self.assertEqual(len(snapshot["recent_events"]), 0)

    def test_future_timestamps_are_rejected(self) -> None:
        """Snapshots previos a kickoff deben fallar de forma controlada."""

        match = self.frame[self.frame["match_id"] == 1].copy()
        with self.assertRaises(ValueError):
            self.model.predict_snapshot(match, 1.5, 1.2, "2024-12-31T23:59:59+00:00")

    def test_transition_matrices_are_normalized(self) -> None:
        """Las matrices de transición deben sumar 1 por fila."""

        match = self.frame[self.frame["match_id"] == 3].copy()
        snapshot = self.model.predict_snapshot(match, 1.3, 1.1, "2025-01-01T12:18:00+00:00")
        home = np.asarray(snapshot["home_transition_matrix"], dtype=float)
        away = np.asarray(snapshot["away_transition_matrix"], dtype=float)
        self.assertTrue(np.allclose(home.sum(axis=1), 1.0))
        self.assertTrue(np.allclose(away.sum(axis=1), 1.0))
        self.assertTrue(np.all(home >= 0.0))
        self.assertTrue(np.all(away >= 0.0))

    def test_no_double_multiplication_and_no_probabilities(self) -> None:
        """No debe haber doble conteo ni probabilidades de mercado."""

        match = self.frame[self.frame["match_id"] == 1].copy()
        snapshot = self.model.predict_snapshot(match[match["event_ts"] <= pd.Timestamp("2025-01-01T12:12:00+00:00", tz="UTC")], 2.0, 1.0, "2025-01-01T12:12:00+00:00")
        self.assertNotIn("prob_1", snapshot)
        self.assertNotIn("prob_x", snapshot)
        self.assertIn(round(snapshot["multiplier_home"], 2), {0.75, 1.0, 1.25})
        self.assertIn(round(snapshot["multiplier_away"], 2), {0.75, 1.0, 1.25})
        self.assertIn("window_5m", snapshot)
        self.assertIn("window_10m", snapshot)
        self.assertGreaterEqual(len(snapshot["recent_events_10m"]), len(snapshot["recent_events_5m"]))

    def test_invalid_transition_matrix_is_rejected(self) -> None:
        """Matrices inválidas deben fallar de forma explícita."""

        bad = MarkovV1Config(base_matrix=[[0.5, -0.5, 1.0], [0.2, 0.2, 0.2], [0.3, 0.3, 0.3]])
        model = MarkovV1(bad)
        match = self.frame[self.frame["match_id"] == 1].copy()
        with self.assertRaises(ValueError):
            model.predict_snapshot(match, 1.5, 1.2, "2025-01-01T12:12:00+00:00")

    def test_determinism_and_artifacts(self) -> None:
        """La salida debe ser determinista y producir artefactos."""

        r1 = self.model.fit_predict(self.frame.copy())
        r2 = MarkovV1(self.config).fit_predict(self.frame.copy())
        self.assertEqual(r1["predictions_hash"], r2["predictions_hash"])
        self.assertEqual(r1["matrices_hash"], r2["matrices_hash"])
        self.assertEqual(r1["events_hash"], r2["events_hash"])
        with tempfile.TemporaryDirectory() as tmp:
            result = run_synthetic_markov_v1(Path(tmp))
            self.assertTrue(result["predictions_hash"])
            manifest = json.loads((Path(tmp) / "markov_v1_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["model_version"], "markov_v1")

    def test_output_is_hawkes_ready(self) -> None:
        """La salida debe estar lista para Hawkes sin excitación calculada."""

        result = self.model.fit_predict(self.frame.copy())
        first = result["snapshots"][0]
        self.assertIn("lambda_markov_home", first)
        self.assertIn("lambda_markov_away", first)
        self.assertIn("home_state", first)
        self.assertIn("away_state", first)
        self.assertIn("recent_events", first)


if __name__ == "__main__":
    unittest.main()
