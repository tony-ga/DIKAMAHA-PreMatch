# Especificación `pre_match_simulation v1`

## Propósito

Simular, antes del kickoff, trayectorias de seis ventanas para ambos equipos y agregar resultados a probabilidades de mercados.

## Entradas congeladas

- Fuerzas e intensidades Dixon-Coles/Kalman disponibles al cutoff.
- Distribución inicial y transiciones de `markov_pre_match v1`.
- Identidad, localía y competición del partido objetivo.
- Configuración de simulación, semilla y versión de artefactos.

Mientras no exista el artefacto canónico de intensidades por partido, v1 permite un emisor histórico de goles condicionado por estado, ventana y localía. Debe declararse explícitamente como `historical_state_emission` y nunca como intensidad Dixon-Coles/Kalman.

## Salidas iniciales

- Distribución de estados por ventana.
- Goles esperados y 1X2.
- Mercados de goles validados.
- Mercados de corners, tarjetas y tiros sólo cuando tengan target, cobertura y evaluación propios.

## Invariantes

- Misma entrada, semilla y artefactos producen la misma salida.
- Cada trayectoria conserva la orientación local/visitante.
- Las probabilidades agregadas son finitas, no negativas y normalizan.
- Toda predicción incluye hashes de sus priors y matrices.
- Una salida que use emisiones históricas se marca `experimental_not_promoted`.
