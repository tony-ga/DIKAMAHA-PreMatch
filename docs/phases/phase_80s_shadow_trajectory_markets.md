# Fase 80S — mercados de trayectoria shadow

## Objetivo

Conservar la funcionalidad secuencial del simulador pre-match sin confundirla
con promoción estadística.

## Salidas

- intervalo del primer gol, incluido `none`;
- número de ventanas de 15 minutos con al menos un gol;
- al menos dos ventanas consecutivas con gol;
- clustering de dos o más goles en una ventana;
- segunda mitad con más ventanas activas.

## Contrato

- clasificación obligatoria `experimental_shadow_not_promoted`;
- probabilidades categóricas normalizadas dentro de `1e-9`;
- replay idéntico;
- conservación de lambdas de Fase 79;
- cero lecturas posteriores al cutoff;
- router oficial intacto.

Esta fase no desbloquea Fase 81.

## Resultado

`validated`

Los modos contextual y core generan cinco mercados secuenciales con 5,000
trayectorias reproducibles. El error de normalización categórica es `0`, el
error máximo de conservación es `6.661e-16`, las lecturas post-cutoff son cero
y la salida está forzada a `experimental_shadow_not_promoted`.
