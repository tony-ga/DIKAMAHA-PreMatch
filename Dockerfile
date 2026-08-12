# Imagen fijada explícitamente para el empaquetado local.
FROM python:3.12.3-slim-bookworm@sha256:fd3817f3a855f6c2ada16ac9468e5ee93e361005bd226fd5a5ee1a504e038c84

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src:/app \
    HAWKES_ENABLED=false \
    OFFICIAL_PREDICTION=false \
    EXTERNAL_CALLS_ENABLED=false \
    PERSISTENCE_ENABLED=false \
    DIKAMAHA_AUTH_ENABLED=true \
    DIKAMAHA_MAX_REQUEST_BYTES=65536 \
    DIKAMAHA_RATE_LIMIT_REQUESTS=600 \
    DIKAMAHA_RATE_LIMIT_WINDOW_SECONDS=60 \
    DIKAMAHA_INFERENCE_TIMEOUT_SECONDS=10 \
    DIKAMAHA_MAX_CONCURRENT_REQUESTS=16 \
    DIKAMAHA_ALLOWED_ORIGINS= \
    DIKAMAHA_BIND_HOST=0.0.0.0 \
    # `/app/data` pertenece al usuario `app` y no es punto de montaje, de modo
    # que siempre es escribible. `/data` sí lo es: al montar ahí un volumen
    # Railway, el punto de montaje llega propiedad de root, el usuario `app` no
    # puede crear el fichero y el worker moría arrastrando a la API entera.
    # Persistir este ledger exige un volumen cuya propiedad se ceda a `app`, o
    # migrarlo a PostgreSQL; hasta entonces es efímero por diseño explícito.
    TELEGRAM_CHANNEL_LEDGER_PATH=/app/data/telegram_channel.sqlite

WORKDIR /app

COPY requirements.docker.txt /app/requirements.docker.txt
RUN pip install --no-cache-dir --disable-pip-version-check -r /app/requirements.docker.txt

COPY src /app/src
COPY scripts/run_phase_101_telegram_channel_service.py /app/scripts/run_phase_101_telegram_channel_service.py
COPY scripts/run_phase_101_telegram_channel_publisher.py /app/scripts/run_phase_101_telegram_channel_publisher.py
COPY artifacts/phase_6_1_inference_contract/inference_contract_v1.json /app/artifacts/phase_6_1_inference_contract/inference_contract_v1.json
COPY artifacts/phase_6_2_local_inference_service/openapi_v1.json /app/artifacts/phase_6_2_local_inference_service/openapi_v1.json
COPY artifacts/phase_25_shadow_model_catalog/shadow_contract.json /app/artifacts/phase_25_shadow_model_catalog/shadow_contract.json
COPY artifacts/prematch_snapshots/active.json /app/artifacts/prematch_snapshots/active.json
COPY artifacts/prematch_snapshots/phase57_incremental_v1_20260727/manifest.json /app/artifacts/prematch_snapshots/phase57_incremental_v1_20260727/manifest.json
COPY artifacts/prematch_snapshots/phase57_incremental_v1_20260727/event_windows.json.gz /app/artifacts/prematch_snapshots/phase57_incremental_v1_20260727/event_windows.json.gz
COPY artifacts/prematch_snapshots/phase160_recent_topup_v1_20260811/manifest.json /app/artifacts/prematch_snapshots/phase160_recent_topup_v1_20260811/manifest.json
COPY artifacts/prematch_snapshots/phase160_recent_topup_v1_20260811/event_windows.json.gz /app/artifacts/prematch_snapshots/phase160_recent_topup_v1_20260811/event_windows.json.gz
COPY artifacts/phase_84a_team_count_markets/audit.json artifacts/phase_84a_team_count_markets/config.json artifacts/phase_84a_team_count_markets/hashes.json artifacts/phase_84a_team_count_markets/models.joblib /app/artifacts/phase_84a_team_count_markets/
COPY artifacts/phase_88_team_market_markov/config.json artifacts/phase_88_team_market_markov/hashes.json artifacts/phase_88_team_market_markov/team_market_markov.joblib /app/artifacts/phase_88_team_market_markov/
COPY artifacts/phase_106_probability_repair/calibrator.json artifacts/phase_106_probability_repair/hashes.json /app/artifacts/phase_106_probability_repair/
COPY artifacts/phase_114_live_markov_hawkes_v1/hawkes_league_policy.json /app/artifacts/phase_114_live_markov_hawkes_v1/hawkes_league_policy.json
COPY artifacts/phase_122_confidence_reliability/eligibility.json artifacts/phase_122_confidence_reliability/hashes.json /app/artifacts/phase_122_confidence_reliability/
# El guard de cobertura falla abierto: si este mapa no llega a la imagen, los
# mercados sin datos reales vuelven a publicarse en silencio. Mismo modo de
# fallo que ya ocurrió con eligibility.json de Fase 122.
COPY artifacts/metric_coverage/coverage_map.json /app/artifacts/metric_coverage/coverage_map.json

RUN addgroup --system app && adduser --system --ingroup app app \
    && mkdir -p /app/data /data \
    && chown -R app:app /app /data
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8000')+'/v1/health', timeout=2)"

CMD ["python", "scripts/run_phase_101_telegram_channel_service.py"]
