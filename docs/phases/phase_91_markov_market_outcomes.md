# Fase 91 — outcomes prospectivos Markov por mitad

## Objetivo

Materializar sin imputación los outcomes de las cuatro líneas congeladas en
Fase 90, sin alterar ninguna predicción.

## Fuentes

- summary ESPN final para identidad, orientación y totales de boxscore;
- play-by-play paginado para asignar corners y tiros a 1T/2T;
- persistencia raw-first antes del parseo;
- consultas permitidas únicamente desde `kickoff + 3h`.

## Reconciliación

Los eventos `corner`, `shot_on_target`, `shot_off_target` y `shot_blocked`
se cuentan por equipo y mitad. La suma temporal debe igualar el total del
boxscore del mismo equipo y métrica. Eventos anulados no cuentan; eventos
modelables sin equipo o reloj rechazan el outcome.

## Targets

- tiros visitante 2T mayor a 5;
- corners local 2T mayor a 2;
- tiros local 1T mayor a 5.

## Gates

- identidad y kickoff exactos;
- partido final;
- cuatro outcomes presentes;
- reconciliación total/mitades;
- raw-first y paginación completa;
- predicciones inmutables;
- scoring bloqueado hasta completar la cohorte.

## Primera ejecución

Estado: `insufficient_coverage`.

- 520 predicciones intactas;
- 0 fixtures elegibles;
- 0 llamadas post-match;
- 0 outcomes y 0 rechazos;
- hash de predicciones idéntico antes/después.
