# Fase 65 — auditoría de posición y fallas Markov

## Objetivo

Determinar con el corpus actual si la implementación Markov residual aporta
información adicional al baseline temporal y localizar el punto de degradación.

## Diseño causal

- Desarrollo de `state_0`: 5,880 partidos hasta `2025-09-07 11:00 UTC`.
- Auditoría walk-forward: 3,921 partidos posteriores.
- Cada partido se predice antes de incorporar sus ventanas, estados, tasas o
  perfiles al siguiente kickoff.
- Las transiciones utilizadas provienen de matrices ajustadas previamente con
  el bloque de desarrollo.
- El target es sólo `first_half_goal`.

## Resultado global

- Markov log-loss: `0.627332`.
- Baseline log-loss: `0.626786`.
- Mejora Markov: `-0.000546`.
- IC bootstrap: `[-0.001350, 0.000155]`.
- Markov Brier: `0.217772`.
- Baseline Brier: `0.217628`.
- Router modificado: `False`.

## Fallas localizadas

1. La cobertura de transición es insuficiente: los tiers `global` y `uniform`
   dominan sobre `competition`, `window` y `team`.
2. Sólo 1,291 consultas de transición fueron `team` frente a 164,636 globales
   (`home` + `away`); la identidad del equipo rara vez llega al cálculo final.
3. La señal Markov prácticamente no cambia el baseline: ningún partido tuvo un
   lift positivo superior a `0.05` y sólo 5 tuvieron lift inferior a `-0.05`.
4. Hay 423 partidos con historial menor a cinco partidos para alguno de los
   equipos, lo que debilita `state_0` en el arranque de temporada o competiciones.
5. La heterogeneidad por liga es real: algunas ligas mejoran marginalmente y
   otras degradan; no existe un peso global defendible.

## Posición actual

Markov está integrado correctamente como candidato causal, pero su señal es
demasiado débil y su contexto demasiado globalizado para justificar activación.
La falla principal no es la comparación contra el baseline: es que la cadena no
está recibiendo suficiente soporte específico de equipo/liga para representar
la reacción contextual que se pretendía modelar.

## Próxima corrección permitida

Construir una recalibración de transiciones con pooling jerárquico explícito y
soporte temporal ampliado, auditar su cobertura por tier y repetir esta misma
auditoría walk-forward. No se permite activar Markov ni ajustar pesos con el
bloque de evaluación futura.

