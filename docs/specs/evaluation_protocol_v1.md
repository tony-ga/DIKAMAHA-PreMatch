# Protocolo de evaluación `evaluation_protocol v1`

## Diseño

Usar ventanas temporales expansivas y separar desarrollo, validación y confirmación por partido completo. Las ventanas internas de un partido nunca se remuestrean como IID.

## Comparadores obligatorios

1. Baseline simple.
2. Dixon-Coles.
3. Dixon-Coles + Kalman.
4. Markov global.
5. Markov dependiente.

Hawkes sólo se añade como comparador posterior, no como requisito inicial.

## Métricas

- Log score y Brier para probabilidades categóricas.
- Calibración y curva de confiabilidad.
- MAE/log score para goles cuando corresponda.
- Métricas específicas para corners, tarjetas y tiros sólo en su mercado.
- Bootstrap agrupado por partido sobre el bloque confirmatorio.

## Gate de promoción

Promover únicamente si el bloque confirmatorio mejora el baseline correspondiente, el intervalo de confianza no contradice la mejora, la cobertura es suficiente y no hay violaciones temporales o de calidad.
