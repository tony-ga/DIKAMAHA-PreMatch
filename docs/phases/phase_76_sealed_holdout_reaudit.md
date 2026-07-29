# Fase 76 — reauditoría de la base completa

## Resultado

`rejected_for_revision`

La base PostgreSQL contiene 10,251 partidos y 42 ligas. La exclusión exacta de
los 9,444 partidos usados por el corpus de Fase 74 produjo 807 identidades no
usadas; 777 estaban marcadas como completas y cubrían nominalmente 14 ligas.

## Cobertura útil

Al reconstruir las ventanas y reconciliar cada secuencia contra el marcador:

- 376 partidos de 9 ligas fueron utilizables;
- 401 partidos fallaron reconciliación;
- los 401 se solicitaron nuevamente sin caché con paginación completa;
- ESPN devolvió `items=[]` para esos timelines históricos;
- no se imputaron goles ni timestamps desde el marcador final.

La extracción “de todas las ligas” sí contiene fixtures y marcadores, pero no
contiene play-by-play recuperable para toda la cobertura nominal.

## Resultado sobre los 376 partidos limpios

- ocupación mínima: `12.729%`;
- mejora NLL de duración: `+0.193679`;
- orden de riesgo estable: `3/4` ligas con soporte (`75%`);
- spread de riesgo siguiente: `0.034152`, inferior al gate `0.05`;
- modelo reentrenado: `False`;
- solapamiento con Fase 74: `0`;
- router modificado: `False`.

## Correcciones aplicadas

- cohorte sellada por exclusión exacta de IDs, no sólo por fecha;
- reingesta selectiva sin reutilizar la caché histórica truncada;
- rechazo obligatorio si más de 2% de los timelines no reconcilian;
- clasificación calculada por gates, sin `insufficient_coverage` fijo;
- pruebas de aprobación, separación semántica y corrupción de timelines.

## Decisión

Fase 76 no queda aprobada: la cobertura útil es 376/9 y el candidato tampoco
alcanza el spread semántico en el holdout limpio. Declararla al 100% ocultaría
dos fallos medidos. La siguiente revisión debe mejorar la representación
predictiva entre dominios y validarla sobre play-by-play real; no debe convertir
marcadores finales en eventos sintéticos.

## Addendum de calidad de ingesta — 2026-07-28

La reconciliación conservadora posterior recuperó cinco partidos y una décima
liga: `381/10`. El mínimo nominal `200/10` ya se cumple en este holdout, pero el
spread v3 queda en `0.042241`, todavía por debajo de `0.05`. Este addendum
corrige el diagnóstico de cobertura; no reabre el holdout ni autoriza ajuste.
Véase `phase_76_ingestion_quality_reaudit.md`.
