# Especificación `markov_residual_selective v1`

## Propósito

Evaluar Markov como una corrección temporal selectiva sobre un baseline de
intensidad pre-match, no como sustituto de Dixon-Coles ni como estimador global
de goles.

## Capas congeladas

1. Dixon-Coles estima la estructura de goles.
2. Kalman se evalúa como actualización temporal del estado.
3. Markov redistribuye temporalmente la intensidad ya estimada.

Markov no puede cambiar arbitrariamente la masa total esperada de goles.

## Estado inicial

Antes del kickoff se estima `P(state_0)` con variables estrictamente causales:

- fuerzas Dixon-Coles;
- estado Kalman;
- localía;
- diferencia esperada de goles;
- ritmo y presión históricos;
- forma reciente;
- identidad de liga y equipos.

La primera implementación debe usar un clasificador multinomial calibrado. Un
modelo complejo sólo se permite si supera a ese clasificador en validación.

## Gate selectivo

La entropía de `P(state_0)` se calcula por partido. El umbral se aprende sólo
en validación y se congela antes de confirmación. Si la incertidumbre supera el
umbral, se utiliza el baseline sin Markov. Si no, se evalúa el residual Markov.

## Alcance inicial

El primer target es `first_half_goal`. Corners, tarjetas, remontadas, 1X2,
BTTS y Over/Under requieren modelos y gates separados.

## Promoción

Markov sólo puede promocionarse para un mercado si mejora al baseline reforzado,
mantiene calibración, tiene soporte suficiente, obtiene bootstrap por partido
con intervalo estrictamente favorable y conserva estabilidad temporal y por
liga. Hasta entonces, el router oficial permanece baseline-only.

