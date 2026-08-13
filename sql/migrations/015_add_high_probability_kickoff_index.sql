-- Auditoría de producción (DEC-192): `unsettled()` y `frozen_on_date()`
-- (src/high_probability_settlement.py) filtran y ordenan
-- high_probability_pick_freezes por kickoff_ts en cada ciclo -unsettled()
-- corre cada HIGH_PROBABILITY_PROSPECTIVE_POLL_SECONDS (30 min por
-- defecto)-, pero la tabla no tenía más índice que su llave primaria
-- (pick_key). Confirmado con EXPLAIN contra producción: seq scan +
-- sort completos en cada corrida. La tabla es append-only por diseño
-- (DEC-182) y crecía ~290 filas/día medido en producción: sin índice, el
-- costo de cada ciclo sólo sube con el tiempo.
--
-- ASC, no DESC como el índice hermano de prediction_settlements: las dos
-- consultas reales de esta tabla ordenan `kickoff_ts ASC` (el pick más
-- antiguo primero), no DESC.
CREATE INDEX IF NOT EXISTS idx_high_probability_pick_freezes_kickoff_ts
ON high_probability_pick_freezes (kickoff_ts ASC);

-- Version: 1.0.0
-- Created: 2026-08-13
