# Fase 125 — Niveles Free/Premium con suscripción Telegram Stars

**Decisión:** `DEC-204`
**Estado:** `implemented_billing_disabled`
**Fecha:** 2026-08-16

## Objetivo

Convertir DIKAMAHA en un producto que se financia a sí mismo, cobrando lo
mínimo que cubra su coste fijo mensual, sin degradar lo que hoy reciben los
usuarios existentes ni relajar ninguna restricción de comunicación del
proyecto.

La columna `plan` existía desde `0002_user_accounts.sql` con un comentario
explícito —*"gancho de titularidad para un futuro nivel de pago; hoy sólo se
transporta"*—: viajaba dentro de la cookie firmada y **ninguna ruta la
consultaba**. Esta fase la convierte en la variable que decide qué se sirve.

## Coste que hay que cubrir

Medias de siete días vía la API de métricas de Railway, a tarifa Railway
(20 USD/vCPU-mes + 10 USD/GB RAM-mes):

| Servicio | vCPU medio | RAM media | USD/mes |
|---|---|---|---|
| DIKAMAHA-PreMatch | 0.476 | 0.86 GB | 18.2 |
| telegram-miniapp | ~0.000 | 0.107 GB | 1.1 |
| telegram-alert-worker | 0.001 | 0.087 GB | 0.9 |
| Postgres | ~0.000 | 0.049 GB | 0.5 |
| dikamaha-premium-telegram-bot | ~0.000 | 0.029 GB | 0.3 |
| Seat Hobby | — | — | 5.0 |
| **Railway** | | | **≈ 26** |
| Claude Pro | | | 20 |
| **Piso mensual** | | | **≈ 46** |

El dato que gobierna el diseño: **el 87% del gasto variable es PreMatch**, y ese
gasto lo produce el barrido de 63 ligas contra ESPN con caché global. Escala con
el catálogo, no con los usuarios. El coste marginal por suscriptor es
prácticamente cero, de modo que el problema no es "cuánto cuesta cada usuario"
sino cuántos usuarios amortizan un coste fijo.

**Precio: 250 ⭐ al mes (~4.90 USD).** Telegram retiene ~35% —el retiro por
Fragment es de ~0.013 USD por Star—, así que quedan ~3.25 USD netos por
suscriptor y el **punto de equilibrio son 15 suscriptores**. Con 10 se cubre el
71% del coste.

## Alcance

| | FREE | PREMIUM |
|---|---|---|
| Aciertos del día e historial (`/historial`, `/v1/track-record*`) | ✅ | ✅ |
| Catálogo, equipos, estadísticas (`explorer/*`, `media`, `models`, `upcoming`) | ✅ | ✅ |
| Predicciones pre-match | 3 al día, a su elección | sin límite |
| En vivo (`/live`, `/v1/predict/live`) | ❌ | ✅ |
| Menú de mayor probabilidad | ❌ | ✅ |
| Favoritos / alertas | topes actuales (10 / 20) | sin tope |

Queda **fuera de alcance**: cualquier cambio en los modelos, en el router, en la
cadena de inferencia o en los artefactos sellados. Esta fase no toca ni una
probabilidad servida.

## Entradas

| Pieza existente | Uso |
|---|---|
| `miniapp/drizzle/0002_user_accounts.sql` | Plantilla de migración idempotente |
| `miniapp/lib/auth/session.ts` | Cookie de 30 días: el problema a resolver |
| `miniapp/lib/http.ts` | Puerta única por petición |
| `miniapp/lib/share-card.ts::shareFixtureKey` | Clave `league_slug:match_id` compartida |
| `src/telegram_bot.py::UserRateLimiter` | Precedente de límite por usuario en el bot |
| `miniapp/worker/alerts.ts` | Bucle donde vive la reconciliación |

## Método

### 1. La titularidad se lee, no se transporta

`session.ts` fija 30 días de TTL y `refreshedSessionToken` reemite la cookie sin
releer la cuenta. Para una suscripción mensual eso falla en los dos sentidos:
quien cancela conserva el producto hasta un mes, y quien paga desde el bot a las
tres de la mañana no lo tiene en la Mini App hasta la siguiente emisión.

`lib/auth/entitlements.ts` resuelve el nivel con una lectura por clave primaria
y caché de proceso de 60 s, **sólo en rutas de pago**. `session.plan` queda
degradado a pista para el primer pintado, con la invariante revisable por grep
de que ninguna ruta autoriza leyéndolo.

Se descartó meter `planExpiresAt` en la cookie: acota la degradación pero no
puede resolver la alta, y arreglar eso exige releer la fila, es decir la opción
elegida con pasos de más.

### 2. La caducidad se calcula al leer

`effective_plan(bigint)` en SQL y su espejo en el `SELECT` de
`resolveEntitlement`. Un premium vencido devuelve `free` en la petición
siguiente sin depender de ningún barrido —un barrido que no corra dejaría
premium a quien no paga—. Como efecto, la cancelación más común, la del propio
panel de Telegram, que **no nos notifica nada**, no necesita código: el cobro no
vuelve, la fecha pasa y el plan cae solo.

### 3. La cuota es por partido, no por petición

`components/providers.tsx` activa `refetchOnWindowFocus` global y
`prediction-detail.tsx` cachea por `["prediction", fixtureId]`: una cuota contada
por petición se consumiría sola al recuperar el foco de la WebView. La promesa
del plan gratuito es "3 predicciones al día de tu elección", no "3 peticiones
HTTP", así que `prediction_grants` registra qué partidos se desbloquearon y
reabrir uno concedido es gratis.

El veredicto cabe en **una sentencia**: el `WHERE q.used < q.daily_limit` del
`ON CONFLICT DO UPDATE` toma el row lock, de modo que Mini App y bot se
serializan ahí y el segundo evalúa `used` ya incrementado. Cuando el `WHERE`
falla devuelve cero filas y no lanza, que es lo que distingue "agotado" de
"error" sin manejo de excepciones en el camino normal.

El contador vive en PostgreSQL y no en memoria como el rate limiter de
`lib/http.ts` porque el cupo es por persona y no por superficie: bot, Mini App y
tarjeta compartida usan la misma clave `league_slug:match_id`.

### 4. Un solo escritor para los pagos

Sólo el bot hace `getUpdates`, así que `pre_checkout_query` y
`successful_payment` aterrizan en Python; sólo la Mini App tiene base de datos.
El bot reenvía a un endpoint interno autenticado con secreto compartido en lugar
de abrir su propia conexión, aunque SQLAlchemy ya esté en
`requirements.telegram-bot.txt`: aplicar un pago son tres escrituras acopladas
en una transacción, y dos implementaciones en dos lenguajes divergirían con el
modo de fallo "pagó y no es premium".

`pre_checkout_query` se verifica **en local, sin red**: Telegram concede 10
segundos y cancela el pago si se agotan; un viaje a la Mini App dentro de esa
ventana es una moneda al aire durante un arranque en frío.

### 5. Red de seguridad

Un `successful_payment` perdido deniega en silencio a alguien que pagó, que es
el peor desenlace de esta fase y el único que el usuario no puede diagnosticar.
`worker/billing-reconcile.ts` contrasta contra `getStarTransactions` cada 900 s
dentro del bucle de alertas, en su propio `try/catch`. La idempotencia es
enteramente el `ON CONFLICT DO NOTHING` del libro mayor: el reconciliador no
tiene estado propio, ni cursor que persistir, ni "último visto" que corromper.

## Bloqueadores encontrados en el código

Los tres eran silenciosos y ninguno se habría detectado sin leer el código:

1. **`src/telegram_bot.py:291`** — `allowed_updates` no incluía
   `pre_checkout_query`, así que Telegram nunca lo habría entregado y **ningún
   pago se habría confirmado**.
2. **`src/telegram_bot.py:622`** — `if not isinstance(text, str): return`
   descartaba todo mensaje sin texto, que es exactamente la forma de un
   `successful_payment`. El cobro se habría perdido en silencio.
3. **`miniapp/app/api/share/route.ts:36`** — llamaba a `/v1/predict/upcoming`
   sin pasar por `proxyPost`, de modo que "compartir" habría sido una vía para
   pedir predicciones ilimitadas sin tocar el contador.

## Gate

El gate de esta fase es **operativo**, no estadístico: no hay ninguna afirmación
probabilística que validar porque no se toca ningún modelo.

| Criterio | Estado |
|---|---|
| Migraciones reaplicables sin efecto | pendiente de despliegue |
| Idempotencia probada por replay del mismo `charge_id` | ✅ `billing-apply.test.ts` |
| Atomicidad del cupo bajo concurrencia real | ⏸ requiere `DIKAMAHA_TEST_DATABASE_URL` |
| Revocación probada por reembolso | ✅ `billing-apply.test.ts` |
| Recuperación probada rompiendo el camino primario | pendiente de despliegue |
| Ruta gateada no consume cómputo aguas arriba | ✅ `proxy-integration.test.ts` |
| Pre-checkout resuelto sin red | ✅ `test_phase_125_star_subscriptions.py` |
| Con el interruptor apagado, comportamiento idéntico al previo | ✅ ambas suites |

## Criterios de salida

1. `npm run db:migrate` dos veces seguidas deja la base idéntica.
2. Una compra real de 250 ⭐ desde la Mini App y otra desde `/premium` quedan
   asentadas con `ingest_source='bot_forward'` y `plan_source='stars'`.
3. Un reembolso real revoca el acceso.
4. Con la Mini App detenida a propósito durante una compra, el reconciliador la
   repara y reporta `repaired: 1`. **La red de seguridad se prueba rompiendo el
   camino primario, no leyendo el código.**
5. Veinticuatro horas con `MINIAPP_BILLING_ENABLED=true` sin
   `entitlement_resolution_failed` ni `star_reconcile_ok.repaired > 0`.

## Evidencia

- Vitest: 18 archivos, 133 pruebas, 1 omitida (concurrencia real sin base).
  Nuevos: `entitlements`, `quota`, `billing-payload`, `billing-apply`,
  `billing-internal-auth`, `billing-reconcile`. Extendidos: `session`,
  `proxy-integration`.
- Pytest: `tests/test_phase_125_star_subscriptions.py`, 21 pruebas nuevas;
  `tests/test_telegram_bot.py` sin regresiones (16).
- `tsc --noEmit` limpio.

## Limitaciones conocidas

- **El acceso heredado vence el 2026-11-01.** El aviso con ≥14 días de
  antelación es obligatorio y no está automatizado: pasar a todo el mundo de
  premium a free en una fecha que nunca se les comunicó es la forma más rápida
  de perder la base de usuarios actual.
- **La prueba de concurrencia se omite sin PostgreSQL real.** Es la única
  aserción que valida de verdad el diseño del cupo; una versión mockeada no dice
  nada sobre bloqueos de fila.
- **La caché de titularidad asume una réplica.** `railway.miniapp.toml` fija
  `numReplicas = 1`. Con más réplicas el techo de 60 s deja de estar acotado por
  `invalidateEntitlement`, que sólo limpia el proceso que atendió el pago.
- **`getStarTransactions` no distingue una renovación de un primer cargo.** El
  reconciliador marca `is_recurring: true` siempre; el dato fino sólo llega por
  el camino primario.

## Restricción de comunicación

Ninguna superficie de venta —bot, Mini App, factura de Telegram— menciona ROI,
Kelly, stake, cuotas, rentabilidad ni acierto garantizado. Premium se vende por
**acceso y volumen**. Monetizar el producto no relaja esa restricción: la tensa,
porque el momento de justificar un precio es exactamente cuando más tienta citar
una cifra de retorno. El aviso de uso responsable se añadió **dentro** del propio
panel de venta por la misma razón.
