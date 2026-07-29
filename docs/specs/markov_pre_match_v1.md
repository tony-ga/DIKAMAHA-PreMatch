# Especificación `markov_pre_match v1`

## Propósito

Estimar y simular, antes del kickoff, trayectorias de estados tácticos a partir de patrones históricos de ventanas de 15 minutos. No consume eventos del partido objetivo.

## Estado

La primera versión debe limitarse a cuatro estados: `equilibrio`, `presion`, `repliegue` y `desorganizacion`. Los eventos son observaciones; no son estados.

## Transición

La unidad de entrenamiento es `S_t -> S_t+1` dentro del mismo partido y equipo:

`P(S_t+1 | S_t, ventana, diferencia_gol, localia, fuerza_relativa, estado_rival, perfil_equipo)`.

`fuerza_relativa` proviene de Dixon-Coles/Kalman disponible antes del partido cuando exista un artefacto canónico por match. La calibración inicial puede omitirla explícitamente; los estados se etiquetan con información de la ventana actual y el estado siguiente es target.

## Cobertura y backoff

Aplicar en orden: equipo+localía+ventana+marcador, equipo+localía+ventana, competición+localía+ventana y prior global. Cada predicción debe guardar el nivel de backoff, soporte efectivo y versión de parámetros.

La implementación v1 refina esos contextos con el estado propio y el estado rival observados en `t`. Usa un prior jerárquico Dirichlet de fuerza total `alpha=32` (ocho pseudo-observaciones por estado) y sólo expone un nivel específico cuando alcanza el soporte configurado. El orden aplicado es `equipo → contexto competición → ventana+estado → estado global`.

## Prohibiciones

- No usar marcador final ni eventos del partido objetivo.
- No usar porcentajes manuales como matriz final.
- No promocionar matrices sintéticas.
- No tratar ventanas como observaciones IID de evaluación.

## Salida

Una distribución inicial de estados y matrices condicionadas, versionadas y auditables, aptas para `pre_match_simulation v1`.
