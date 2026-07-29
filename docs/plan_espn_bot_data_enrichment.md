# Plan — enriquecimiento ESPN para bots y contexto pre-match

## Propósito

Convertir la documentación ESPN disponible en capacidades visibles para
Telegram/Discord y en candidatos causales para el pipeline pre-match, sin
alterar el router oficial ni introducir datos posteriores al cutoff.

## Principios

- Persistir cada respuesta raw antes de normalizarla.
- Asociar `source_fetched_at`, endpoint, parámetros, hash y versión de parser.
- Separar `display_only`, `prematch_candidate`, `live_only` y
  `settlement_only`.
- No transformar una observación sin timestamp verificable en feature.
- Mantener odds, CLV, ROI y staking fuera de esta ruta hasta Fase 83.
- Mostrar mercados secundarios únicamente como `experimental_shadow`.

## Objetivo 100A — contexto visible de partido

Integrar en ambos bots: estado, kickoff, competición/fase, estadio/ciudad,
capacidad, árbitros, transmisiones, escudos, colores y enlace de resumen.

**Éxito:** identidad de fixture reconciliada; datos opcionales con fallback;
mensajes bajo el límite de cada plataforma; raw-first y pruebas HTTP.

## Objetivo 100B — contexto de equipo y competición

Integrar: standings, puntos, GF/GC, diferencia, forma, récord local/visitante,
calendario, días de descanso, tabla de líderes, fases/grupos/bracket y ficha
de equipo.

**Éxito:** tabla de posiciones por liga; calendario causal; forma calculada
sólo con partidos anteriores; cero consulta post-kickoff para predicción.

## Objetivo 100C — disponibilidad y jugadores

Integrar: lesiones, suspensiones si ESPN las publica, estado activo, roster,
posición, pie dominante, perfil, fotos y acumulados de temporada. La
formación/titulares sólo se mostrarán o usarán si su snapshot es previo al
kickoff.

**Éxito:** identidad jugador-equipo estable; ausencia explícita cuando ESPN no
publica el dato; timestamps de disponibilidad; sin props promocionados.

## Objetivo 100D — candidatos pre-match

Evaluar, sin promoción automática: descanso, congestión, forma local/visitante,
standings, disponibilidad, árbitro histórico, estadio y fase competitiva.

**Éxito:** dataset snapshot-causal; ablation walk-forward por partido; mejora
confirmada contra baseline o descarte documentado. Dixon-Coles/Kalman siguen
siendo la única salida oficial hasta superar sus gates vigentes.

## Objetivo 100E — datos live y settlement

Usar play-by-play, situation, probabilidades live y estadísticas finales sólo
para interfaz live futura, auditoría, labels y settlement. Conservar la
reconciliación `1T + 2T = total` y `goles PBP = marcador oficial`.

**Éxito:** paginación completa, hash raw-first, rechazo explícito de
discrepancias y ninguna lectura live como feature de un partido pre-match.

## Objetivo 100F — noticias y cuotas

Mostrar noticias y highlights como contexto editorial, con timestamp, fuente y
etiqueta no-modelo. Archivar odds con bookmaker, línea, timestamp y mercado
solamente para la futura Fase 83; no exponer staking ni ventaja económica.

**Éxito:** noticias no convertidas en feature sin pipeline NLP causal; odds
separadas del router, de la calibración oficial y de la interfaz de apuesta.

## Orden de ejecución

1. 100A y 100B: valor visible alto y riesgo bajo.
2. 100C: identidad/disponibilidad, prerequisito de jugador.
3. 100D: evaluación causal, no integración directa.
4. 100E: robustez live/settlement.
5. 100F: archivo editorial y financiero aislado.

## No objetivos

- No crear predicciones in-play en esta fase.
- No usar odds como feature o recomendación de apuesta.
- No promocionar Markov, Hawkes ni props de jugador.
- No inferir lesiones, alineaciones o transferencias cuando ESPN no las
  publique con un timestamp usable.
