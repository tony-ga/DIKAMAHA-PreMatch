"""Pruebas del runtime shadow de mercados agregados."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from src.team_count_market_runtime import (
    APPROVED_MARKETS,
    DEFAULT_ARTIFACT,
    DEFAULT_MARKOV_ARTIFACT,
    MARKOV_APPROVED_MARKETS,
    MARKOV_BASELINE_FALLBACKS,
    ArtifactTeamCountMarketProvider,
    _verify_hash_manifest,
)
from src.universal_prematch import (
    UniversalPrematchEngine,
    UpcomingMatchInput,
    _load_windows,
)


def _request(match_id: int = 990001) -> UpcomingMatchInput:
    """Construye una solicitud futura reproducible."""

    return UpcomingMatchInput(
        league_slug="esp.1", home_team_id=94, away_team_id=86,
        kickoff_ts="2030-01-10T20:00:00+00:00", match_id=match_id)


def test_runtime_exposes_only_approved_markets() -> None:
    """Publica exactamente las ocho líneas shadow revalidadas."""

    prediction = UniversalPrematchEngine().predict(_request())
    shadow = prediction.experimental_team_markets
    assert shadow is not None
    assert shadow["status"] == "experimental_shadow_not_promoted"
    approved = APPROVED_MARKETS | MARKOV_APPROVED_MARKETS
    assert set(shadow["enabled_markets"]) == approved
    assert set(shadow["probabilities"]) == approved
    assert set(shadow["baseline_probabilities"]) == approved
    assert all(0.0 <= value <= 1.0
               for value in shadow["probabilities"].values())
    assert len(shadow["user_market_view"]) == len(approved)
    assert {
        row["key"] for row in shadow["user_market_view"]} == approved
    assert all(
        row["probability"] == shadow["probabilities"][row["key"]]
        for row in shadow["user_market_view"])
    for name in MARKOV_BASELINE_FALLBACKS:
        assert shadow["probabilities"][name] == (
            shadow["baseline_probabilities"][name])
        row = next(
            item for item in shadow["user_market_view"]
            if item["key"] == name)
        assert row["source_model"] == "phase88_league_venue_fallback"
    ladders = shadow["distributional_market_view"]
    assert len(ladders) == 21
    assert sum(
        row["metric"] == "shots_on_target" for row in ladders) == 3
    assert shadow["recommended_market_view"]
    assert len(shadow["recommended_market_view"]) <= 6
    grids = shadow["bounded_market_grid_view"]
    assert len(grids) == 21
    assert all(len(row["lines"]) == 3 for row in grids)
    assert all(
        1.5 <= line["line"] <= 9.5
        for row in grids for line in row["lines"])
    assert {
        row["period"] for row in grids
    } == {"first_half", "second_half", "full_match"}
    assert all(
        line["over_probability"] + line["under_probability"]
        == pytest.approx(1.0)
        for row in grids for line in row["lines"])
    totals = shadow["global_market_view"]
    assert {row["metric"] for row in totals} == {
        "corners", "shots", "yellow_cards", "shots_on_target"}
    assert all(row["team_side"] == "total" for row in totals)
    assert all(sum(item["probability"] for item in row["probability_mass"])
               == pytest.approx(1.0) for row in totals)
    assert all(row["expected_count"] == pytest.approx(
        sum(item["count"] * item["probability"]
            for item in row["probability_mass"])) for row in totals)
    assert shadow["audit"]["over_under_monotonic"] is True
    for row in ladders:
        assert sum(
            item["probability"] for item in row["probability_mass"]
        ) == pytest.approx(1.0)
        overs = [item["over_probability"] for item in row["ladder"]]
        assert overs == sorted(overs, reverse=True)
        assert all(
            item["over_probability"] + item["under_probability"]
            == pytest.approx(1.0) for item in row["ladder"])


def test_shadow_does_not_change_official_fields() -> None:
    """Conserva bit a bit todos los campos oficiales."""

    enabled = asdict(UniversalPrematchEngine().predict(_request()))
    disabled = asdict(UniversalPrematchEngine(
        team_markets_enabled=False).predict(_request()))
    enabled.pop("experimental_team_markets")
    disabled.pop("experimental_team_markets")
    assert enabled == disabled


def test_target_match_rows_are_explicitly_excluded() -> None:
    """La presencia del partido objetivo no cambia el sidecar."""

    engine = UniversalPrematchEngine()
    rows = _load_windows(str(engine._windows_path))
    target_id = next(
        int(row["match_id"]) for row in rows
        if str(row["league_slug"]) == "esp.1")
    request = _request(target_id)
    provider = ArtifactTeamCountMarketProvider()
    with_target = provider.predict(rows, request, engine._windows_path)
    without = tuple(row for row in rows if int(row["match_id"]) != target_id)
    excluded = provider.predict(without, request, engine._windows_path)
    assert with_target["probabilities"] == excluded["probabilities"]
    assert with_target["audit"]["target_match_excluded"] is True


def test_missing_artifact_degrades_safely(tmp_path: Path) -> None:
    """Un sidecar ausente no inventa probabilidades ni lanza error."""

    engine = UniversalPrematchEngine()
    rows = _load_windows(str(engine._windows_path))
    provider = ArtifactTeamCountMarketProvider(tmp_path / "missing")
    result = provider.predict(rows, _request(), engine._windows_path)
    assert result["status"] == "shadow_unavailable"
    assert result["probabilities"] == {}
    assert result["audit"]["official_output_unchanged"] is True


def test_missing_markov_preserves_phase84a_markets(tmp_path: Path) -> None:
    """Conserva las líneas agregadas si falla sólo Markov."""

    engine = UniversalPrematchEngine()
    rows = _load_windows(str(engine._windows_path))
    provider = ArtifactTeamCountMarketProvider(
        markov_artifact_path=tmp_path / "missing")
    result = provider.predict(rows, _request(), engine._windows_path)
    assert set(result["probabilities"]) == APPROVED_MARKETS
    assert len(result["user_market_view"]) == len(APPROVED_MARKETS)
    assert result["provenance"]["team_market_markov"]["status"] == (
        "shadow_unavailable")


def test_markov_rejects_kickoff_before_training_cutoff() -> None:
    """Impide usar historia posterior al kickoff solicitado."""

    engine = UniversalPrematchEngine()
    rows = _load_windows(str(engine._windows_path))
    request = UpcomingMatchInput(
        league_slug="esp.1", home_team_id=94, away_team_id=86,
        kickoff_ts="2026-01-10T20:00:00+00:00", match_id=990003)
    result = ArtifactTeamCountMarketProvider().predict(
        rows, request, engine._windows_path)
    assert set(result["probabilities"]) == APPROVED_MARKETS
    assert result["provenance"]["team_market_markov"]["status"] == (
        "shadow_unavailable")


def test_count_artifact_rejects_tampered_config_even_with_updated_hash(
    tmp_path: Path,
) -> None:
    """El hash no sustituye la validación semántica de configuración."""

    artifact = tmp_path / "phase84"
    shutil.copytree(DEFAULT_ARTIFACT, artifact)
    config_path = artifact / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["dispersions"]["corners"] = float("nan")
    config_path.write_text(json.dumps(config), encoding="utf-8")
    hashes_path = artifact / "hashes.json"
    hashes = json.loads(hashes_path.read_text(encoding="utf-8"))
    hashes["config.json"] = hashlib.sha256(config_path.read_bytes()).hexdigest()
    hashes_path.write_text(json.dumps(hashes), encoding="utf-8")
    with pytest.raises(ValueError, match="dispersions_nonfinite"):
        ArtifactTeamCountMarketProvider(artifact)._load()


def test_markov_artifact_rejects_tampered_config_even_with_updated_hash(
    tmp_path: Path,
) -> None:
    """La versión Markov forma parte del contrato servible."""

    artifact = tmp_path / "phase88"
    shutil.copytree(DEFAULT_MARKOV_ARTIFACT, artifact)
    config_path = artifact / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["version"] = "tampered"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    hashes_path = artifact / "hashes.json"
    hashes = json.loads(hashes_path.read_text(encoding="utf-8"))
    hashes["config.json"] = hashlib.sha256(config_path.read_bytes()).hexdigest()
    hashes_path.write_text(json.dumps(hashes), encoding="utf-8")
    with pytest.raises(ValueError, match="version_mismatch"):
        ArtifactTeamCountMarketProvider(
            markov_artifact_path=artifact)._load_markov()


def test_runtime_manifest_is_portable_and_scoped_to_required_files(
    tmp_path: Path,
) -> None:
    """No exige evidencia no empaquetada y acepta LF frente al hash CRLF."""

    runtime_lf = b'{\n  "status": "valid"\n}\n'
    (tmp_path / "runtime.json").write_bytes(runtime_lf)
    runtime_crlf = runtime_lf.replace(b"\n", b"\r\n")
    hashes = {
        "runtime.json": hashlib.sha256(runtime_crlf).hexdigest(),
        "evidence_not_packaged.json": "0" * 64,
    }

    _verify_hash_manifest(tmp_path, hashes, {"runtime.json"})


def test_cambridge_barnet_exposes_probabilities_for_every_period() -> None:
    """Congela la regresión observada en el primer fixture de producción."""

    request = UpcomingMatchInput(
        league_slug="eng.league_cup",
        home_team_id=351,
        away_team_id=280,
        kickoff_ts="2026-08-08T12:00:00+00:00",
        match_id=401880614,
    )

    shadow = UniversalPrematchEngine().predict(
        request).experimental_team_markets

    assert shadow is not None
    assert shadow["status"] == "experimental_shadow_not_promoted"
    assert len(shadow["user_market_view"]) == 8
    assert len(shadow["bounded_market_grid_view"]) == 21
    assert {
        row["period"] for row in shadow["bounded_market_grid_view"]
    } == {"first_half", "second_half", "full_match"}


# Version: 1.0.0
# Created: 2026-07-28
