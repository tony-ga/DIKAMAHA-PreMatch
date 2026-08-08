# Fase 114 — plan verificable de implementación

Implementar una ruta in-play shadow que conserve el prior pre-match y separe
tres salidas auditables:

1. `markov_live_v1` actualiza régimen, goles restantes y próximo evento con
   marcador, reloj y eventos observados hasta el snapshot.
2. `hawkes_live_v2` calcula únicamente un residual de memoria corta.
3. `combined_live` aplica
   `log(lambda_combined)=log(lambda_markov)+rho_c*residual_hawkes`, donde
   `rho_c` se selecciona por objetivo y nunca sustituye el estado Markov.

La ruta anterior `/v1/predict/live` debe mantener su comportamiento por
defecto. Los bloques nuevos requieren flags shadow coherentes y están
prohibidos en predicciones oficiales. `rho_goal=0` y
`rho_next_event=0` reproducen exactamente Markov Live en su objetivo, y la
ausencia de Hawkes no altera su salida.

La captura ESPN debe consultar scoreboard/event/plays/situation sin cache live,
persistir cada respuesta antes de parsearla, conservar paginación y fallback a
summary, normalizar reloj/periodo/marcador y rechazar eventos futuros o un
marcador que no reconcilie con el play-by-play. Site API mantiene
`site.api.espn.com` como primario y reintenta una vez el mismo recurso en
`site.web.api.espn.com` sólo ante HTTP 403; Core no cambia de host y toda
respuesta conserva la URL efectiva en provenance.

El gate técnico exige replay determinista, probabilidades normalizadas,
intensidades finitas, Hawkes subcrítico, fallback exacto, compatibilidad HTTP,
pruebas unitarias/integrales y cero cambios en el router pre-match oficial.

La validación no espera una cohorte prospectiva nueva. Usa PostgreSQL en modo
read-only y reconstruye pseudo-live sobre al menos 5.000 partidos históricos
reconciliados y 20 ligas. Los priors sólo incorporan partidos con kickoff
estrictamente anterior; partidos con el mismo kickoff se actualizan como un
bloque atómico. Desarrollo selecciona el estado Markov, validación selecciona
`rho_goal` y `rho_next_event`, y confirmación se puntúa una sola vez. El
bootstrap agrupa snapshots por partido y la robustez exige al menos 70% de
ligas no degradadas.

Resultado actual: 7.400 partidos/34 ligas. Markov supera confirmación y queda
como baseline live. Hawkes global mejora agregado en goles con `rho_goal=1` y
conserva próximo evento Markov con `rho_next_event=0`, pero sólo 59,375% de
ligas no degradan. Una allowlist congelada con los 1.586 partidos de validación
admite 17 ligas; aplicada una sola vez a confirmación obtiene delta objetivo
`-0,000398`, IC95% `[-0,000650, -0,000135]`, y 84,375% de ligas no degradadas.
Fuera de la allowlist, o para próximo evento, `rho_c=0` mantiene fallback
Markov exacto. La promoción queda fuera de esta implementación.
