# Fase 52 — refresco post-2025 del snapshot

## Objetivo

Eliminar la advertencia de frescura del flujo real incorporando partidos
completos posteriores a diciembre de 2025 sin depender de PostgreSQL activo.

## Resultado

- Liga refrescada: `mex.1`.
- Referencias ESPN consultadas: `185`.
- Partidos completos incorporados: `168`.
- Ventanas de 15 minutos incorporadas: `2,016`.
- Partidos excluidos: `17`, todos por discrepancia entre goles observados y
  marcador final; no se forzó ninguna corrección.
- Snapshot activo: `phase52_post2025_mex_v1_20260727`.
- Filas finales: `113,544`.
- PostgreSQL escrito: `False`.

## Revalidación del fixture real

Puebla–Guadalajara volvió a resolverse con HTTP 200 y sin usar datos del
partido objetivo. El histórico ahora llega al `2026-07-18`, con 13 días de
antigüedad; `history_freshness_warning` quedó en `False`.

## Gates de seguridad

- La publicación fue inmutable y con hash SHA-256.
- La versión anterior queda disponible para rollback.
- Los 17 partidos inconsistentes permanecen fuera del snapshot.
- No se entrenó, evaluó ni promovió Markov.

## Limitación

Esta actualización cubre `mex.1`, que es la liga del fixture real probado. El
siguiente refresco operativo debe repetir el proceso para las demás ligas con
partidos post-2025 antes de declarar frescura global.
