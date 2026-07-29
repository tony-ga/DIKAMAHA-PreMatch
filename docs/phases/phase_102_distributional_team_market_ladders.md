# Fase 102 — escaleras distribucionales por equipo

## Objetivo

Exponer, antes del kickoff, la distribución completa de corners, tiros,
tiros a puerta y tarjetas por equipo y periodo, y derivar de ella líneas
over/under coherentes y una selección compacta de escenarios probables.

## Contrato

Por `equipo × métrica × periodo`:

- conteo esperado;
- conteo exacto modal;
- distribución de probabilidad de conteos;
- escalera over/under;
- probabilidad baseline con los mismos datos;
- soporte y procedencia;
- estado `experimental_shadow_not_promoted`.

Los periodos son `first_half`, `second_half` y `full_match`. Cada probabilidad
under es el complemento exacto del over correspondiente.

## Modelo y baseline

- modelo: cadena Fase 88 condicionada por equipo, liga y localía;
- baseline: la misma cadena y emisiones sin identidad de equipo, con pooling
  liga × localía;
- tiros a puerta: modelo de conteos negativo-binomial Fase 84A por equipo y
  total de partido; no se inventan mitades sin modelo causal disponible;
- ambos usan únicamente historia anterior al kickoff;
- tiros conserva semántica comercial `shots + goals`;
- el target match permanece excluido.

## Líneas

- corners: medias líneas desde 0.5 hasta un máximo plausible por periodo;
- tiros: medias líneas desde 0.5 hasta un máximo plausible por periodo;
- tarjetas: medias líneas desde 0.5 hasta un máximo plausible por periodo;
- tiros a puerta: medias líneas de partido completo por equipo y total;
- el soporte observado de la distribución nunca se trunca para calcular masa.

## Selección visible

La API conserva la escalera completa. Los bots muestran sólo escenarios:

- con probabilidad no trivial;
- coherentes y monotónicos;
- con máximo una recomendación por equipo, métrica y periodo;
- ordenados por probabilidad, sin lenguaje de apuesta ni rentabilidad;
- con modelo y baseline visibles.

La revisión v1.1 añade una rejilla informativa por
`equipo × métrica × periodo`:

- exactamente tres medias líneas consecutivas;
- rango visible cerrado entre 1.5 y 9.5;
- centro seleccionado por cercanía de `P(over)` a 50%;
- over y under simultáneos, con sus respectivos baselines;
- primer tiempo, segundo tiempo y partido completo separados.

Esta política modifica únicamente qué cortes de la PMF se muestran. No
reentrena ni altera la distribución causal.

## Totales globales

Al final del dashboard se muestra una línea global informativa por corners,
tiros, tarjetas y tiros a puerta de partido completo. Corners, tiros y tarjetas
se agregan mediante convolución de las PMF local y visitante bajo independencia
condicional declarada. Tiros a puerta reutiliza su PMF total negativa-binomial.
Nunca se agregan sumando probabilidades individuales.

La revisión v1.3 reemplazó la convolución para el resumen visible: los cuatro
totales usan ahora la distribución negativa-binomial directa de Fase 84A,
estimada con historia causal de equipo, rival y liga. El dashboard comunica
media, moda e intervalo central 60%, evitando que una línea máxima artificial
sature probabilidades de tiros.

## Gate técnico

- suma de la PMF igual a uno;
- `P(over x)` no aumenta al subir la línea;
- `P(over) + P(under) = 1`;
- replay idéntico;
- salida oficial bit a bit intacta;
- cutoff causal y target excluido;
- Telegram y Discord dentro de sus límites.

## Promoción

Esta fase es de contrato e inferencia shadow. No promueve mercados. Una futura
evaluación debe puntuar líneas predefinidas por partido completo, con
walk-forward, Brier, log-loss, calibración y bootstrap antes de cualquier uso
económico.
