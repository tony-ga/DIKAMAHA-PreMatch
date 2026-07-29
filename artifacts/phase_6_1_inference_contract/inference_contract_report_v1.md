# Fase 6.1 - Contrato de inferencia DIKAMAHA

**Decision:** `inference_contract_approved_with_caveats`

El ensamblaje local respeta Dixon-Coles v1 -> Kalman v2 -> Markov v1. Hawkes v1 permanece desactivado por defecto y no esta permitido para predicciones oficiales.

## Resultado

- Pre-match deriva 1X2, Over 2.5 y BTTS exclusivamente de la matriz Poisson.
- Kalman conserva intercepto fijo, localia en estado, suma-cero y provenance experimental.
- Markov recibe lambda_base explicitamente, conserva C_e(t)=1.0 y no genera probabilidades.
- Replay determinista: `true`.
- PostgreSQL y migraciones: no accedidos ni modificados.

## Caveats

- Kalman v2 sigue experimental.
- La matriz Markov v1 sigue siendo sintetica y no calibrada.
- Hawkes v1 permanece `hawkes_candidate_unconfirmed` y desactivado.

## Hashes

- `audit`: `ecfaeb6a5011cf26452c589e389d63843ccd0460590447272fd3d7a86093a072`
- `configuration`: `0eb13fa80729333b923cd48cd9b03dd6d3e59dddd0f4bb6b22f13a8e683ec113`
- `contract`: `2c075af8003b11e4a8325115989893074b4703c97acf66f286c1ed24bf8674a4`
- `examples`: `744caadb90e1bf4d954f144eab90982a78ee6f1ebb443010106c84d9f5d1a50a`
- `tests`: `cdef6ca58ea6c3db2d051a1a0ce208c9a3e989d0b0c7c8f188974f7efc63f10c`