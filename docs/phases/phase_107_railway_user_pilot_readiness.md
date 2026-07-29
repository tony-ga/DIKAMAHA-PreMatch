# Fase 107 — preparación para pilotos reales en Railway

## Objetivo

Entregar una unidad desplegable, observable y recuperable para las primeras
pruebas con usuarios reales, sin modificar las probabilidades vigentes.

## Alcance

- salida Telegram orientada al usuario, sin vocabulario interno;
- contenedor Railway con API y worker supervisados;
- secretos exclusivamente por variables de entorno;
- ledger Telegram sobre volumen persistente;
- health, readiness, timeout, rate limit y límite de concurrencia;
- logs JSON sin cuerpos, tokens ni credenciales;
- retry exponencial y fallos parciales aislados;
- cierre limpio y reinicio idempotente;
- runbook de despliegue y rollback.

## Gate

- 100 solicitudes simultáneas sin caída del proceso;
- respuestas limitadas y contabilizadas cuando se agota capacidad;
- p95, throughput, códigos HTTP y errores auditados;
- requests inválidas, grandes, lentas y no autenticadas rechazadas;
- dependencia externa caída no destruye el ciclo completo;
- replay Telegram sin duplicados;
- todos los mensajes públicos bajo el límite y libres de términos internos;
- health/readiness disponibles durante operación normal;
- pruebas dirigidas y smoke de contenedor aprobados.

## Exclusiones

- no cambia Dixon-Coles, Kalman, Markov, BTTS ni PMF;
- no demuestra ROI;
- no publica mensajes reales durante el gate;
- no migra el ledger a PostgreSQL en esta primera unidad.

## Estado

`validated`.

La imagen final cargó la cadena vigente, exigió autenticación, respondió una
predicción real, ejecutó como usuario no privilegiado y cerró con código 0.
El gate de 100 solicitudes simultáneas produjo 16 respuestas `200` y 84
rechazos `503` controlados, sin timeouts ni pérdida de disponibilidad; p95
`2.892 s`. La suite dirigida aprobó 48 pruebas y Telegram completó un dry-run
de una agenda, tres tarjetas y tres dashboards.
