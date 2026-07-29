# Fase 6.2 - Servicio local de inferencia DIKAMAHA

**Decision:** `local_inference_service_approved_with_caveats`

Servicio FastAPI local/dry-run sobre el contrato de Fase 6.1.

## Endpoints

- `GET /v1/health` devuelve versiones y flags.
- `POST /v1/predict/pre-match` deriva mercados desde la matriz Poisson.
- `POST /v1/predict/upcoming` acepta liga, equipos y kickoff y usa el snapshot causal local.
- `POST /v1/predict/fixture` resuelve ESPN sólo en operational_readonly y sin persistencia.
- `POST /v1/predict/live` devuelve intensidades y estado Markov sin probabilidades.

## Seguridad de alcance

- Hawkes: desactivado por defecto y bloqueado para predicciones oficiales.
- PostgreSQL: no accedido, no modificado y sin migraciones.
- Llamadas externas: desactivadas.
- Persistencia: desactivada.

## Caveats

- Kalman v2 permanece experimental.
- Markov v1 conserva matriz sintética no calibrada.
- No es un servicio productivo ni está desplegado externamente.

## Hashes

- `audit`: `da7f9d4e10047429a92f37af575b49bf02737d8d72f4b19f95f4fab2f8febef4`
- `configuration`: `6a2b68854c4d9c1d5908936ddb0f7438ed3046558cbe954fffa8cd0e12ab2611`
- `examples`: `26024b7edf2775613d2fd4c5a6bee82539cb785869e88de689468e70d92aa398`
- `openapi`: `9894d127932846623f168b011fcb4d5102aa0b92f425e7f51495a28cb54f9bde`