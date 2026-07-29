# Fase 22 — señal pre-match de ritmo de primera mitad

## Objetivo

Evaluar una señal auxiliar para `first_half_goal` usando únicamente el ritmo
histórico de eventos observado antes del kickoff. La fase no modifica
`match_features v1`, no usa alineaciones/cuotas todavía y no promueve mercados.

## Entrada y construcción

- Ventanas históricas `event_windows v1` de las cohortes 2023-24, 2024-25 y
  agosto-octubre de 2025.
- Cohorte de calibración: 44 partidos de octubre-noviembre de 2025.
- Cohorte confirmatoria: 241 partidos de diciembre de 2025 a mayo de 2026.
- Para cada partido se calculan, antes del kickoff, tasas recientes de goles,
  tiros, tiros a puerta, corners, presión, faltas y tarjetas en primera mitad.
- La historia se limita a los últimos cinco partidos disponibles por equipo y
  se suaviza hacia la media histórica disponible.

## Modelo y evaluación

- Modelo auxiliar: regresión logística regularizada y estandarizada.
- Entrenamiento de calibración: histórico base previo a los 44 partidos.
- Confirmación: histórico base más los 44 partidos, evaluado sólo en los 241.
- Comparadores obligatorios: baseline de prevalencia causal y Markov v2 de Fase
  20.
- Métrica principal: log-loss por partido completo; incertidumbre por bootstrap
  de partidos, no por ventanas.

## Gates

- Ninguna feature puede incorporar ventanas, marcador o estadísticas del
  partido objetivo.
- No debe existir solapamiento entre train, calibración y confirmación.
- La promoción del modelo auxiliar exige mejora confirmatoria frente al
  baseline con intervalo bootstrap estrictamente positivo y cobertura completa.
- Si la evidencia cruza cero o el modelo degrada, se conserva el resultado
  negativo y el router de Fase 21 permanece sin cambios.

## Artefactos

`config.json`, `input_manifest.json`, `coverage.json`, `feature_rows.json`,
`metrics.json`, `calibration.json`, `confirmation.json`, `audit.json`,
`validation_report.md`, `final_report.md` y `hashes.json`.
