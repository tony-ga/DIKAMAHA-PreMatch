# Fase 84A — mercados agregados de corners, tarjetas y tiros

## Objetivo

Añadir mercados pre-match agregados sin modificar la cadena de goles vigente.

## Fuente

Corpus causal de Fase 74, resolución 15 minutos. Los targets son sumas
post-match por equipo; las features rolling se congelan antes de cada kickoff.
La versión vigente define tiros comerciales como eventos de tiro más goles;
los goles también suman a tiros a puerta, en paridad con ESPN.

## Targets primarios

- corners por equipo y total;
- amarillas por equipo y total;
- presencia de tarjeta roja;
- tiros por equipo y total;
- tiros a puerta por equipo y total.

## Modelos y comparadores

- baseline liga × localía con smoothing;
- regresión Poisson regularizada sobre perfiles rolling de equipo/rival;
- selección de regularización sólo en `selection`;
- reajuste final con `fit+selection` y evaluación única en `confirmation`.

## Gates

- cobertura completa y targets no negativos;
- cero features del partido objetivo;
- MAE y Poisson deviance no peores que el baseline;
- log-loss/Brier no peores en las líneas publicadas;
- mejora no negativa en al menos 70% de ligas con soporte;
- replay idéntico.

Hasta superar todos los gates, las salidas son `experimental_shadow_not_promoted`.

## Fuera de alcance

Props de jugador. Fase 84B debe demostrar identidad estable, minutos,
titularidad y snapshots de alineación causales antes de entrenarlos.

## Resultado

`ready_for_next_phase`

- 9,465 partidos fuente, 1,895 confirmation y 33 ligas.
- 18,930 observaciones orientadas con features estrictamente anteriores.
- Conteos aprobados: corners, corners 1T, amarillas, amarillas 1T, tiros y
  tiros a puerta.
- Conteo bloqueado: rojas, por degradación de MAE.
- Líneas habilitadas en shadow:
  - corners local over 4.5;
  - corners visitante over 4.5;
  - tiros local over 10.5;
  - tiros visitante over 10.5;
  - tiros a puerta totales over 7.5.
- Las otras seis líneas permanecen desactivadas por no superar simultáneamente
  log-loss, Brier y el gate del conteo padre.
- Replay idéntico; modelos serializados y router de goles intacto.
- Regresión integral con PostgreSQL: `368 passed`.
- Props de jugador: `blocked_by_data`.

La siguiente fase permitida es integrar únicamente las cinco líneas aprobadas
al flujo universal en modo shadow y ejecutar una cohorte prospectiva.
