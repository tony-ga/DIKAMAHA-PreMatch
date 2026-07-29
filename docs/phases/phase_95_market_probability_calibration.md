# Fase 95 — calibración causal de mercados

## Objetivo

Calibrar las nueve probabilidades de Fase 94 sin cuotas externas y sin usar el
outcome del partido objetivo antes de emitir su probabilidad.

## Protocolo congelado

- ordenar los 500 partidos por kickoff e ID;
- reservar los primeros 100 como warm-up;
- ajustar un Platt calibrator independiente por mercado;
- para cada partido posterior, usar únicamente partidos anteriores;
- revelar el outcome sólo después de emitir la probabilidad calibrada;
- puntuar los 400 partidos restantes como unidades completas.

## Gate

- 400 partidos y 3,600 decisiones;
- cero targets futuros en el ajuste;
- log-loss y Brier globales no empeoran;
- ECE global no empeora;
- IC95% pareado de mejora de log-loss publicado;
- ninguna modificación del router ni declaración económica.

## Clasificación permitida

`validated` si los controles causales y de cobertura pasan. La calibración
puede recomendarse por línea, pero no constituye promoción ni betting edge.

## Resultado

Clasificación: `validated`.

- 400 partidos y 3,600 decisiones;
- log-loss 0.659177 → 0.644836;
- Brier 0.232875 → 0.226793;
- ECE 0.057422 → 0.032275;
- IC95% de mejora log-loss [0.007502, 0.020872];
- cinco líneas recomiendan Platt y cuatro conservan probabilidad raw;
- replay idéntico, router sin cambios y suite integral 404/404.
