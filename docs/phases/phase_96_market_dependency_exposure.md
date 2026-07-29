# Fase 96 — dependencia y exposición shadow

## Objetivo

Medir la dependencia entre los nueve mercados del mismo partido y evitar
tratar 4,500 decisiones como observaciones económicamente independientes.

## Entregables

- correlación phi de outcomes;
- correlación de probabilidades;
- componentes de concentración positiva con umbral 0.30;
- distribución de 0–9 aciertos por partido;
- auditoría específica de los 9 partidos perfectos;
- política shadow de máximo tres selecciones por partido y una por componente.

## Gate

- 500 partidos completos;
- matrices simétricas con diagonal uno;
- todas las nueve líneas presentes;
- bootstrap y métricas siguen agrupados por partido;
- política informativa, sin stakes, Kelly, ROI o activación automática.

## Interpretación

Los pares negativos se auditan como dependencia, pero no se unen como
concentración positiva. La fase reduce concentración futura, no demuestra
rentabilidad y no puede seleccionar mercados usando outcomes del mismo partido.

## Resultado

Clasificación: `validated`.

- 500 partidos, nueve mercados y 4,500 decisiones;
- seis pares con correlación absoluta >=0.30;
- tres pares principales por equipo/periodo entre 0.608 y 0.657;
- nueve partidos perfectos frente a 6.72 esperados bajo independencia;
- distribución completa de 0–9 aciertos publicada;
- política shadow materializada sin stakes ni activación automática;
- replay idéntico y suite integral 404/404.
