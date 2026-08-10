"""Pruebas del servicio local DIKAMAHA v1."""

from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient

from src.dikamaha_service import SERVICE_VERSION, ServiceConfig, create_app
from src.espn_fixture_resolver import ResolvedFixture


def pre_payload(**changes: object) -> dict[str, object]:
    """Construye request pre-match valido."""

    payload: dict[str, object] = {
        "match_id": 900001, "home_team_id": 1, "away_team_id": 2,
        "kickoff_ts": "2025-01-10T20:00:00+00:00", "feature_cutoff_ts": "2025-01-10T19:59:59+00:00",
        "competition_id": "esp.1", "feature_version": "match_features_v1",
        "eligible_for_materialization": True, "history_minimum_met": True,
        "league_intercept": 0.2, "home_advantage": 0.15,
        "dc_attack_home": 0.2, "dc_defense_home": -0.1, "dc_attack_away": -0.2, "dc_defense_away": 0.1,
        "kalman_attack_home": 0.25, "kalman_defense_home": -0.08,
        "kalman_attack_away": -0.25, "kalman_defense_away": 0.08,
    }
    payload.update(changes)
    return payload


def live_payload(**changes: object) -> dict[str, object]:
    """Construye request live valido."""

    payload: dict[str, object] = {
        "match_id": 900001, "home_team_id": 1, "away_team_id": 2,
        "kickoff_ts": "2025-01-10T20:00:00+00:00", "snapshot_ts": "2025-01-10T20:10:00+00:00",
        "lambda_base_home": 1.5, "lambda_base_away": 1.1,
        "events": [{"event_id": "e1", "event_ts": "2025-01-10T20:08:00+00:00", "event_type": "shot_on_target", "team_id": 1}],
    }
    payload.update(changes)
    return payload


def test_health() -> None:
    """Expone versión y flags locales."""

    response = TestClient(create_app()).get("/v1/health")
    assert response.status_code == 200
    assert response.json()["service_version"] == SERVICE_VERSION
    assert response.json()["hawkes_enabled"] is False
    assert response.json()["hawkes_shadow_mode"] is False


def test_pre_match_and_determinism() -> None:
    """Devuelve mercados Poisson reproducibles."""

    client = TestClient(create_app())
    first = client.post("/v1/predict/pre-match", json=pre_payload())
    second = client.post("/v1/predict/pre-match", json=pre_payload())
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    data = first.json()
    assert abs(data["probability_home"] + data["probability_draw"] + data["probability_away"] - 1.0) < 1e-10


def test_upcoming_prediction_uses_compact_request_and_causal_snapshot() -> None:
    """Resuelve mercados estructurales sin exigir features manuales."""

    client = TestClient(create_app())
    payload = {"league_slug": "esp.1", "home_team_id": 94, "away_team_id": 86, "kickoff_ts": "2030-01-10T20:00:00+00:00", "match_id": 990001}
    response = client.post("/v1/predict/upcoming", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "available"
    assert data["model"] == "selective_dc_kalman_official"
    assert data["provenance"]["markov_used"] is False
    assert data["provenance"]["kalman_used"] is True
    assert data["provenance"]["dixon_coles_used"] is True
    assert data["provenance"]["market_models"]["btts"] == (
        "causal_league_btts_rate")
    assert data["provenance"]["btts_calibration"]["version"] == (
        "btts_league_rate_v1")
    assert data["provenance"]["snapshot_versioned"] is True
    assert data["audit"]["target_match_data_used"] is False
    shadow = data["experimental_team_markets"]
    assert shadow["status"] == "experimental_shadow_not_promoted"
    assert len(shadow["probabilities"]) == 8
    assert len(shadow["user_market_view"]) == 8
    assert shadow["audit"]["official_output_unchanged"] is True
    assert abs(data["probability_home"] + data["probability_draw"] + data["probability_away"] - 1.0) < 1e-10


def test_upcoming_prediction_rejects_unsupported_or_past_requests() -> None:
    """No inventa predicciones para ligas desconocidas o partidos pasados."""

    client = TestClient(create_app())
    unsupported = client.post("/v1/predict/upcoming", json={"league_slug": "unknown.1", "home_team_id": 1, "away_team_id": 2, "kickoff_ts": "2030-01-10T20:00:00+00:00"})
    past = client.post("/v1/predict/upcoming", json={"league_slug": "esp.1", "home_team_id": 94, "away_team_id": 86, "kickoff_ts": "2020-01-10T20:00:00+00:00"})
    assert unsupported.status_code == past.status_code == 422


def test_fixture_prediction_exposes_same_team_market_contract() -> None:
    """Integra el sidecar también después de resolver un fixture."""

    class Resolver:
        """Resolver local determinista sin red."""

        @staticmethod
        def resolve(_: object) -> ResolvedFixture:
            """Devuelve un fixture futuro conocido."""

            return ResolvedFixture(
                "esp.1", 990002, "990002",
                "2030-01-10T20:00:00+00:00", 94, 86,
                "Equipo local", "Equipo visitante", "pre")

    config = ServiceConfig(
        mode="operational_readonly", external_calls_enabled=True)
    response = TestClient(create_app(config, Resolver())).post(
        "/v1/predict/fixture",
        json={"league_slug": "esp.1", "kickoff_date": "20300110",
              "match_id": 990002})
    assert response.status_code == 200
    data = response.json()
    assert data["fixture"]["match_id"] == 990002
    expected = {
        "home_corners_over_4_5", "away_corners_over_4_5",
        "away_shots_over_10_5",
        "shots_on_target_total_over_7_5",
            "away_shots_second_half_over_5_5",
            "home_corners_second_half_over_2_5",
            "home_shots_first_half_over_5_5",
            "home_shots_second_half_over_5_5",
    }
    assert set(data["experimental_team_markets"]["probabilities"]) == expected


def test_pre_match_exposes_read_only_shadow_catalog() -> None:
    """Expone trazabilidad shadow sin calcular ni habilitar candidatos."""

    data = TestClient(create_app()).post("/v1/predict/pre-match", json=pre_payload()).json()
    shadow = data["shadow_catalog"]
    assert shadow["mode"] == "read_only"
    assert shadow["observation_only"] is True
    assert shadow["candidate_outputs_computed"] is False
    assert shadow["official_output_unchanged"] is True
    assert shadow["target_match_data_used"] is False
    assert all(item["enabled_by_default"] is False for item in shadow["candidates"])
    assert all(item["official_output_allowed"] is False for item in shadow["candidates"])


def test_live_hawkes_disabled_and_no_probabilities() -> None:
    """Devuelve Markov sin activar Hawkes ni mercados."""

    response = TestClient(create_app()).post("/v1/predict/live", json=live_payload())
    assert response.status_code == 200
    data = response.json()
    assert data["hawkes_applied"] is False
    assert data["experimental_hawkes"] is None
    assert not any("probability" in key for key in data)


def test_rejects_blocked_and_temporal_inputs() -> None:
    """Rechaza partido bloqueado y timestamps inválidos."""

    client = TestClient(create_app())
    assert client.post("/v1/predict/pre-match", json=pre_payload(match_id=704766)).status_code == 422
    assert client.post("/v1/predict/pre-match", json=pre_payload(feature_cutoff_ts="2025-01-10T20:00:01+00:00")).status_code == 422
    future = [{"event_id": "e2", "event_ts": "2025-01-10T20:11:00+00:00", "event_type": "goal", "team_id": 1}]
    assert client.post("/v1/predict/live", json=live_payload(events=future)).status_code == 422


def test_official_prediction_cannot_activate_hawkes() -> None:
    """Bloquea activación explícita de Hawkes en modo oficial."""

    payload = live_payload(official_prediction=True, hawkes_enabled=True, hawkes_shadow_mode=True)
    assert TestClient(create_app()).post("/v1/predict/live", json=payload).status_code == 422


def test_shadow_mode_is_explicit_and_keeps_official_markov_output() -> None:
    """Expone Hawkes en bloque experimental sin sustituir Markov."""

    client = TestClient(create_app())
    official = client.post("/v1/predict/live", json=live_payload()).json()
    shadow = client.post(
        "/v1/predict/live",
        json=live_payload(hawkes_enabled=True, hawkes_shadow_mode=True),
    ).json()
    for key in ("lambda_markov_home", "lambda_markov_away", "home_state", "away_state"):
        assert shadow[key] == official[key]
    assert shadow["official_source"] == "markov_v1"
    assert shadow["experimental_hawkes"]["stability"]["subcritical"] is True
    assert shadow["experimental_hawkes"]["provenance"]["candidate"] == "alpha_reduced"
    counters = client.get("/v1/metrics").json()["counters"]
    assert counters["hawkes_shadow_enabled"] >= 1


def test_shadow_requires_both_flags() -> None:
    """Rechaza una activación parcial del gate shadow."""

    response = TestClient(create_app()).post(
        "/v1/predict/live",
        json=live_payload(hawkes_enabled=True),
    )
    assert response.status_code == 422


def test_http_exposes_complementary_live_blocks_only_in_shadow() -> None:
    """La API conserva v1 y añade las tres capas live experimentales."""

    client = TestClient(create_app())
    payload = live_payload(
        period=1,
        match_clock_seconds=600.0,
        score_home=0,
        score_away=0,
        markov_live_enabled=True,
        markov_live_shadow_mode=True,
        hawkes_enabled=True,
        hawkes_shadow_mode=True,
    )
    response = client.post("/v1/predict/live", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["official_source"] == "markov_v1"
    assert data["experimental_hawkes"] is None
    assert data["experimental_markov_live"]["status"] == "experimental_shadow_not_promoted"
    assert data["experimental_hawkes_residual"]["status"] == "experimental_shadow_not_promoted"
    assert data["experimental_combined_live"]["status"] == "experimental_shadow_not_promoted"
    counters = client.get("/v1/metrics").json()["counters"]
    assert counters["markov_live_enabled"] == 1
    assert counters["combined_live_shadow_enabled"] == 1


def test_service_config_rejects_external_or_persistent_mode() -> None:
    """Confirma que red sólo existe en modo operativo sin persistencia."""

    from src.dikamaha_service import ServiceConfig

    try:
        ServiceConfig(external_calls_enabled=True)
    except ValueError:
        pass
    else:
        raise AssertionError("La red no debe habilitarse en modo local.")
    operational = ServiceConfig(mode="operational_readonly", external_calls_enabled=True)
    assert operational.persistence_enabled is False


def test_readiness_and_metrics_are_local() -> None:
    """Expone readiness y métricas sin depender de PostgreSQL."""

    client = TestClient(create_app())
    assert client.get("/v1/readiness").json()["ready"] is True
    assert client.post("/v1/predict/live", json=live_payload()).status_code == 200
    metrics = client.get("/v1/metrics")
    assert metrics.status_code == 200
    data = metrics.json()
    assert data["counters"]["hawkes_disabled"] >= 1
    assert "/v1/readiness" in data["requests_by_endpoint"]


def test_request_id_is_propagated_and_logs_are_metadata_only(caplog) -> None:
    """Propaga request id y no registra cuerpos completos."""

    client = TestClient(create_app())
    with caplog.at_level("INFO", logger="src.dikamaha_service"):
        response = client.get("/v1/health", headers={"X-Request-ID": "audit-123"})
    assert response.headers["X-Request-ID"] == "audit-123"
    records = [json.loads(record.message) for record in caplog.records if record.name == "src.dikamaha_service"]
    assert records[-1]["request_id"] == "audit-123"
    assert "match_id" not in records[-1]
    assert "DATABASE_URL" not in records[-1]


def test_metrics_classify_leakage_and_blocked_match() -> None:
    """Cuenta rechazos temporales y de 704766."""

    client = TestClient(create_app())
    blocked = client.post("/v1/predict/pre-match", json=pre_payload(match_id=704766))
    leaked = client.post("/v1/predict/pre-match", json=pre_payload(feature_cutoff_ts="2025-01-10T20:00:01+00:00"))
    assert blocked.status_code == leaked.status_code == 422
    counters = client.get("/v1/metrics").json()["counters"]
    assert counters["match_704766_rejections"] >= 1
    assert counters["leakage_rejections"] >= 1


def test_staging_authentication_is_configurable_and_secret_is_hidden() -> None:
    """Protege endpoints operativos sin exponer la credencial."""

    config = ServiceConfig(authentication_enabled=True, api_key="ephemeral-test-key")
    client = TestClient(create_app(config))
    assert client.get("/v1/health").status_code == 200
    assert client.get("/v1/metrics").status_code == 401
    response = client.get("/v1/metrics", headers={"X-Dikamaha-Key": "ephemeral-test-key"})
    assert response.status_code == 200
    assert "api_key" not in client.get("/v1/health").json()


def test_request_size_rate_limit_and_health_exemption() -> None:
    """Rechaza cuerpos grandes y exceso de tráfico sin degradar health."""

    config = ServiceConfig(max_request_bytes=1024, rate_limit_requests=2)
    client = TestClient(create_app(config))
    oversized = client.post(
        "/v1/predict/live",
        content=b"x" * 1025,
        headers={"Content-Type": "application/json"},
    )
    assert oversized.status_code == 413
    assert client.get("/v1/metrics").status_code == 200
    assert client.get("/v1/metrics").status_code == 200
    assert client.get("/v1/metrics").status_code == 429
    assert client.get("/v1/health").status_code == 200
    assert client.get("/v1/readiness").status_code == 200


def test_security_headers_and_restrictive_cors() -> None:
    """Aplica headers defensivos y permite solo orígenes configurados."""

    config = ServiceConfig(allowed_origins=("https://staging.local",))
    client = TestClient(create_app(config))
    allowed = client.get("/v1/health", headers={"Origin": "https://staging.local"})
    rejected = client.get("/v1/health", headers={"Origin": "https://invalid.local"})
    preflight = client.options(
        "/v1/predict/live",
        headers={"Origin": "https://staging.local", "Access-Control-Request-Method": "POST"},
    )
    assert allowed.headers["Access-Control-Allow-Origin"] == "https://staging.local"
    assert allowed.headers["X-Content-Type-Options"] == "nosniff"
    assert allowed.headers["X-Frame-Options"] == "DENY"
    assert preflight.status_code == 204
    assert preflight.headers["Access-Control-Allow-Origin"] == "https://staging.local"
    assert rejected.status_code == 403


def test_hardening_does_not_change_math_or_hawkes_policy() -> None:
    """Mantiene inferencia determinista y Hawkes fuera de salida oficial."""

    headers = {"X-Dikamaha-Key": "ephemeral-test-key"}
    config = ServiceConfig(authentication_enabled=True, api_key="ephemeral-test-key")
    client = TestClient(create_app(config))
    first = client.post("/v1/predict/pre-match", json=pre_payload(), headers=headers)
    second = client.post("/v1/predict/pre-match", json=pre_payload(), headers=headers)
    assert first.json() == second.json()
    official = client.post("/v1/predict/live", json=live_payload(), headers=headers).json()
    shadow = client.post(
        "/v1/predict/live",
        json=live_payload(hawkes_enabled=True, hawkes_shadow_mode=True),
        headers=headers,
    ).json()
    assert official["lambda_markov_home"] == shadow["lambda_markov_home"]
    assert official["lambda_markov_away"] == shadow["lambda_markov_away"]
    assert official["experimental_hawkes"] is None
    assert shadow["official_source"] == "markov_v1"


def test_inference_timeout_is_controlled(monkeypatch) -> None:
    """Convierte una inferencia saturada en un error 504 explícito."""

    app = create_app(ServiceConfig(inference_timeout_seconds=0.01))

    def slow_prediction(_: object) -> None:
        time.sleep(0.05)
        return None

    monkeypatch.setattr(app.state.inference_engine, "predict_pre_match", slow_prediction)
    response = TestClient(app).post("/v1/predict/pre-match", json=pre_payload())
    assert response.status_code == 504
    assert response.json()["detail"]["code"] == "inference_timeout"


class _FakeLiveRuntime:
    """Runtime determinista para contratos HTTP live sin red."""

    policy = {
        "version": "hawkes_live_v2_league_admission_v1",
        "allowed_leagues": ["esp.1"],
        "rho_goal": 1.0,
        "rho_next_event": 0.0,
    }

    def list_active(
        self, leagues: str, limit: int, selected_date: str | None,
    ) -> dict[str, object]:
        return {
            "fixtures": [{
                "league_slug": "esp.1", "match_id": 900001,
                "competition_id": "900001", "home_team_id": 1,
                "away_team_id": 2, "home_team_name": "Equipo A",
                "away_team_name": "Equipo B",
                "kickoff_ts": "2026-08-08T20:00:00+00:00",
                "provider_status": "in", "home_score": 1,
                "away_score": 0, "display_clock": "32'",
            }],
            "count": 1, "date": selected_date or "20260808",
            "league_count": len(leagues.split(",")),
            "partial_failure_count": 0,
            "status": "live_shadow_catalog",
        }

    def predict_fixture(
        self, league: str, match_id: int, selected_date: str | None,
    ) -> dict[str, object]:
        return {
            "status": "shadow_predicted",
            "fixture": {"league_slug": league, "match_id": match_id},
            "experimental_markov_live": {
                "status": "experimental_shadow_not_promoted"},
            "experimental_hawkes_residual": {
                "status": "experimental_shadow_not_promoted"},
            "experimental_combined_live": {
                "status": "experimental_shadow_not_promoted"},
        }


def test_live_catalog_prediction_and_model_inventory_are_exposed() -> None:
    """Publica modelos live sólo por la API central y conserva shadow."""

    app = create_app(ServiceConfig(
        mode="operational_readonly", external_calls_enabled=True,
    ), live_runtime=_FakeLiveRuntime())
    client = TestClient(app)

    catalog = client.get("/v1/live", params={"leagues": "esp.1"})
    prediction = client.post("/v1/predict/live/fixture", json={
        "league_slug": "esp.1", "match_id": 900001,
    })
    models = client.get("/v1/models")

    assert catalog.status_code == 200
    assert catalog.json()["fixtures"][0]["display_clock"] == "32'"
    assert prediction.status_code == 200
    assert prediction.json()["experimental_markov_live"]["status"] == (
        "experimental_shadow_not_promoted")
    assert prediction.json()["experimental_hawkes_residual"]["status"] == (
        "experimental_shadow_not_promoted")
    assert models.status_code == 200
    assert {row["name"] for row in models.json()["models"]} >= {
        "Markov Live v1", "Hawkes Live v2 residual",
        "Markov + Hawkes combinado",
    }
    counters = client.get("/v1/metrics").json()["counters"]
    assert counters["live_responses"] == 1
    assert counters["pre_match_responses"] == 0


def test_provider_predictor_endpoint_is_external_benchmark_only() -> None:
    """Expone el contrato inyectado sin modificar las predicciones propias."""

    class ProviderContext:
        @staticmethod
        def fetch(league: str, event_id: str, scope: str) -> dict[str, object]:
            return {
                "contract_version": "provider_match_context_v1",
                "league_slug": league, "event_id": event_id, "scope": scope,
                "status": "available",
                "probabilities": {"home": .5, "draw": .3, "away": .2},
                "role": "external_benchmark_display_only",
                "not_model_feature": True,
                "replaces_dikamaha_models": False,
            }

    app = create_app(
        ServiceConfig(mode="operational_readonly", external_calls_enabled=True),
        provider_context=ProviderContext(),
    )
    response = TestClient(app).get("/v1/provider/predictor", params={
        "league": "esp.1", "event_id": "401000001", "scope": "live",
    })

    assert response.status_code == 200
    assert response.json()["probabilities"] == {"home": .5, "draw": .3, "away": .2}
    assert response.json()["replaces_dikamaha_models"] is False


def test_provider_predictor_endpoint_remains_authenticated() -> None:
    """No abre el benchmark fuera del perímetro de la API central."""

    config = ServiceConfig(
        mode="operational_readonly", external_calls_enabled=True,
        authentication_enabled=True, api_key="test-provider-key",
    )
    client = TestClient(create_app(config, provider_context=object()))

    assert client.get("/v1/provider/predictor", params={
        "league": "esp.1", "event_id": "401000001",
    }).status_code == 401


def test_provider_market_endpoint_is_financially_isolated() -> None:
    class ProviderContext:
        @staticmethod
        def markets(league: str, date: str) -> dict[str, object]:
            return {
                "contract_version": "provider_market_tape_v1",
                "league_slug": league, "date": date, "count": 1,
                "role": "financial_isolated_display_only",
                "not_model_feature": True,
            }

    app = create_app(
        ServiceConfig(mode="operational_readonly", external_calls_enabled=True),
        provider_context=ProviderContext(),
    )
    response = TestClient(app).get("/v1/provider/markets", params={
        "league": "col.1", "date": "20260810",
    })

    assert response.status_code == 200
    assert response.json()["contract_version"] == "provider_market_tape_v1"
    assert response.json()["not_model_feature"] is True


# Version: 1.0.0
# Created: 2026-07-15
