# Fase 76 — confirmación prospectiva ciega de v3

## Estado

`insufficient_coverage`

## Lock

- cutoff: `2026-07-28T06:44:20.320524Z`;
- modelo: `predictive_latent_state_v3`;
- SHA-256 parámetros:
  `4dd565131c39a86718b57f31e9f02a345833d408bacb068870c45a1db8256fb2`;
- features, cuatro estados y umbrales congelados;
- mínimo para abrir outcomes: 200 partidos utilizables y 10 ligas.

## Primera captura

- partidos completos posteriores al cutoff: `0`;
- ligas: `0`;
- métricas selladas: `True`;
- outcomes leídos: `False`;
- hash del modelo verificado: `True`;
- replay sin cambios: `True`.

Una captura vacía inmediatamente después del cutoff es el resultado esperado.
No se calcula spread, duración ni estabilidad antes de alcanzar la cobertura.

## Operación

La automatización diaria ejecuta lock idempotente, colección y gate ciego.
Usa Core `/plays` paginado y el fallback raw-first
`Site /summary.commentary`. La evaluación sólo se ejecutará una vez cuando el
gate cambie a `ready_for_evaluation`.
