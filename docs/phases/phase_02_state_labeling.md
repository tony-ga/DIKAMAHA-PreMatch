# Fase 02 — `state_labeling v1`

## Objetivo

Convertir agregados de `event_windows v1` en estados tácticos reproducibles.

## Entregables

- Diccionario de estados y reglas.
- Fórmulas de presión, agresividad y comportamiento defensivo.
- Auditoría de distribución, cobertura y sensibilidad a umbrales.
- Tratamiento explícito de estado desconocido.

## Gate de salida

- Cada estado es explicable por campos de la ventana actual.
- Ninguna regla usa información futura.
- Los cambios razonables de umbral no destruyen la distribución ni la cobertura.
