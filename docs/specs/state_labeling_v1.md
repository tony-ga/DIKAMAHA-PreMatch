# Especificación `state_labeling v1`

## Propósito

Etiquetar cada fila de `event_windows v1` con un estado explicable usando sólo campos de su propia ventana y el marcador al inicio. El label no consulta la ventana siguiente ni el resultado final.

## Variables derivadas

- `aggression = fouls + yellow_cards + 2 * red_cards`.
- `defensive_load = pressure_conceded - pressure`.
- `pressure_margin = pressure - pressure_conceded`.

Estas variables describen la ventana; no son estados por sí mismas.

## Prioridad de etiquetas

1. `desorganizacion`: tarjeta roja propia o carga defensiva muy alta.
2. `repliegue`: ventaja al inicio, poca presión propia y presión rival material.
3. `presion`: presión propia alta con ventaja sobre el rival.
4. `equilibrio`: cualquier caso restante con cobertura observada.
5. `unknown`: cobertura no observable o valores estructuralmente inválidos.

## Umbrales base

- `pressure >= 3` y `pressure_margin >= 2` para `presion`.
- `goal_difference_start >= 1`, `pressure <= 1` y `pressure_conceded >= 2` para `repliegue`.
- `red_cards >= 1` o `defensive_load >= 3` para `desorganizacion`.

## Sensibilidad

Evaluar los umbrales de presión y margen en `base - 1`, `base`, `base + 1`. No congelar el label si un cambio unitario elimina estados, produce cobertura desconocida o altera materialmente la distribución sin explicación.

## Prohibiciones

- No usar score final, ventana siguiente, targets o features post-partido.
- No etiquetar agresividad o defensa por texto libre.
- No inferir estados desconocidos con defaults.
