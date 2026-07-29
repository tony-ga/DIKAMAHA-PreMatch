# Fase 76 — descubrimiento de estados latentes

## Estado

`rejected_for_revision`

## Resultado

La formulación final direccional por equipo seleccionó 8 estados únicamente en
`fit/selection`. En selección alcanzó spread de gol siguiente `0.062649` y
mejora de duración `0.034856`, pero no generalizó:

- spread confirmatorio `0.029279`, inferior al gate `0.05`;
- NMI mínimo `0.569401`, inferior al gate `0.70`;
- mejora confirmatoria de duración `0.035683`;
- orden de riesgo estable en 2/2 ligas con soporte, cobertura demasiado
  estrecha para compensar los gates fallidos;
- cuatro estados ordinarios con ocupación superior a 5% y cuatro estados
  transitorios ligados a gol o disciplina.

La confirmación fue observada durante la revisión y no puede reutilizarse para
otra selección. Fase 77 permanece bloqueada. La siguiente revisión deberá
congelarse sobre una nueva cohorte independiente o cambiar la familia de
emisiones usando sólo desarrollo.

## Reauditoría aditiva

La familia GMM no se sobrescribe. Su reemplazo experimental y las causas del
fallo están documentados en
`docs/phases/phase_76_predictive_state_reaudit.md`, con clasificación
`promising_unconfirmed`.

## Entradas

- microventanas causales de 5 minutos de Fase 74;
- particiones inmutables `fit`, `selection`, `confirmation`;
- baseline same-data de Fase 75;
- DEC-079.

## Emisiones

Cada observación conjunta local/visitante contiene diferencias y totales de
tiros, tiros a puerta, corners, presión, faltas, tarjetas y goles observados en
la microventana, además del progreso reglamentario normalizado, conocido sin
observar eventos futuros. El gol de la ventana siguiente no es una emisión:
se conserva únicamente como label para evaluar semántica predictiva.

## Selección

- candidatos de 4 a 8 estados;
- normalización y emisiones ajustadas sólo en `fit`;
- número de estados y semilla seleccionados sólo con `selection`;
- alineación mediante distancia entre centroides; NMI es invariante a etiqueta;
- `confirmation` se abre una sola vez después de congelar el candidato.

## Duración

Se estima `P(D=d|S)` discreta y suavizada sobre runs completos de `fit`. Su
log-likelihood en `selection/confirmation` debe superar la distribución
geométrica derivada de la persistencia del Markov ordinario.

## Gate

- 4–8 estados y ocupación ordinaria global ≥5%;
- NMI entre semillas/folds ≥0.70;
- spread absoluto de gol en ventana siguiente ≥0.05;
- orden de riesgo estable en ≥75% de ligas con soporte;
- NLL de duración explícita inferior al geométrico en OOS;
- cero target futuro en emisiones;
- router sin cambios.

## Artefactos

`config.json`, `input_manifest.json`, `coverage.json`, `audit.json`,
`metrics.json`, `state_cards.json`, `state_assignments.jsonl`,
`validation_report.md`, `final_report.md` y `hashes.json`.
