# Especificación `markov_state_semantics v3`

## Propósito

Representar el contexto temporal de un partido sin confundir control territorial
con riesgo conjunto de gol. La versión es candidata y no reemplaza contratos
oficiales hasta superar evaluación fuera de muestra.

## Dos ejes separados

- `tempo_state`: régimen conjunto del partido (`calma`, `construccion`,
  `amenaza_sostenida`, `intercambio_abierto`). Es el único eje autorizado para
  modular `first_half_goal`.
- `control_state`: régimen direccional por equipo (`control`, `neutral`,
  `bajo_presion`). Se conserva para futuros mercados de atribución de gol y no
  entra en el target conjunto.

Los estados se derivan de tiros, tiros a puerta, bloqueos y corners. Los goles
no forman parte de la regla de estado.

## Alineación temporal

La evidencia observada en la ventana `t` describe el régimen previo a `t+1`.
La primera ventana usa una distribución `state_0` estimada sólo con perfiles
anteriores al kickoff. Ningún evento del partido objetivo está disponible en
inferencia pre-match.

## Cadena

`tempo_state` es una sola cadena por partido. Las transiciones aplican pooling
`equipo-participante → liga+ventana → ventana → global`, conservando soporte y
provenance. No se simulan dos cadenas independientes para estados recíprocos.

## Residual y abstención

El baseline Dixon-Coles/Kalman permanece anclado. Un calibrador regularizado
recibe el logit del baseline y ocupaciones esperadas de la cadena. Si el modelo
no supera el baseline en validación o el gate de liga/entropía no tiene soporte,
la probabilidad final es exactamente la del baseline.

## Gates

1. Semántica: cuatro estados con soporte y orden de riesgo causal verificable.
2. Predictibilidad: `state_0` y transiciones superan priors de liga/ventana.
3. Incremental: log-loss y Brier mejores, bootstrap por partido estrictamente
   favorable y estabilidad temporal/por liga en holdout.

## Prohibiciones

- No usar goles para etiquetar estados.
- No usar la ventana `t` para emitir goles de esa misma ventana observada.
- No seleccionar hiperparámetros, ligas o umbrales con el holdout.
- No activar Markov si cualquiera de los tres gates falla.

