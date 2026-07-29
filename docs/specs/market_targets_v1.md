# Especificación `market_targets v1`

## Propósito

Definir targets post-match y reglas de evaluación separadas para cada mercado pre-match. Un mercado no se habilita sólo porque existan eventos; requiere fuente, cobertura y validación propias.

## Mercados iniciales

- 1X2, total de goles, over/under y BTTS: derivados del marcador final canónico.
- Corners: derivados exclusivamente de eventos reconciliados o una estadística canónica trazable.
- Tarjetas: amarillas y rojas válidas, con anulaciones tratadas explícitamente.
- Tiros: tiros totales y a puerta conforme a reglas de reconciliación versionadas.

## Reglas

- No mezclar definición de target y feature.
- Medir cobertura, discrepancias y cambios de definición por mercado.
- Bloquear un mercado si su fuente no es reproducible o si no alcanza cobertura mínima.
- Comparar cada mercado contra su baseline propio; una mejora en goles no valida corners o tarjetas.

## Salida

Cada mercado habilitado expone definición, fuente canónica, cobertura, ventana temporal, métricas OOS y decisión de promoción independiente.
