# Fase 21 — selector temporal por target

## Decisión implementada

Markov no se fuerza en todos los targets. La selección se realiza únicamente
con la calibración de 44 partidos y exige una mejora mínima de `0.02` en
log-loss frente al baseline.

## Resultado

Sólo `first_half_goal` selecciona Markov. `second_half_goal`, reacciones y
remontadas usan baseline temporal. En los 241 partidos de confirmación:

- `first_half_goal`: selector `0.626560` frente a `0.629820`;
- los demás targets no son degradados por la ruta seleccionada;
- cobertura completa y sin leakage;
- mercados promovidos: `False`.

Clasificación: `promising_unconfirmed`.

# Version: 1.0.0
# Created: 2026-07-26
