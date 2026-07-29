# Fase 103 — evaluación walk-forward de escaleras distribucionales

## Objetivo

Determinar si las distribuciones por equipo de Fase 102 aportan valor
incremental real frente a su baseline liga × localía sin usar información
posterior al kickoff ni escoger líneas con el bloque confirmatorio.

## Cohortes

- `fit`: sólo actualiza el estado causal;
- `selection`: puntúa todas las líneas congeladas y elige candidatos;
- `confirmation`: se abre una única vez después de escribir y hashear el
  manifiesto de candidatos.

Cada partido se predice antes de actualizar el modelo con sus seis ventanas.
Las dos orientaciones y todos los mercados del partido forman una unidad
indivisible de bootstrap.

## Selección de líneas

- rejilla idéntica a Fase 102;
- máximo una línea por `lado × métrica × periodo`;
- soporte mínimo de 200 partidos;
- prevalencia entre 5% y 95%;
- mejora simultánea de log-loss y Brier contra baseline;
- ECE no puede empeorar más de 0.01;
- desempate determinista por mejora de log-loss, Brier y línea.

La dirección over/under no se duplica porque ambas tienen el mismo score
propio al ser complementarias.

## Gate confirmatorio

Una línea se aprueba sólo si cumple simultáneamente:

- al menos 200 partidos;
- límite inferior del IC95% bootstrap de la mejora de log-loss mayor que cero;
- Brier no peor que baseline;
- ECE no peor que baseline;
- al menos 70% de ligas elegibles, con 30 partidos o más, no degradan.

El bootstrap usa 10,000 remuestras pareadas por partido completo. No se
modifica ningún criterio después de abrir confirmación.

## Salidas

El artefacto versionado debe contener configuración, cobertura, predicciones
congeladas, manifiesto de selección, métricas confirmatorias, auditoría,
reporte final y hashes. Fase 103 no promueve automáticamente ningún mercado.

## Tiros a puerta

El modelo negativo-binomial de Fase 84A se audita por separado. Si su proceso
de elección de hiperparámetros reutiliza toda la partición `selection`, no se
empleará ese mismo tramo para seleccionar líneas: se aplicará una división
temporal interna o se declarará explícitamente diagnóstico no promocionable.
