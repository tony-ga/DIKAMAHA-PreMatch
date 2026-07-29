# Fase 33 — materialización de insumos pre-match

## Objetivo

Conectar los candidatos aprobados por Fase 31 con las fuentes causales que
necesita Fase 32. La fase no evalúa, no entrena, no calcula targets y no
modifica el router oficial.

## Contrato causal

- Las features se construyen individualmente para cada candidato usando sólo
  partidos históricos con kickoff anterior al objetivo.
- Los candidatos nunca entran en la historia de otro candidato con el mismo
  kickoff; así se evita contaminación cruzada entre partidos prospectivos.
- El contexto usa la captura sanitizada de Fase 23: identidad, titulares,
  formación y cuotas `open` no live.
- Se excluyen eventos, marcador final, estadísticas post-match y cuotas
  `current`, `close` o live como variables del objetivo.
- La ausencia de timestamp histórico de publicación de ESPN mantiene el
  contexto externo en `research_only`; no habilita promociones.

## Ejecución

```bash
python scripts/run_phase_33_prematch_input_materialization.py
python scripts/run_phase_32_prematch_candidate_preparation.py
```

## Gates

- Features y contexto alineados con el kickoff del candidato.
- Cero IDs prospectivos usados como historial previo.
- Cero violaciones temporales.
- `target_match_data_used=False` y `target_match_statistics_used=False`.
- Cero predicciones, targets, cambios del router o mercados promovidos.

## Resultado actual

La ejecución actual queda en espera porque Fase 31 aún no tiene una cohorte
independiente. Cuando aparezcan candidatos, Fase 33 publicará sus artefactos
específicos y Fase 32 los preferirá sobre los artefactos históricos.

## Siguiente paso

Ejecutar Fases 31, 33 y 32 después de cada sincronización ESPN. Sólo con la
preparación causal completa se puede abrir el paquete de predicciones para la
evaluación confirmatoria.
