# Fase 106 — reparación probabilística selectiva

## Objetivo

Corregir la sobreconfianza BTTS y retirar únicamente la línea Markov que
degrada en todas las métricas relevantes.

## Gate BTTS

- warm-up causal de 200 partidos;
- 800 predicciones prequential;
- tasa BTTS causal por liga con prior `0.50` y shrinkage seleccionado
  exclusivamente en el warm-up;
- log-loss, Brier y ECE no degradados;
- IC95% pareado de mejora de log-loss mayor que cero;
- al menos 70% de ligas no degradadas;
- parámetros finales sellados después de cerrar el gate.

Se descartó Platt porque el ajuste aprendido invertía el ranking de la señal
estructural. La reparación aprobada no usa el resultado del partido objetivo:
estima la tasa de BTTS de su liga únicamente con partidos anteriores al
kickoff y la contrae hacia `0.50` con shrinkage `500`.

## Fallback Markov

`home_corners_second_half_over_2_5` usará exactamente su probabilidad baseline.
No se reentrena ni se selecciona una línea sustituta con Fase 105.

## Estado

`selective_integrated`.

## Evidencia

- 800 predicciones prequential posteriores al warm-up;
- log-loss `0.874028 → 0.691966`;
- Brier `0.302916 → 0.249410`;
- ECE `0.185686 → 0.016445`;
- IC95% pareado de mejora de log-loss:
  `[0.129208, 0.237257]`;
- 19 de 21 ligas no degradadas (`90.48%`);
- replay completo de 1,000 partidos:
  accuracy `60.29%`, log-loss `0.692561` y Brier `0.266393`;
- fallback exacto verificado para
  `home_corners_second_half_over_2_5`;
- 38 pruebas dirigidas aprobadas.

La evidencia es histórica y causal. No acredita ROI ni sustituye una
validación prospectiva contra cuotas de cierre.
