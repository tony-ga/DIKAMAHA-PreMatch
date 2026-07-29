# Fase 88 — Markov de mercados por equipo

## Objetivo

Modelar la trayectoria pre-match de corners, tiros y tarjetas por equipo,
separando primera y segunda mitad, mediante cadenas de 15 minutos.

## Estados

Cada métrica tiene estados observables y auditables:

- corners: `sin_corner`, `presion`, `asedio`;
- tiros: `bajo`, `ataque`, `asedio`;
- tarjetas: `limpio`, `amonestado`, `desorden`.

Los estados se derivan únicamente del conteo de la ventana observada. Las
emisiones preservan la distribución empírica de conteos dentro de cada estado.

## Pre-match

La distribución inicial y las transiciones usan exclusivamente partidos
anteriores al kickoff. El pooling sigue:

`equipo × localía → liga × localía → global`

Después de predecir un partido, su outcome puede actualizar el historial para
kickoffs posteriores. Nunca se actualiza antes de emitir ambas orientaciones.

## Mercados

Para local y visitante:

- corners 1T y 2T O2.5;
- tiros 1T y 2T O5.5;
- tarjetas 1T y 2T O0.5.

## Backtest

- últimas 100 predicciones de `confirmation`;
- selección cronológica previa al scoring;
- comparación contra prior liga/localía con los mismos datos;
- accuracy, log-loss y Brier por mercado y total;
- reporte ordenado por calidad media del partido;
- replay idéntico.

La prueba es histórica y diagnóstica; sólo se promoverán mercados que superen
su baseline y mantengan causalidad.

## Resultado

Estado: `partially_validated`.

- corpus: 9,646 partidos y 39 ligas, incluidos 181 partidos de 2024;
- evaluación congelada: 100 partidos y 1,200 probabilidades;
- fiabilidad: 65.92% Markov frente a 66.58% baseline;
- log-loss: 0.608606 frente a 0.596778;
- Brier: 0.211424 frente a 0.204902;
- IC95% de mejora total: `[-0.035153, 0.017891]`;
- cuatro mercados ganan simultáneamente log-loss y Brier.

Los candidatos históricos son tiros visitante 2T O5.5, corners local 2T O2.5
y tiros local 1T/2T O5.5. Los otros ocho mercados conservan el baseline. Esta
selección por mercado no convierte el bloque completo en sustituto del
baseline ni modifica el router de goles.

La versión vigente usa tiros comerciales: cada gol válido suma también a
`shots`, igual que `totalShots` de ESPN.

## Evidencia

- `artifacts/phase_88_team_market_markov/final_report.md`;
- `artifacts/phase_88_team_market_markov/metrics.json`;
- `artifacts/phase_88_team_market_markov/ranked_100_predictions.csv`;
- `artifacts/phase_88_team_market_markov/audit.json`;
- `artifacts/phase_88_team_market_markov/hashes.json`.
