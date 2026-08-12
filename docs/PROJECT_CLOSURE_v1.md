# Cierre del proyecto DIKAMAHA PreMatch v1

**Fecha:** 2026-08-12
**Estado:** producto en producción, cerrado como entregable; investigación archivada.

## Por qué existe este documento

El roadmap de DIKAMAHA mezclaba dos pistas: un producto ya desplegado (motor
pre-match, bot, Mini App, track record) y un programa de investigación
(Markov v4) que llevaba ~15 iteraciones sin confirmarse. El usuario decidió
cerrar el producto actual con éxito en vez de seguir persiguiendo la
promoción de Markov v4 (ver `docs/decision_log.md` DEC-170). Este documento es
la única fuente que hace falta leer para entender qué quedó activo, qué
quedó archivado y por qué, sin releer ~2,900 líneas de bitácora de
decisiones.

No reemplaza `docs/00_roadmap_actual.md` ni `docs/status.md` -siguen siendo
la fuente operativa fase por fase-, pero resume el resultado final de
cerrarlo.

## Qué está activo en producción, con evidencia verificada

Los cinco servicios Railway del proyecto `heroic-motivation`
(`DIKAMAHA-PreMatch`, `dikamaha-premium-telegram-bot`, `telegram-miniapp`,
`telegram-alert-worker`, `Postgres`) reportan `SUCCESS` a 2026-08-12.

- **Motor pre-match oficial** (Dixon-Coles/Kalman, Fase 113): 1X2 y Over 2.5
  oficiales; BTTS reparado por Fase 106. Ocho mercados de equipo en shadow.
- **Motor live compuesto** (Fase 116): confirmado sirviendo en producción el
  2026-08-12 vía `GET /v1/health` -> `live_probability_engine_official: true`.
  La documentación anterior lo daba por "pendiente de despliegue"; era un
  error de estado, no de código (ver DEC en `docs/status.md`, sección Fase
  116, "Corrección de estado").
- **Catálogo de 63 ligas y torneos** (Fase 120): activo, snapshot
  `phase160_recent_topup_v1_20260811`.
- **Mini App** (Fase 115): desplegada, paridad funcional con el bot,
  `/api/health` responde `ready` con PostgreSQL conectado.
- **Bot Telegram premium** (Fase 109): desplegado, acceso privado por
  allowlist.
- **Menú de mayor probabilidad** (Fase 122): desplegado. Evidencia histórica
  post-hoc únicamente -el gate v2 que aprueba sus 9 celdas se re-especificó
  después de ver el resultado de v1-, no confirmación prospectiva
  independiente todavía.
- **Validación prospectiva del menú** (Fase 123, nueva): implementada y
  **verificada corriendo en producción** el 2026-08-12 -primer ciclo real
  completó sin error (`phase123_cycle_completed`) tras corregir un bug de
  timeout preexistente en `/v1/high-probability` (ver más abajo). Corre
  dentro del mismo proceso que ya publica en el canal de Telegram, sin
  infraestructura nueva. La cohorte prospectiva real empieza a acumularse a
  partir de este despliegue: `total_frozen`/`total_settled` arrancan en cero.
- **Historial de aciertos verificable** (Fases 118/121): infraestructura
  lista, ledger migrado a PostgreSQL (DEC-164) tras dos incidentes de
  producción por SQLite efímero. `prediction_settlements` recién puede
  empezar a acumular una muestra real de forma confiable.
- **Mercados de equipo en shadow** (84A, 85, 88, 89, 90, 93): sirviendo
  como mercados experimentales y fallback exacto en la Mini App y el bot; no
  oficiales, pero funcionando como shadow por diseño.
- **Markov Live / Hawkes residual** (Fase 114): validado históricamente,
  integrado en producto como shadow; Hawkes selectivo en 17 ligas admitidas,
  fallback Markov exacto en el resto.

## Qué quedó archivado, y por qué

Ver `docs/decision_log.md` DEC-170 (texto completo).

- **Fases 73, 81, 82, 83** (recolección prospectiva, confirmación
  independiente, integración oficial y validación de valor de apuesta de
  Markov v4): archivadas sin trabajo activo ni fecha objetivo. Fase 73 tenía
  solo 60 filas/5 fixtures de una cohorte que necesita 500/10 ligas para
  Fase 81, recolectada manualmente sin automatización. Tras ~15 iteraciones
  rechazadas (Fases 76-80U, cerradas por DEC-100), seguir no tenía fecha de
  cierre realista.
- **Fase 84B** (mercados de jugador): archivada por la misma razón
  estructural -no existe fuente de datos causal de alineación/minutos/
  atribución de eventos-, no por decisión de negocio.
- Esto **no** modifica ni reabre DEC-100 (que ya cerró el tuning
  retrospectivo); lo extiende declarando que tampoco se sigue invirtiendo en
  generar la cohorte prospectiva que Fase 81 necesitaría.
- Una reapertura futura requiere una decisión explícita nueva con cohorte
  independiente, exactamente como ya exigía DEC-100. El código de Markov v4/
  Hawkes en shadow **no se tocó** -sigue funcionando igual que antes de
  archivarse.

## Qué queda fuera de alcance por diseño (no es "pendiente")

- **ROI, Kelly, CLV, staking** (Fase 83): bloqueados permanentemente hasta
  que haya probabilidades promovidas y cuotas históricas comparables. El
  proyecto declara explícitamente que no es asesoría financiera
  (`GUIA_USO_SOPORTE_GRUPO_PRIVADO.txt`: *"DIKAMAHA no publica stakes,
  Kelly, ROI ni ejecución de apuestas"*). Esto se mantiene cerrado a
  propósito, no se "completa".
- **Liga MX Femenil, K League**: excluidas del catálogo porque el proveedor
  ESPN no expone esas referencias. No es solucionable desde el proyecto.

## Incidentes reales encontrados y corregidos durante este cierre

Ninguno de los dos fue introducido por el trabajo de cierre; ambos ya
existían y fueron detectados al verificar el estado real del sistema.

1. **Reporte de apuestas no autorizado** (`REPORTE_COMPLETO_FIABILIDAD.*`,
   DEC-169): un script y su reporte generado (ROI por mercado, estrategias
   de parlay, "apostar siempre") existían sin trackear en git y sin ninguna
   entrada en `status.md`/`decision_log.md`, contradiciendo directamente la
   Fase 83 congelada y la promesa pública del proyecto. Eliminado; nunca
   había sido commiteado.
2. **Timeout mal configurado en `/v1/high-probability`** (DEC-172): el
   endpoint del menú de mayor probabilidad nunca se agregó a la lista de
   rutas con timeout extendido (`_call_with_timeout`,
   `src/dikamaha_service.py`), pese a barrer el mismo catálogo multi-liga
   que `/v1/live`/`/v1/upcoming`. El servidor se cortaba a sí mismo con 504
   antes de terminar el barrido -probablemente afectando también a usuarios
   reales de `/mayor-probabilidad` en la Mini App, no sólo a la cohorte
   prospectiva de Fase 123-. Corregido y verificado: el ciclo real completó
   sin error tras el fix.

## Pendientes operativos (Paso 2 del plan de cierre, diferido)

Requieren las cuentas de Telegram/Discord del usuario; no automatizables
desde una sesión de Claude Code:

- Smoke interactivo real desde un usuario Telegram autorizado en la Mini App.
- Registrar el short name de la Mini App en BotFather para enlaces `startapp`.
- Validar una regla de alerta con dedupe real antes de activar
  `MINIAPP_ALERTS_ENABLED=true` (hoy `false`).
- Smoke manual de los callbacks del bot de Discord (Fase 99,
  `promising_unconfirmed`).
- Smoke autenticado de `/v1/high-probability` con la clave real de
  producción (Railway no expone valores de variables por esta conexión
  OAuth; requiere que el usuario la extraiga del dashboard).

## Próximo checkpoint

**Paso 4 del plan de cierre**, pendiente de tiempo real de calendario:
revisar `/v1/track-record`, `/v1/track-record/daily` y el reporte de
`prospective_reliability` de Fase 123 (`docs/status.md`, sección Fase 123)
en 2-4 semanas, una vez haya suficientes partidos liquidados para que las
cifras dejen de estar ocultas por muestra mínima
(`MINIMUM_SAMPLE = 20` en `src/settlement_store.py`).

## Commits de este cierre (rama `main`)

| Commit | Qué hizo |
| --- | --- |
| `0699999` | Higiene de repo; elimina el reporte de apuestas no autorizado (DEC-169) |
| `0ed2370` | Corrige el estado documentado de Fase 116 |
| `40ad558` | Archiva la promoción de Markov v4 (DEC-170) |
| `e34148b` | Implementa Fase 123 |
| `6396231` | Integra el ciclo de Fase 123 en el proceso del publicador |
| `cdb3234`, `c84b0b1` | Mejoran el detalle de log para diagnosticar el fallo real |
| `0e9f8a6` | Corrige el timeout de `/v1/high-probability` (DEC-172) |

Todos pusheados a `origin/main` y desplegados; los 5 servicios Railway
reportan `SUCCESS` a la fecha de este documento.
