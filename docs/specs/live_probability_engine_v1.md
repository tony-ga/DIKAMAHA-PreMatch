# `live_probability_engine_contract_v1`

## Alcance

Contrato causal oficial para probabilidades in-live. Dixon-Coles/Kalman
aportan las intensidades anteriores al kickoff; el partido objetivo sólo aporta
eventos observados hasta `snapshot_ts`. ESPN Predictor y `pickcenter` quedan
fuera del motor.

## Entrada obligatoria

- identidad de fixture, liga, local y visitante;
- kickoff, `snapshot_ts`, periodo, reloj y marcador;
- intensidades pre-match con cutoff estricto anterior al kickoff;
- eventos normalizados, deduplicados, orientados y no futuros;
- hash del snapshot y del prior causal.

Los eventos desconocidos, anulados, duplicados y sin equipo se conservan en la
auditoría. Goles válidos deben reconciliar el marcador.

## Capas

1. `dynamic_poisson`: integra intensidades por segmentos de cinco minutos.
2. `ctmc`: propaga tres regímenes continuos —presión local, equilibrio y
   presión visitante— mediante una matriz generadora conservativa.
3. `hazard`: aplica factores Cox acotados con ventanas causales 5/10/20.
4. `dynamic_elo`: transforma la fuerza estructural pre-match y evidencia live
   contraída en un ajuste latente simétrico.
5. `hawkes_residual`: modula hazards en log-escala con la política congelada de
   admisión por liga; nunca suma otra intensidad independiente.
6. `monte_carlo_diagnostic`: valida de forma asincrónica la solución analítica
   con semilla derivada de fixture y snapshot.

## Salida

`official_live_prediction` publica intensidades restantes, 1X2, periodos,
over/under 0.5–3.5, BTTS, score exacto, distribución de goles, próximo evento,
gol en 5/10 minutos, confianza, timestamp y fallback. El bloque
`live_probability_engine` conserva cada capa, provenance y auditoría.

Los aliases `experimental_markov_live`, `experimental_hawkes_residual` y
`experimental_combined_live` permanecen durante la migración de clientes.

## Invariantes

- probabilidades finitas en `[0, 1]` y 1X2 normalizado dentro de `1e-10`;
- tasas finitas no negativas y CTMC con filas que suman cero;
- ningún evento posterior al snapshot;
- mismo snapshot/configuración produce el mismo hash;
- `rho=0` reproduce exactamente el baseline analítico;
- Monte Carlo no bloquea la respuesta;
- fallo del motor usa fallback versionado sin recalcular pre-match.

Version: 1.0.0
Created: 2026-08-09
