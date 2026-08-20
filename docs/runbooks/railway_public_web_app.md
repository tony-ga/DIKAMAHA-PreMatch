# Runbook — Sitio web público (Fase 133)

Complementa `docs/runbooks/railway_telegram_miniapp.md` y
`docs/runbooks/telegram_stars_subscriptions.md`. **No hay servicio nuevo**: el
sitio web lo sirve el mismo `telegram-miniapp` (`dbd1077b-…`) desde el mismo
Dockerfile y la misma imagen. Lo único que cambia es que ese servicio pasa a
responder también en un dominio propio y a aceptar un segundo camino de entrada.

## Orden de despliegue

El mismo criterio que en la Fase 125: **cada paso deja el sistema en un estado
válido**, y el interruptor va al final.

1. **Migración.** `preDeployCommand = npm run db:migrate` aplica
   `0006_stripe_web_billing.sql` en el despliegue. Es idempotente; reaplicarla no
   tiene efecto. Sin ella, el primer pago con Stripe violaría el CHECK de
   `plan_source` y el usuario pagaría sin recibir nada.
2. **Dominio.** Añadir el dominio propio al servicio en Railway y apuntar el DNS.
   La URL `telegram-miniapp-production-cbab.up.railway.app` sigue funcionando y
   es la que usa el bot: **no se toca**.
3. **`/setdomain` en BotFather.** Con el bot seleccionado, `/setdomain` y el
   dominio del paso 2. Sin esto el Login Widget no renderiza y no hay forma de
   entrar desde el navegador. Es manual y no se puede verificar desde el código.
4. **Variables de la superficie web:**
   - `TELEGRAM_BOT_USERNAME` — nombre del bot sin `@`.
   - `MINIAPP_PUBLIC_WEB_URL` — el dominio del paso 2, con `https`.

   El esquema exige las dos juntas: con dominio y sin nombre de bot, el servicio
   se niega a arrancar antes que servir una pantalla de acceso que no funciona.
5. **Código.** Desplegar con `MINIAPP_STRIPE_ENABLED=false`. En este punto la web
   ya sirve el producto completo y el pago sigue siendo sólo por Telegram.
6. **Comprobar la Mini App.** Antes de seguir: abrirla desde Telegram y verificar
   que se comporta igual que antes -mismo arranque, botón atrás del contenedor,
   tema, y compra con Stars-.
7. **Stripe.** Sólo después:
   - Crear el producto y el precio recurrente mensual en Stripe.
   - `STRIPE_SECRET_KEY`, `STRIPE_PRICE_ID`.
   - Registrar el endpoint `https://<dominio>/api/billing/stripe/webhook` con los
     eventos `checkout.session.completed`, `invoice.paid`,
     `customer.subscription.deleted` y `charge.refunded`; copiar el secreto de
     firma a `STRIPE_WEBHOOK_SECRET`.
   - Habilitar el Billing Portal en el panel de Stripe (lo usa
     `/api/billing/stripe/portal` para cancelar).
   - Encender `MINIAPP_STRIPE_ENABLED=true`.

   Con el interruptor en `true` y cualquiera de las tres claves ausente, el
   servicio **no arranca**. Es deliberado: es preferible a fallar en la cara del
   primer usuario que intente pagar.

## Rollback

- `MINIAPP_STRIPE_ENABLED=false` apaga el cobro web sin redesplegar código: las
  tres rutas devuelven 503 y la interfaz vuelve a remitir a Telegram. Las
  suscripciones ya activas **siguen valiendo**: el plan vive en `miniapp_users`,
  no en el interruptor.
- Vaciar `MINIAPP_PUBLIC_WEB_URL` cierra la superficie web entera. La Mini App no
  se entera: su camino de entrada es otro.
- La migración `0006` no se revierte. No retira ningún valor de `plan_source` ni
  toca las tablas de Stars; dejarla puesta es inocuo.

## Comprobaciones tras encender

| Qué | Cómo | Qué debe pasar |
|---|---|---|
| Acceso web | Abrir el dominio sin sesión | Redirige a `/login` y el widget renderiza |
| Continuidad de cuenta | Entrar con la misma cuenta que usa la Mini App | Mismo historial, mismo plan, misma cuota diaria |
| Cuenta no aprobada | Entrar con una cuenta `pending` | Mensaje de aprobación pendiente, no un error genérico |
| Cobro web | Checkout en modo test | El webhook aplica el plan en segundos, no en 60 s |
| Idempotencia | Reenviar el evento desde el panel de Stripe | Segunda entrega sin efecto; una sola fila en `stripe_events` |
| Exclusión mutua | Intentar pagar con Stripe teniendo Stars activo | 409 y mensaje que remite a Telegram |
| Cancelación | Portal de Stripe | `customer.subscription.deleted` degrada a `free` al vencer |

## Altas y bajas

Sin cambios respecto a `railway_telegram_miniapp.md`: se siguen gestionando por
SQL sobre `miniapp_users` (`pending | active | blocked`), y valen para las dos
superficies porque **son la misma cuenta**.

## Consultas útiles

```sql
-- Suscripciones vivas por pasarela.
SELECT plan_source, count(*)
  FROM miniapp_users
 WHERE plan = 'premium' AND plan_expires_at > now()
 GROUP BY plan_source;

-- Últimos eventos de Stripe asentados.
SELECT event_id, event_type, user_id, applied_at
  FROM stripe_events ORDER BY applied_at DESC LIMIT 20;

-- Nadie debería aparecer con las dos pasarelas activas (DEC-220).
SELECT s.user_id
  FROM star_subscriptions s
  JOIN stripe_subscriptions t ON t.user_id = s.user_id
 WHERE s.status = 'active' AND t.status = 'active'
   AND s.current_period_end > now() AND t.current_period_end > now();
```

## Trampas conocidas

- **El widget no renderiza**: casi siempre es `/setdomain` sin hacer, o hecho
  con otro dominio. El navegador no da un error claro; el iframe simplemente no
  aparece.
- **Firma de webhook inválida**: si alguien introduce un middleware que lea o
  reescriba el cuerpo antes del manejador, la firma deja de cuadrar. El cuerpo
  tiene que llegar crudo.
- **Precio**: el importe en moneda vive en Stripe, no en `billing_plans`. Esa
  tabla sigue gobernando **sólo** el precio en Stars.
