# Fase 113 — Auditoría integral de modelos predictivos

Estado: `validated_selective`.

## Objetivo

Reauditar la cadena matemática completa, corregir desviaciones de fórmula y
causalidad, endurecer las fronteras fail-closed y volver a medir cualquier
resultado histórico afectado.

## Alcance

- Dixon-Coles y sus consumidores Kalman;
- splits temporales y recorridos prequential de mercados de equipo;
- métricas de marcador exacto;
- distribuciones Poisson y binomial negativa;
- carga e integridad de artefactos;
- validación del gate de promoción y fallbacks oficiales;
- revisión de reportes experimentales no canónicos.

## Gates

1. Fórmulas exactas y pruebas de regresión aprobadas.
2. Cero kickoffs compartidos entre splits.
3. Invariancia de predicciones al reordenar partidos del mismo kickoff.
4. Historia oficial estrictamente anterior al cutoff y convergencia obligatoria.
5. Probabilidades y PMF finitas, acotadas y normalizadas.
6. Hashes de todos los componentes del artefacto verificados.
7. Suite integral aprobada y evidencia histórica regenerada.

Los siete gates están cerrados. Las cifras anteriores afectadas quedan
reemplazadas por los artefactos regenerados; no se amplía el conjunto de
mercados promovidos.

## Resultado

- Dixon-Coles usa la orientación canónica `x=home_goals`, `y=away_goals`.
- Los 1,405 kickoffs simultáneos del corpus se procesan por lotes; 3,884 filas
  que antes podían ver un resultado simultáneo ya no lo hacen.
- Las fronteras compartidas de Fase 104 bajaron de 27 a cero y 45 cold starts
  quedaron fuera del gate comparativo.
- Los ocho manifiestos de artefactos auditados verifican todos sus hashes.
- La cadena oficial mantiene 1X2 y over 2.5; BTTS permanece en la reparación
  causal de Fase 106.
- El runtime expone ocho mercados de equipo sólo como shadow, con PMF,
  complementos y monotonicidad validados.
- Fase 105 se reemitió con 1,000 partidos, 11,000 decisiones y Brier
  normalizado por evento; el Brier crudo entre familias incompatibles se
  suprime.
- Suite integral: 485 aprobadas, 8 integraciones opcionales omitidas y una
  advertencia externa de deprecación.
- Ninguna prueba autoriza ROI, CLV, Kelly, stakes o combinadas.

## Revisión operativa v1.1 — hashes portables

El despliegue Linux reveló dos diferencias entre el gate local y la imagen
mínima: Git materializaba JSON sellados con LF aunque el manifiesto se había
calculado sobre CRLF, y los manifiestos científicos enumeraban evidencia que
el runtime no consume ni empaqueta. Esto forzaba `shadow_unavailable` en los
mercados de equipo y fallback BTTS pese a que los modelos eran válidos.

La corrección verifica todos los archivos requeridos por cada proveedor,
ignora sólo entradas de evidencia no ejecutables y acepta exclusivamente la
normalización LF/CRLF en archivos de texto conocidos. Los binarios y cualquier
cambio de contenido continúan fail-closed.

La imagen de producción reconstruida ejecutó Cambridge United–Barnet con 8
líneas, 21 grupos distribucionales y los periodos `first_half`, `second_half`
y `full_match`. El presentador produjo tarjeta y dashboard con probabilidades
visibles. Regresión integral: 522 aprobadas y 8 omitidas.

## Artefactos sellados

`artifacts/phase_113_model_integrity_audit/` contiene configuración,
manifiesto de entradas, cobertura, auditoría, métricas, reporte de validación,
reporte final y hashes SHA-256.
