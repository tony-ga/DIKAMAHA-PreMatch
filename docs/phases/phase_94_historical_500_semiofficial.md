# Fase 94 — validación histórica semi-oficial de 500 partidos

## Objetivo

Completar el paso 5 de acoplamiento con la mejor aproximación histórica al
flujo final: 500 partidos ya jugados, nueve mercados pre-match y liquidación
contra el play-by-play reconciliado.

## Cohorte congelada

- split: `confirmation`;
- selección: 500 elegibles más recientes, excluyendo Fase 88;
- tamaño: 500 partidos;
- exclusión: los 100 partidos publicados en Fase 88;
- selección anterior al scoring y sin filtrar por cobertura de outcome.

La elegibilidad de liga se congela sólo con `fit` y `selection`: al menos diez
partidos, media de corners totales >= 2 y media de tiros comerciales >= 8.
Esto excluye feeds cuya cronología existe pero omite sistemáticamente eventos
de mercado; no selecciona por acierto ni por outcome del partido objetivo.

## Mercados

Se usan las cinco líneas agregadas habilitadas por Fase 84A y las cuatro
líneas temporales habilitadas por Fase 88. Dixon-Coles/Kalman permanecen
intactos en el router oficial; esta fase valida el sidecar de mercados.

## Criterios de éxito

1. 500 partidos únicos y 4,500 predicciones individuales.
2. Predicción antes de actualizar el estado con el partido objetivo.
3. Cero solapamiento con la prueba histórica previa de 100.
4. Outcomes reproducidos desde las ventanas causales de play-by-play.
5. Equivalencia exacta con los outcomes congelados de Fase 84A.
6. Accuracy, log-loss y Brier por mercado, familia, liga y total.
7. Bootstrap pareado por partido y ranking completo de los 500 partidos.
8. Replay determinista de los artefactos.

## Interpretación

La salida es `semi_official_historical`: sirve para cerrar integración,
semántica y comportamiento end-to-end. No se etiqueta como promoción
prospectiva independiente porque las cuatro líneas Markov fueron elegidas
usando evidencia histórica posterior.

## Resultado

Estado: `semi_official_historical`.

- 500 partidos, 30 ligas y 4,500 decisiones;
- accuracy total: 62.31% frente a 59.78% del baseline;
- log-loss: 0.660556 frente a 0.677244;
- Brier: 0.233246 frente a 0.241387;
- IC95% pareado de mejora log-loss: [0.012022, 0.021301];
- agregado: 63.68% frente a 60.48%;
- Markov temporal: 60.60% frente a 58.90%;
- 9 partidos con 9/9 y media de 5.61/9;
- 27 ligas elegibles y 12 rechazadas por taxonomía incompleta;
- cero solapamiento con los 100 de Fase 88;
- 4,500 outcomes equivalentes al play-by-play reconciliado.
- replay completo con hashes idénticos;
- suite integral con PostgreSQL: 400 pruebas aprobadas.
