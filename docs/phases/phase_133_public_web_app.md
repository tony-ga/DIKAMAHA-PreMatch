# Fase 133 — Sitio web público con la misma Mini App

**Decisión:** `DEC-219`, `DEC-220`
**Estado:** `implemented_web_payments_disabled`
**Fecha:** 2026-08-19

## Objetivo

Servir DIKAMAHA desde un dominio propio, con **el mismo diseño y las mismas
funciones** que la Mini App de Telegram, sin bifurcar el código y sin degradar en
nada la experiencia dentro de Telegram.

La Mini App de Fase 115 nunca fue un cliente ligero: es una aplicación Next.js 16
con App Router y BFF propio -`app/api/**`, Postgres propio, proxy a la API
Python-. El producto **ya es web**. Esta fase no lo reescribe: identifica los
cuatro puntos que dependen de la plataforma y los hace condicionales al contexto
de ejecución.

| Punto atado a Telegram | Archivo |
|---|---|
| Autenticación por `initData` | `lib/auth/telegram.ts`, `app/api/session/telegram/route.ts` |
| Cobro con `openInvoice` (Stars) | `components/premium-gate.tsx` |
| `BackButton` y tema del contenedor | `components/app-shell.tsx`, `components/providers.tsx` |
| Compartir con `openTelegramLink` | `components/share-prediction.tsx` |

## Alcance

Dentro:

- Contexto de ejecución `telegram | web` resuelto una sola vez y propagado por React.
- Acceso web con Telegram Login Widget, que devuelve el **mismo `telegram_user_id`**
  que `initData` y por tanto la misma cuenta, el mismo plan y el mismo historial.
- Cobro web con Stripe, tras interruptor `MINIAPP_STRIPE_ENABLED`, con exclusión
  mutua frente a una suscripción Stars viva (`DEC-220`).
- Contenedor centrado en escritorio, compartir por `navigator.share`, `robots.txt`.
- Un solo servicio Railway con dominio propio; el bot no se toca.

Fuera:

- Cualquier cambio en modelos, router, cadena de inferencia o artefactos sellados.
  **Esta fase no toca ni una probabilidad servida.**
- Rediseño para escritorio más allá del contenedor centrado.
- Registro con email o cuentas no-Telegram (`DEC-219`, opción (b) descartada).
- Cambios en el bot Python, el worker de alertas o la API DIKAMAHA.

## Entradas

| Pieza existente | Uso |
|---|---|
| `miniapp/lib/auth/telegram.ts` | Defensas de firma que hereda el validador del widget |
| `miniapp/app/api/session/telegram/route.ts` | Plantilla estructural de la ruta de sesión web |
| `miniapp/lib/auth/session.ts::issueSession` | Sesión propia, idéntica en ambos contextos |
| `miniapp/lib/auth/access.ts::resolveAccess` | Alta y estado de cuenta, sin cambios |
| `miniapp/lib/auth/entitlements.ts` | Autoridad única del plan; Stripe entra por aquí |
| `miniapp/lib/http.ts` | CSRF y rate limit reutilizados por las rutas nuevas |
| `miniapp/drizzle/0004_premium_plan_and_star_billing.sql` | Plantilla idempotente de la migración `0006` |
| `miniapp/app/globals.css` | Paleta y `[data-tg-theme="light"]`, ya independientes de `--tg-theme-*` |

## Método

### 1. El contexto se detecta, no se configura

`lib/runtime-context.ts` resuelve `telegram` cuando `window.Telegram?.WebApp.initData`
no está vacío, y `web` en cualquier otro caso. Es exactamente la señal que hoy
dispara el error *"Abre DIKAMAHA desde Telegram"*, así que ya está probada en
producción. Se resuelve una vez y viaja por contexto de React; ningún componente
vuelve a mirar `window` por su cuenta.

No hay variable de entorno de contexto: un mismo despliegue sirve los dos, y el
servidor sólo distingue en las rutas de sesión.

### 2. La identidad no cambia

El Login Widget firma con `secret = SHA256(botToken)` -no con
`HMAC("WebAppData", botToken)`, que es la variante de `initData`-, sobre el mismo
`data_check_string` de claves ordenadas separadas por `\n`.
`lib/auth/telegram-login.ts` respeta esa diferencia y hereda todo lo demás:
`timingSafeEqual`, `hash` con forma `^[a-f0-9]{64}$`, `auth_date` ni futuro ni
caducado, y parseo validado del usuario.

Como el identificador es el mismo, no hay tabla de identidades, ni vinculación,
ni migración de datos: `POST /api/session/web` hace el mismo upsert que la ruta
de Telegram -con el mismo `onConflictDoUpdate` que **no** pisa `status`, `role`
ni `plan`-, resuelve el acceso por `resolveAccess` y emite la misma sesión.

### 3. El contenedor deja de ser obligatorio

`BackButton` sólo se registra en contexto Telegram; en web navega el navegador y
la barra inferior de siete destinos cubre el resto. El tema pasa a resolverse por
`colorScheme` cuando hay WebApp y por `prefers-color-scheme` cuando no, escribiendo
el mismo `data-tg-theme` que ya consume `globals.css`: la hoja de estilos no se
toca.

`maximumScale: 1` se conserva en el `viewport` estático -existe por un bug real
del WebView de iOS, documentado en `app/layout.tsx`- y se **libera en el cliente
sólo en contexto web**, donde bloquear el pellizco es un defecto de accesibilidad
y no hay WebView que proteger.

### 4. Escritorio sin rediseño

El layout es móvil-primero y el E2E corre a 390×844. `.app-frame` gana
`max-width` y centrado sobre el fondo `--void`. Es un cambio de dos líneas en
`globals.css` que cumple literalmente "mismo diseño" y evita rediseñar veinte
páginas. Una vista ancha real sería una fase propia.

### 5. Stripe entra por el mismo camino que Stars

Migración `0006_stripe_web_billing.sql`, idempotente como todas -el runner las
reaplica en cada despliegue y no lleva ledger-: amplía el CHECK de `plan_source`
con `'stripe'` y añade `stripe_customers`, `stripe_subscriptions` y
`stripe_events`. Esta última es la que hace idempotente el webhook, exactamente
el papel que `star_payments` cumple para Stars.

El webhook se autentica con la firma sobre el cuerpo **crudo**, deduplica por
`event_id` antes de aplicar nada, y concede el plan por la misma vía que Stars:
escribir `plan`/`plan_source`/`plan_expires_at` e invalidar la caché de
titularidad. No hay un segundo camino de concesión.

La exclusión mutua de `DEC-220` se comprueba **antes** de crear la sesión de
checkout: con una suscripción Stars viva, la ruta devuelve `409` y la interfaz
dice dónde gestionarla. Es lo que impide que dos pasarelas escriban sobre el
único `plan_expires_at` del usuario.

La superficie comercial mantiene la restricción del proyecto: **vende acceso y
volumen, nunca retorno**. Ni ROI, ni Kelly, ni stakes, ni cuotas.

## Salidas

| Artefacto | Contenido |
|---|---|
| `lib/runtime-context.ts` | Detección de contexto |
| `lib/auth/telegram-login.ts` | Validador de firma del widget |
| `app/api/session/web/route.ts` | Alta y sesión desde la web |
| `app/login/page.tsx` | Pantalla de acceso pública |
| `app/api/billing/stripe/{checkout,webhook,portal}/route.ts` | Cobro web |
| `drizzle/0006_stripe_web_billing.sql` | Migración idempotente |
| `docs/runbooks/railway_public_web_app.md` | Dominio, `/setdomain`, webhook, interruptores |

## Gate de salida

Se cierra la fase sólo con todo esto:

1. La suite Vitest existente **en verde sin modificarse**: 23 archivos previos,
   244 pruebas en total con las nuevas. Cumplido.

   En Playwright quedan **6 fallos que son previos a esta fase**, no regresiones.
   Se comprobó forzando `detectContext()` a `"telegram"` -el comportamiento
   anterior- y fallan igual: cinco muestran el muro Premium porque su stub
   devuelve un payload sin `plan`, y `high-probability.spec.ts:238` espera 6
   destinos en la barra cuando hay 7 desde la Fase 126. Arreglarlos no pertenece
   a esta fase; quedan anotados como deuda conocida.

   Una fixture sí hubo que modificar, y conviene decirlo en vez de esconderlo:
   `responsive-fit.spec.ts` estubaba `initData: ''`, que es exactamente la señal
   con la que se distingue el WebView de un navegador. Bajo esa fixture la Mini
   App se comportaba como sitio web y la prueba del bloqueo de escala fallaba.
   **La aserción no cambió** -en Telegram se sigue exigiendo `maximum-scale=1`-;
   lo que cambió es que el stub manda ahora un `initData` como el de un cliente
   real, sin el cual la Mini App ni siquiera podría autenticarse.
2. Pruebas nuevas del validador del widget: firma válida, hash manipulado,
   `auth_date` caducado, `auth_date` futuro, usuario malformado.
3. Pruebas nuevas del webhook: firma inválida rechazada, mismo `event_id` dos
   veces aplica una sola vez, `customer.subscription.deleted` degrada el plan,
   exclusión mutua Stars/Stripe efectiva.
4. Proyecto Playwright `web` sin stub de `window.Telegram`: `/` sin sesión lleva
   a `/login`, navegación completa, cero errores de consola.
5. Reaplicar `0006` sin efecto.
6. Acceso real desde el dominio propio tras `/setdomain` en BotFather.
7. Checkout real en modo test de Stripe, con el webhook aplicando el plan.

Los puntos 5-7 no se pueden obtener sin desplegar. El despliegue va por pasos:
primero migración, luego secretos, luego código con `MINIAPP_STRIPE_ENABLED=false`,
y sólo al final el interruptor —el mismo orden que el runbook de Fase 125—.

## Limitaciones conocidas

- El acceso web sigue exigiendo una cuenta de Telegram. Es deliberado (`DEC-219`):
  compra continuidad total de identidad al precio de no captar usuarios sin
  Telegram. Abrir eso sería una fase propia con su propia decisión.
- El Login Widget no funciona hasta registrar el dominio con `/setdomain`; es un
  paso manual en BotFather y no hay forma de verificarlo desde el código.
- El diseño en escritorio es el móvil centrado. No es una limitación técnica sino
  el alcance elegido.
- La cuota diaria, los favoritos y las alertas comparten tope entre ambos
  contextos, porque comparten usuario. Es lo correcto —un humano, un presupuesto—
  pero conviene decirlo: abrir la web no duplica las tres predicciones diarias.
- El botón de compra en la web **no muestra el importe en moneda**: dice
  "suscripción mensual" y la cifra aparece en el checkout de Stripe. Escribirla
  en la interfaz exigiría leerla de Stripe en cada pintado o duplicarla en
  configuración, y una cifra duplicada es una cifra que algún día no coincidirá
  con el cargo real. En Telegram se sigue anunciando en Stars, que es lo que se
  cobra allí.
- Stripe se integró **sin su SDK**: tres llamadas `fetch` y la verificación de
  firma con `node:crypto`, en línea con el runtime mínimo de la Fase 108. El
  coste es que un cambio de forma en la API de Stripe hay que absorberlo a mano;
  por eso el fin de periodo se lee de la suscripción en vez de reconstruirse del
  evento.
