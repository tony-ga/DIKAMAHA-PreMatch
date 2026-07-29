# Reauditoría Fase 76 — estados predictivos v2

## Clasificación

`promising_unconfirmed`

## Errores confirmados

- La GMM se aplicó a features con mediana de ceros `72.20%` y máximo
  `99.37%`; sus gaussianas crearon componentes degenerados.
- El estado conjunto local/visitante diluyó la dirección del riesgo.
- El lag completo duplicó ruido y redujo NMI.
- Goles, amarillas y rojas contemporáneos formaron estados transitorios raros,
  pero inestables entre periodos.
- El gate por liga exigía soporte simultáneo de esos estados raros y reducía
  artificialmente la auditoría a pocas ligas.

## Corrección

`predictive_latent_state_v2` aprende en `fit` un score regularizado de gol del
mismo equipo en la microventana siguiente. El target futuro nunca es feature.
El score se discretiza mediante cuantiles de `fit`; tiros, tiros a puerta,
corners, presión, faltas, concesiones, progreso y localía forman la emisión.
Goles y tarjetas permanecen auditados, pero no definen estados ordinarios.

## Resultados internos

- 6 estados, ocupación mínima `15.02%`;
- spread en `selection`: `0.053971`;
- NMI temporal: `0.779114`;
- orden estable en 30/30 ligas con soporte;
- mejora NLL de duración: `0.115708`;
- p permutación: `0.004975`, con p95 nulo `0.010399`;
- replay completo exacto por hash.

## Diagnóstico no independiente

La cohorte antes llamada confirmación ya fue observada durante la revisión. El
candidato obtiene allí spread `0.056352` y mejora de duración `0.113284`, pero
esas cifras no habilitan promoción ni Fase 77.

## Gate

La reauditoría demuestra que el error de Fase 76 estaba en la familia de
emisiones, no en la duración. El candidato queda funcional y congelado como
`promising_unconfirmed`. Fase 77 sigue bloqueada hasta una nueva cohorte
independiente.
