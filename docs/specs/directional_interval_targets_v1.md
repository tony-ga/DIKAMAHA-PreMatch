# Especificación `directional_interval_targets v1`

## Unidad

Una observación por partido e intervalo de 15 minutos. La unidad IID para
scoring, bootstrap y partición continúa siendo el partido completo.

## Target

La clase conjunta se deriva después del partido:

- `neither`: ningún equipo marca;
- `home_only`: marca sólo el local;
- `away_only`: marca sólo el visitante;
- `both`: marcan ambos.

Los seis intervalos son `[0,15)`, `[15,30)`, `[30,45)`, `[45,60)`,
`[60,75)` y `[75,90+]`. Los targets binarios `any_goal`, `home_scores` y
`away_scores` son proyecciones evaluables, no features.

## Contrato pre-match

- Ningún evento, marcador o estadística del partido objetivo entra a features.
- Los perfiles de equipo y liga se congelan antes de cada kickoff.
- Después de emitir features y predicción, el partido completo puede actualizar
  el historial para partidos posteriores.
- Los labels se almacenan separados de las matrices de inferencia.

## Evaluación

- Log-loss multiclase, Brier multiclase y ECE.
- Log-loss de proyecciones `any_goal`, `home_scores` y `away_scores`.
- Métricas agregadas primero por partido y después por cohorte.
- Modelo, hiperparámetros y calibración se eligen únicamente en `selection`.
- `confirmation` no participa en ajuste ni selección.

## Gate

Probabilidades finitas, en `[0,1]` y normalizadas dentro de `1e-9`; replay de
métricas dentro de `1e-6`; targets físicamente separados del paquete de
inferencia; cero solapamiento de partidos entre splits.

# Version: 1.0.0
# Created: 2026-07-27
