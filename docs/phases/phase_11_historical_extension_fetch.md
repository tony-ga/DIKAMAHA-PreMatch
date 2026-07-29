# Fase 11 — extensión histórica ESPN sin escritura canónica

## Objetivo

Obtener partidos completos posteriores a la cohorte de noviembre de 2025 para
aumentar la confirmación temporal de `temporal_targets v2`.

## Alcance

- Competición: `esp.1`.
- Rango inicial congelado: `20251201..20260524`.
- Fuente: endpoints ESPN allowlisted y su caché local.
- PostgreSQL: no se escribe ninguna tabla.
- Se excluyen los partidos ya presentes en `event_windows v1` y Fase 09.

## Gates

- Identidad local/visitante consistente.
- Estado final y marcador completos.
- Eventos con timestamp y hashes disponibles.
- Sin IDs duplicados ni solapamiento con cohortes anteriores.
- Payloads crudos conservados sólo en caché local; artefactos públicos sin
  `raw_data`.

## Clasificación

`validated_extension_available` sólo indica que existe una cohorte candidata
auditable. No implica que los targets o Markov estén validados.

## Siguiente paso

Construir ventanas v2 para los candidatos, anexarlos a la partición temporal y
repetir la evaluación con bootstrap por partido completo.

# Version: 1.0.0
# Created: 2026-07-26
