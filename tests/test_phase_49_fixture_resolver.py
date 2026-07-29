"""Pruebas del contrato de refresco de la Fase 49 sin llamadas externas."""

from __future__ import annotations

from argparse import Namespace
import json

from scripts import run_phase_49_snapshot_refresh as phase49


def _args() -> Namespace:
    """Construye argumentos mínimos para un refresco dry-run."""

    return Namespace(
        league="esp.1",
        start_date="20300101",
        end_date="20300101",
        days_back=7,
        write_staging=False,
        sleep_between_requests=0.0,
        stop_on_error=False,
    )


def test_refresh_wrapper_is_dry_run_and_does_not_replace_snapshot(monkeypatch, tmp_path) -> None:
    """El wrapper conserva staging y snapshot como operaciones separadas."""

    result = {"gates": {"source_fetch_ok": True, "staging_write_ok": False}, "event_results": [{"match_id": "1"}]}
    monkeypatch.setattr(phase49, "run_range", lambda *args, **kwargs: result)
    monkeypatch.setattr(phase49, "OUTPUT", tmp_path)

    outcome = phase49.run(_args())
    config = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert outcome["classification"] == "refresh_staging_verified"
    assert config["dry_run"] is True
    assert config["canonical_snapshot_replaced"] is False
    assert (tmp_path / "hashes.json").exists()


def test_dates_require_a_complete_explicit_pair() -> None:
    """No se permite una ventana incompleta que pueda ocultar el rango real."""

    args = _args()
    args.end_date = None
    try:
        phase49._dates(args)
    except ValueError as error:
        assert str(error) == "start_date_and_end_date_must_be_provided_together"
    else:
        raise AssertionError("Se esperaba rechazo de fechas incompletas")

