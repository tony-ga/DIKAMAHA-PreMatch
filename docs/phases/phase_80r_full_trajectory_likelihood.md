# Fase 80R — likelihood de trayectoria completa

## Objetivo

Evaluar la contribución específicamente secuencial de Markov sobre la
trayectoria direccional de seis ventanas de 15 minutos.

## Comparadores

- tabular same-data factorized;
- tabular más transición directa entre clases observadas;
- Markov latente dual con 36 pares de estados.

La probabilidad conjunta se factoriza durante el scoring. Condicionar un factor
en outcomes anteriores no convierte esos outcomes en features pre-match: el
modelo define antes del kickoff la distribución de todas las secuencias.

## Gate

- mejora por ventana `>= max(0.005, 1%)` contra el mejor comparador;
- IC95% por partido estrictamente positivo;
- Brier condicional mejora `>=0.002`;
- ECE no empeora más de `0.005`;
- estabilidad por liga y temporada según Fase 80;
- parámetros y selección aislados dentro de cada fold;
- cero uso de estados u outcomes del target para construir la distribución.

Fase 81 y el router permanecen bloqueados durante la revisión.

## Resultado

`rejected_for_revision`

La selección eligió `no_transition` con smoothing `50`, evidencia directa de
que las transiciones latentes no explican la pequeña mejora de emisiones. En
confirmation, Markov obtuvo log-loss `0.989798` frente a `0.989387` del
comparador secuencial directo, con mejora `-0.000411`, Brier `-0.000088` e
IC95% `[-0.001266, 0.000422]`. Sólo 48.28% de ligas fueron no negativas.

Fase 81 permanece bloqueada. Se permite únicamente publicar mercados de
trayectoria como shadow experimental para conservar funcionalidad y provenance.
