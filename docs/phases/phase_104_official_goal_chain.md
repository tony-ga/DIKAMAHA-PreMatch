# Fase 104 — cadena oficial de goles

## Objetivo

Reemplazar la etiqueta ficticia de Dixon-Coles + Kalman por una ruta de
inferencia realmente ejecutada, causal, versionada y promovida por mercado.

## Arquitectura candidata

```text
snapshot histórico anterior al kickoff
  -> Dixon-Coles MLE con decaimiento temporal
  -> estado inicial Kalman
  -> replay Kalman cronológico por kickoff
  -> matriz de marcador con corrección Dixon-Coles
  -> 1X2, over 2.5 y ambos marcan
  -> sidecar Markov de trayectoria no promovido
```

## Controles

- El partido objetivo nunca entra al ajuste ni al replay.
- Partidos con el mismo kickoff se predicen antes de cualquier actualización.
- Cada mercado mantiene fallback exacto al baseline universal.
- La procedencia indica por separado Dixon-Coles, Kalman y Markov.
- Telegram muestra únicamente los componentes realmente usados.
- Markov de goles no puede promoverse bajo la evidencia clausurada por
  `DEC-100`.

## Gate de promoción

Cada mercado debe cumplir en evaluación walk-forward:

1. mejora media positiva de log-loss frente al baseline;
2. límite inferior del IC95% pareado mayor que cero;
3. Brier no degradado;
4. al menos 70% de ligas elegibles no degradadas;
5. replay determinista y causalidad completa.

Si un mercado falla, su salida oficial continúa siendo el baseline. La
activación nunca es monolítica.

## Entregables

- puerto abstracto para el modelo oficial de goles;
- implementación Dixon-Coles/Kalman causal;
- router por mercado y procedencia tipada;
- evaluación walk-forward reproducible;
- integración API y Telegram;
- rollback por configuración;
- pruebas de causalidad, fallback y paridad.

## Estado

`selective_official`.

- 500 partidos y 31 ligas evaluados walk-forward.
- 1X2 aprobado.
- Over 2.5 aprobado.
- Ambos marcan conserva baseline por estabilidad insuficiente.
- Markov de goles permanece shadow bajo `DEC-100`.
- 45 pruebas dirigidas aprobadas.

Artefactos:
`artifacts/phase_104_official_goal_chain/`.
