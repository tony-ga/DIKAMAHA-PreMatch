# Fase 74 — corpus causal y calidad secuencial

## Estado

`ready_for_phase_75`

## Resultado

- 10,202 partidos completos auditados en 42 ligas;
- 39 ligas admitidas y 3 excluidas por cobertura inferior a 95%;
- 9,465 partidos reconciliados publicados tras reauditoría de ingesta;
- 9 discrepancias de marcador y 4 timelines ausentes excluidos con identidad;
- 339,984 filas a 5 minutos, 169,992 a 10 y 113,328 a 15;
- cero solapamiento entre ajuste, selección y confirmación;
- PostgreSQL permaneció SELECT-only y no hubo entrenamiento.

## Objetivo

Reconstruir desde el ledger ESPN por jugada un corpus multi-liga en ventanas de
5, 10 y 15 minutos. Las resoluciones se calculan desde el reloj original; está
prohibido subdividir agregados de 15 minutos.

## Contrato causal

- El contexto de una ventana termina exactamente en su inicio.
- Los eventos de la ventana son observaciones, no features del objetivo.
- Marcador inicial y diferencia de gol contienen sólo eventos anteriores.
- Tiempo añadido se absorbe en la última ventana de cada parte.
- Tandas, anulaciones y eventos sin equipo no se asignan silenciosamente.

## Partición

Los partidos se ordenan por kickoff y se separan de forma inmutable:

- 60% ajuste;
- 20% selección;
- 20% confirmación independiente.

No se permite que un `match_id` aparezca en más de una partición.

## Gate

- 100% de marcadores reconciliados en partidos publicados;
- cero eventos del objetivo en su contexto inicial;
- cero solapamiento entre particiones;
- al menos 95% de secuencias completas por liga admitida;
- exclusiones por liga y partido versionadas;
- PostgreSQL consultado sólo mediante `SELECT`;
- sin entrenamiento ni modificación del router.

## Artefactos

`config.json`, `input_manifest.json`, `coverage.json`, `audit.json`,
`metrics.json`, `micro_windows_5m.jsonl`, `micro_windows_10m.jsonl`,
`micro_windows_15m.jsonl`, `validation_report.md`, `final_report.md` y
`hashes.json`.
