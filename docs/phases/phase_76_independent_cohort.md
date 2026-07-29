# Fase 76 — cohorte independiente acumulativa

## Estado

`insufficient_coverage`

## Cutoff sellado

`2026-07-26T18:00:00Z`

El colector consulta todas las ligas registradas desde la fecha del cutoff
hasta hoy UTC. Sólo admite partidos completos con kickoff posterior, guarda
payloads raw antes de parsear, reconcilia marcador y aplica
`predictive_latent_state_v2` sin reentrenar ni cambiar umbrales.

## Primera captura

- 19 partidos de 5 ligas;
- 25,851 eventos únicos;
- 76 payloads raw;
- 19/19 marcadores reconciliados después de corregir paginación;
- replay de ingesta idempotente;
- cero cambios al router.

## Bug corregido

El endpoint Core devolvía más de 300 plays, pero el conector consumía sólo la
primera página. La primera evaluación reconcilió únicamente 1/19 partidos. La
versión 1.1.0 recorre `pageCount`, conserva todas las páginas raw y elevó la
reconciliación a 19/19.

## Resultado descriptivo

- ocupación de los seis estados entre 13.45% y 20.03%;
- spread observado `0.099888`;
- duración explícita `-0.025180` frente a geométrica;
- sólo Brasil alcanzó soporte por estado para orden de riesgo.

Estas cifras no permiten modificar ni aprobar el modelo.

## Gates

- confirmación Fase 76: mínimo 200 partidos y 10 ligas;
- confirmación prospectiva Fase 81: mínimo 500 partidos y 10 ligas.

El colector es acumulativo y reproducible. Fase 77 permanece bloqueada mientras
el primer gate no tenga cobertura.
