# Fase 01 — `event_windows v1`

## Objetivo

Producir un dataset histórico y auditable de eventos agregados por ventanas de 15 minutos.

## Criterios de entrada

- `event_windows v1` aprobado.
- Reglas de reconciliación de ESPN disponibles.
- Ledger con timestamp, equipo, tipo de evento y bandera de anulación.

## Entregables

- Configuración de ventanas.
- Dataset de ventanas.
- Auditoría temporal y de cobertura.
- Reporte de duplicados, anulados, desconocidos y equipos nulos.
- Manifest, hashes y reporte final.

## Criterios de salida

- Sin eventos válidos fuera de su ventana.
- Cobertura cuantificada para cada tipo de evento relevante.
- No hay uso de partido objetivo en una futura inferencia pre-match.
- Reejecución determinista confirmada.

## Fuera de alcance

- Etiquetar estados finales.
- Entrenar Markov.
- Simular mercados.
