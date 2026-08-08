# Fase 114 — reporte de implementación

## Resultado

La infraestructura de predicción live quedó implementada en modo `shadow`.
Markov Live es la referencia causal de estado, marcador y tiempo restante;
Hawkes v2 sólo aporta un residual de memoria corta, acotado y subcrítico. La
combinación se realiza en escala logarítmica y `rho=0` reproduce exactamente
la salida Markov Live.

Clasificación: `historically_validated_markov_and_selective_hawkes_shadow`.

## Alcance entregado

- polling fresco de scoreboard, event, plays y situation de ESPN;
- persistencia raw-first append-only antes de normalizar;
- aislamiento de fallos por fixture y deduplicación determinista;
- reloj period-aware, identidad home/away y reconciliación score/PBP;
- mercados 1X2, over 2.5 y BTTS sobre goles restantes;
- hazards de próximo evento con masa explícita de `no_event`;
- bloques API independientes para Markov, residual Hawkes y combinación;
- runner multiliga gradual con política Hawkes por liga seleccionada sólo en
  validación;
- compatibilidad de la ruta live heredada y router oficial intacto.

## Evidencia técnica

La suite integral terminó con 519 pruebas aprobadas y 8 omitidas de manera
explícita. `py_compile` y `git diff --check` pasaron. Un replay sintético
repitió exactamente los hashes Markov y combinado; el radio espectral Hawkes
fue `0.31428571428571433`, por debajo de uno.

El transporte detectó que `site.api.espn.com` era bloqueado con HTTP 403 por
Akamai. El fallback acotado `site.web.api.espn.com` devolvió scoreboard y
summary y standings con HTTP 200; Core devolvió event y 206 plays con HTTP
200. La URL efectiva queda en provenance y el runner cerró sin errores. El
parser también conserva el descuento publicado como `90'+N'` aunque el reloj
numérico ESPN permanezca limitado a 5,400 segundos.

## Gate histórico

PostgreSQL se leyó sin escrituras: 9,649 partidos de regulación reconciliados,
7,400 elegibles y 34 ligas. Los bloques temporales contienen
4,417/1,586/1,397 partidos y no comparten kickoffs.

Markov mejoró frente a score/tiempo en `-0.002259`, IC95%
`[-0.002858, -0.001635]`, con 84.375% de ligas no degradadas. Hawkes global
mejoró `-0.000648`, pero sólo alcanzó 59.375% de ligas. La allowlist elegida
con validación admitió 17 ligas; en confirmación Hawkes selectivo mejoró
`-0.000398`, IC95% `[-0.000650, -0.000135]`, y alcanzó 84.375% de ligas.

Markov y Hawkes selectivo superan sus gates históricos. Próximo evento y ligas
no admitidas usan fallback Markov exacto. Dos ejecuciones reprodujeron el hash
`c926fd712c596e4d475856cf6259db766cbb1f950a83e0d6e2da7bad47612b53`.
Ninguna salida se activó en router, bots, apuestas, ROI o Kelly.
