# Fase 03 — `markov_pre_match v1`

## Objetivo

Calibrar transiciones dependientes con soporte empírico, smoothing y backoff.

## Entregables

- Matrices condicionadas.
- Priors y política de backoff.
- Soporte por celda y celdas escasas.
- Validación temporal de likelihood y calibración.

La regularización debe fijarse antes de evaluar el bloque de validación; cualquier ajuste posterior exige una nueva versión y una nueva partición temporal.

## Gate de salida

- Cada matriz normaliza y es reproducible.
- Ninguna celda insuficiente se presenta como estimación individual fiable.
- El modelo supera o iguala al Markov global en validación sin leakage.
