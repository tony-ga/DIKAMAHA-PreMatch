# Runbook — Sitio web público (Fase 133)

Complementa `docs/runbooks/railway_telegram_miniapp.md` y
`docs/runbooks/telegram_stars_subscriptions.md`. **No hay servicio nuevo**: el
sitio web lo sirve el mismo `telegram-miniapp` (`dbd1077b-…`) desde el mismo
Dockerfile y la misma imagen. Lo único que cambia es que ese servicio pasa a
servir también una superficie web y a aceptar un segundo camino de entrada.

## Orden de despliegue

El mismo criterio que en la Fase 125: **cada paso deja el sistema en un estado
válido**, y el interruptor va al final.

1. **Migración.** `preDeployCommand = npm run db:migrate` aplica
   `0006_stripe_web_billing.sql` en el despliegue. Es idempotente; reaplicarla no
   tiene efecto. Sin ella, el primer pago con Stripe violaría el CHECK de
   `plan_source` y el usuario pagaría sin recibir nada.
2. **Dominio.** El servicio ya expone
   `telegram-miniapp-production-cbab.up.railway.app`, y **esa es la superficie
   web** (`MINIAPP_PUBLIC_WEB_URL`, aplicado el 2026-08-20). Railway **no genera
   un segundo subdominio** mientras el servicio ya tiene uno: `generate-domain`
   devuelve el existente en lugar de crear otro. Para separar "web" de "miniapp"
   haría falta un dominio propio comprado, adjuntado con `generate-domain` y
   verificado por DNS; entonces bastaría cambiar `MINIAPP_PUBLIC_WEB_URL` y
   repetir el paso 3.
3. **`/setdomain` en BotFather.** Con `@viewtofuture_bot` seleccionado,
   `/setdomain` y el dominio del paso 2. Sin esto el Login Widget no autentica y
   no hay forma de entrar desde el navegador. Es manual: exige la cuenta de
   Telegram del dueño del bot y **no se puede hacer ni verificar desde el
   código** -el iframe del widget es de otro origen y no se puede inspeccionar-.
   La prueba es directa: pulsar el botón en `/login`. Si el dominio no está
   registrado, Telegram responde con un error de dominio en vez de pedir
   confirmación.
4. **Variables de la superficie web** (aplicadas el 2026-08-20):
   - `TELEGRAM_BOT_USERNAME` — `viewtofuture_bot`, sin `@`.
   - `MINIAPP_PUBLIC_WEB_URL` — el dominio del paso 2, con `https`.

   El esquema exige las dos juntas: con dominio y sin nombre de bot, el servicio
   se niega a arrancar antes que servir una pantalla de acceso que no funciona.
   Ponerlas de una en una **tira el servicio**; van en la misma operación.
5. **Código.** Desplegar con `MINIAPP_STRIPE_ENABLED=false`. En este punto la web
   ya sirve el producto completo y el pago sigue siendo sólo por Telegram.
   Hecho el 2026-08-20 con el commit `a709bfa` (push a `main`, auto-deploy):
   `/api/health` responde `{"status":"ready","database":true,"upstream":true}`
   -o sea, la migración `0006` se aplicó sin romper nada-, `/login` sirve el
   widget con el bot correcto, y `/robots.txt` publica sólo portada, acceso y
   tarjetas compartidas.
6. **Comprobar la Mini App.** Antes de seguir: abrirla desde Telegram y verificar
   que se comporta igual que antes -mismo arranque, botón atrás del contenedor,
   tema, y compra con Stars-.
7. **Stripe.** Sólo después:
   - Crear el producto y el precio recurrente mensual en Stripe.

     Hecho el 2026-08-20 en modo live: producto `prod_V6aClRs2gXEtLb`
     ("DIKAMAHA Premium") y precio `price_1U6N2aPFLfIEEg44jtZeSwdd`, 4,90 USD al
     mes. Es paridad nominal con los 250 ⭐ de Telegram, pero Stripe no retiene
     el ~35% que retiene Telegram: quedan ~4,60 netos por suscriptor frente a
     ~3,25, así que el punto de equilibrio baja de 15 a ~10.
   - `STRIPE_SECRET_KEY` (secreto, lo pega una persona) y `STRIPE_PRICE_ID`
     (identificador público, ya aplicado).
   - Registrar el endpoint `https://<dominio>/api/billing/stripe/webhook` con los
     eventos `checkout.session.completed`, `invoice.paid`,
     `customer.subscription.deleted` y `charge.refunded`; copiar el secreto de
     firma a `STRIPE_WEBHOOK_SECRET`.

     Hecho el 2026-08-20: endpoint `we_1U6N03PFLfIEEg44MTLN4uwu` en la cuenta
     `acct_1U327iPFLfIEEg44` (Dikamaha), **en modo live**. Verificado alcanzable
     desde fuera -devuelve `503 stripe_disabled` mientras el interruptor está
     apagado, que es la respuesta correcta-. El secreto de firma se revela en el
     panel del endpoint; no se copia a ningún documento.

     **La clave y el endpoint tienen que ser del mismo modo.** Un `sk_test_`
     con este endpoint live no recibe nada, y la firma de un endpoint de prueba
     no valida contra una clave live. Es el fallo más caro de diagnosticar
     porque no produce error visible: simplemente no llega ningún evento.
   - Habilitar el Billing Portal en el panel de Stripe (lo usa
     `/api/billing/stripe/portal` para cancelar). **No existe todavía**: la
     cuenta no tiene configuración por defecto, y sin ella esa ruta devuelve
     error de Stripe. Se activa en Settings → Billing → Customer portal; la API
     de creación no está expuesta al conector, así que es un paso de panel.
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

- **El widget renderiza pero no autentica**: es `/setdomain` sin hacer, o hecho
  con otro dominio. El botón aparece igual -el iframe de `oauth.telegram.org` se
  carga siempre-, y el fallo sólo se ve al pulsarlo. Por eso no basta con
  comprobar que la pantalla de acceso "se ve bien".
- **Firma de webhook inválida**: si alguien introduce un middleware que lea o
  reescriba el cuerpo antes del manejador, la firma deja de cuadrar. El cuerpo
  tiene que llegar crudo.
- **Precio**: el importe en moneda vive en Stripe, no en `billing_plans`. Esa
  tabla sigue gobernando **sólo** el precio en Stars.
