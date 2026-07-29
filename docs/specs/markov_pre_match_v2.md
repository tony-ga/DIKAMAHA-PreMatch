# Especificación `markov_pre_match v2`

## Propósito

Conservar la dinámica táctica Markov de 15 minutos e incorporar una intensidad de gol estructural disponible antes del kickoff para cada partido.

## Intensidad estructural

Para cada equipo y partido, el proveedor canónico recibe `lambda_dc` y `lambda_kalman` generados antes del kickoff. La v2 fija:

`lambda_base = 0.80 * lambda_dc + 0.20 * lambda_kalman`.

La ponderación no se ajusta con el bloque de confirmación. Es un parámetro versionado que será evaluado en un protocolo OOS nuevo.

## Papel de Markov

Markov no cambia la masa total `lambda_base`. La distribuye entre las seis ventanas en proporción a pesos históricos de estado, localía y ventana. Cada trayectoria conserva la intensidad total prevista por equipo.

## Prohibiciones

- No usar eventos, marcador, estadísticas o resultado del partido objetivo.
- No reutilizar métricas o resultados de Fase 05 para elegir pesos.
- No promover v2 sin una evaluación OOS independiente.

## Salida

Una trayectoria por partido con `lambda_dc`, `lambda_kalman`, `lambda_base`, pesos temporales y hashes de ambos priors.
