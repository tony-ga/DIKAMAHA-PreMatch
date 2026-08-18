# Runbook — Suscripciones Telegram Stars

Operación del nivel de pago (Fase 125, `DEC-204`). Para el diseño y los motivos,
`docs/phases/phase_125_star_subscription_tiers.md`.

**Rollback de todo, en cualquier momento:** `MINIAPP_BILLING_ENABLED=false` en el
servicio `telegram-miniapp`. Es una variable de Railway, no exige redesplegar
código, y las puertas se evaporan **antes** de la primera lectura a base. Las
migraciones son aditivas y no necesitan camino de vuelta.

## Prerrequisito externo al repositorio

**Corrección (2026-08-17): esta sección afirmaba que Stars requiere
"habilitación" en BotFather. Es falso y quedó sin verificar hasta que un
despliegue real lo expuso.** A diferencia de un proveedor tradicional (Stripe,
etc.), Stars **no se activa por proveedor**: `createInvoiceLink` con
`currency: "XTR"` no lleva `provider_token` y no aparece en
`/mybots → Payments → elegir proveedor`, porque no es un proveedor. El panel
"Telegram Stars" que BotFather muestra aparte es informativo -saldo, retiro vía
Fragment- y no es un interruptor de activación.

No se encontró en la documentación oficial (`core.telegram.org/bots/payments-stars`,
`core.telegram.org/bots/payments`) ningún paso de configuración previa
necesario para pagos únicos con Stars. La referencia completa de la Bot API
(`core.telegram.org/bots/api#createinvoicelink`) es demasiado extensa para
haberse podido revisar por completo buscando una condición específica sobre
`subscription_period`; **no verificado, no descartado**. La única forma
confiable de confirmarlo es la prueba empírica del paso 5 de este runbook: una
compra real con `subscription_period` fijado. Si Telegram la rechaza, el error
de `createInvoiceLink` (capturado como `billing_invoice_unavailable` en los
logs, con el `description` de Telegram) es la señal real, no una suposición
de este documento.

## Matriz de variables

| Servicio | Variables |
|---|---|
| `telegram-miniapp` | `MINIAPP_BILLING_ENABLED`, `MINIAPP_INTERNAL_API_KEY`, `MINIAPP_BILLING_SECRET`, `MINIAPP_PREMIUM_STARS_PRICE`, `MINIAPP_FREE_DAILY_PREDICTIONS`, `MINIAPP_QUOTA_TIMEZONE` |
| `telegram-alert-worker` | `MINIAPP_BILLING_ENABLED`, `MINIAPP_BILLING_SECRET`, `MINIAPP_BILLING_RECONCILE_SECONDS` |
| `dikamaha-premium-telegram-bot` | `TELEGRAM_BILLING_ENABLED`, `MINIAPP_INTERNAL_URL`, `MINIAPP_INTERNAL_API_KEY`, `MINIAPP_BILLING_SECRET` |

> **`MINIAPP_BILLING_SECRET` y `MINIAPP_INTERNAL_API_KEY` deben ser idénticos
> byte a byte entre los servicios que los comparten.** Un desajuste se
> manifiesta como *"todos los pre-checkout rechazados"*, que parece un problema
> de Telegram y no lo es.

`MINIAPP_INTERNAL_API_KEY` y `MINIAPP_BILLING_SECRET` no tienen valor por
defecto (mínimo 32 caracteres). El servicio **se niega a arrancar sin ellos**, y
eso es deliberado: servir endpoints internos con una clave adivinable es peor
que no arrancar. Por eso deben fijarse en un despliegue **anterior** al del
código que los usa.

## Orden de despliegue

Nada gatea a ningún usuario hasta el paso 7.

1. **Sólo esquema.** `0004_premium_plan_and_star_billing.sql` y
   `0005_grandfather_active_accounts.sql`. Verificar que `npm run db:migrate`
   dos veces seguidas deja la base idéntica — es la propiedad para la que existe
   todo el estilo de migración de este repositorio.
2. **Secretos, interruptores apagados.** Fijar los dos secretos compartidos en
   los tres servicios, con `MINIAPP_BILLING_ENABLED=false` y
   `TELEGRAM_BILLING_ENABLED=false`. Desplegar.
3. **Titularidad y cuota.** Con el interruptor apagado, `resolveEntitlement`
   devuelve premium antes de tocar PostgreSQL. Verificar en logs: **cero**
   eventos `entitlement_*` y p95 de `/api/live` sin cambios.
4. **Fontanería de pagos, sin cerrar nada.** `TELEGRAM_BILLING_ENABLED=true` en
   el bot y `MINIAPP_BILLING_ENABLED=true` **sólo en el worker**. Ya funciona
   `/premium` y los pagos se asientan, mientras nada está gateado.
5. **Compra real de extremo a extremo.** Ver "Verificación" abajo.
6. **Interfaz, todavía sin cerrar.** Con el interruptor de la Mini App apagado,
   `/api/billing/entitlement` reporta `enforced: false` y el panel de plan no se
   pinta, así que se valida el resto de la maqueta en dispositivos reales.
7. **Encender.** `MINIAPP_BILLING_ENABLED=true` en `telegram-miniapp`. Vigilar
   24 h: tasa de 402, `entitlement_resolution_failed`, `quota_fail_open` y
   `star_reconcile_ok.repaired`.
8. **Avisar del acceso heredado.** Con **≥14 días** de antelación al
   2026-11-01. No está automatizado. Pasar a todo el mundo de premium a free en
   una fecha que nunca se les comunicó es la forma más rápida de perder la base
   de usuarios actual.

## Verificación con dinero real

```sql
SELECT telegram_payment_charge_id, user_id, kind, stars_amount, ingest_source
  FROM star_payments ORDER BY applied_at DESC LIMIT 10;
```
```sql
SELECT user_id, plan, plan_source, plan_expires_at FROM miniapp_users
 WHERE plan_source = 'stars';
```

1. Un administrador compra desde la Mini App y otro desde `/premium` en el bot.
   Esperado: dos filas con `ingest_source='bot_forward'`,
   `star_subscriptions.current_period_end ≈ now + 30d`, `plan_source='stars'`, y
   premium reflejado en la Mini App en menos de 60 s.
2. Reembolsar uno con `POST /api/admin/billing/refund` y confirmar la
   revocación.
3. **Detener el servicio `telegram-miniapp` a propósito**, hacer una compra,
   reiniciarlo y confirmar en los logs del worker
   `{"event":"star_reconcile_ok","repaired":1}`. La red de seguridad se prueba
   rompiendo el camino primario, no leyendo el código.

## Cambiar el precio sin redesplegar

```bash
curl -X PATCH https://<miniapp>/api/admin/billing/price -H 'content-type: application/json' -H "x-csrf-token: $CSRF" --cookie "dikamaha_miniapp_session=$SESSION" -d '{"starsAmount":300}'
```

Requiere sesión con `role='admin'`. Sólo afecta a las facturas emitidas a partir
de ese momento: las suscripciones ya cobradas conservan su importe hasta que el
usuario cancele y vuelva a suscribirse. Verificar con
`SELECT * FROM billing_plans;`.

`MINIAPP_PREMIUM_STARS_PRICE` es **sólo semilla**: se usa la primera vez que se
crea la fila y como red si la fila no existiera. Cambiar la variable no cambia
el precio vigente.

## Alta y baja manual

```sql
-- Premium temporal concedido a mano (soporte, compensación).
UPDATE miniapp_users
   SET plan = 'premium', plan_source = 'grandfathered',
       plan_expires_at = now() + interval '30 days', plan_updated_at = now()
 WHERE telegram_user_id = <ID>;
```
```sql
-- Premium perpetuo. Sólo para administración: `plan_source='admin'` es el único
-- valor que la restricción permite sin fecha de caducidad.
UPDATE miniapp_users
   SET plan = 'premium', plan_source = 'admin',
       plan_expires_at = NULL, plan_updated_at = now()
 WHERE telegram_user_id = <ID>;
```

Un cambio manual tarda hasta 60 s en verse, por la caché de titularidad. No hay
que reiniciar nada.

## Diagnóstico por síntoma

| Síntoma | Dónde mirar |
|---|---|
| "Pagué y sigo en gratuito" | `SELECT * FROM star_payments WHERE user_id = <ID>`. Si no hay fila, el reenvío se perdió: esperar al reconciliador (≤15 min) o revisar logs del bot por `star_payment_forward_failed`. Si hay fila con `ingest_source='reconcile'`, el camino primario está fallando. |
| Todos los pre-checkout rechazados | `MINIAPP_BILLING_SECRET` desalineado entre bot y Mini App. Comparar byte a byte. |
| No llega ningún pre-checkout | `allowed_updates` del bot debe incluir `pre_checkout_query`. Confirmar además el alta de Stars en BotFather. |
| El botón de compra falla | Log `billing_invoice_failed` con el `description` de Telegram. Suele ser el alta de Stars sin completar. |
| `star_reconcile_ok.repaired > 0` | **Es una incidencia, no una estadística.** El reenvío bot → Mini App está perdiendo pagos. Revisar reinicios y 5xx de `telegram-miniapp`. |
| `entitlement_resolution_failed` | PostgreSQL inalcanzable desde la Mini App. Los usuarios quedan en `free` —conservan historial, catálogo y sus 3 diarias—, no bloqueados. |
| `quota_fail_open` en el bot | La Mini App no responde al endpoint de cuota. El bot sirve sin contar, a propósito. |
| Un premium recibe "necesitas Premium" | No debería ocurrir nunca: la degradación usa un mensaje distinto. Si aparece, es un fallo real de resolución, no una degradación. |

## Qué NO puede aparecer en ninguna superficie de venta

ROI, Kelly, stake, cuotas, rentabilidad, ganancias o acierto garantizado.
Premium se vende por **acceso y volumen**. Esto vale para el bot, la Mini App, la
descripción de la factura de Telegram y cualquier anuncio. Ver `DEC-169` y
`docs/PROJECT_CLOSURE_v1.md`: monetizar no relaja la restricción, la tensa.
