# Fase 80T — Markov condicionado por arquetipo pre-match

## Objetivo

Reemplazar el régimen in-play no predecible por un estado persistente conocido
antes del kickoff.

## Estado

La cadena observable usa cuatro clases direccionales por ventana:
`neither`, `home_only`, `away_only`, `both`.

El arquetipo fijo del partido usa únicamente perfiles causales pre-match:

- ritmo esperado total;
- balance o dominio esperado local/visitante.

Se probarán taxonomías de 4 y 6 arquetipos con cortes aprendidos sólo dentro del
train correspondiente.

## Comparadores

- tabular same-data factorized;
- Markov directo de clases;
- Markov de clases condicionado por arquetipo pre-match.

## Gate

Se conservan todos los gates de Fase 80: mejora `>= max(0.005, 1%)`, IC95%
positivo, Brier `>=0.002`, ECE, estabilidad por liga y confirmación temporal.
El router permanece congelado.

## Resultado

`rejected_for_revision`

La taxonomía `home_away_quadrants` con smoothing `200` fue seleccionada con
ocupación mínima `11.63%`. En confirmation obtuvo log-loss `0.989731` frente a
`0.989387` del Markov directo: mejora `-0.000344`, Brier `-0.000178`, IC95%
`[-0.000906, 0.000200]` y 34.48% de ligas no negativas.

La discretización queda descartada. Se autoriza únicamente evaluar transición
continua no homogénea en Fase 80U, sin promoción.
