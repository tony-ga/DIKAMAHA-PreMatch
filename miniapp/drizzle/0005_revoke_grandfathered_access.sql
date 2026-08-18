-- Revoca el acceso heredado y retira la migración que lo concedía.
--
-- Sustituye a `0005_grandfather_active_accounts.sql` (eliminada). Aquella
-- concedía premium hasta 2026-11-01 a toda cuenta `status='active'` con
-- `plan='free'` y `plan_source='default'`. Tres errores se combinaban:
--
--   1. Este runner **no lleva registro de migraciones aplicadas**
--      (`scripts/migrate.ts` reejecuta todos los `.sql` en cada despliegue),
--      así que la concesión no era un evento único sino uno repetido.
--   2. `plan_source = 'default'` no es una guarda: es el estado de toda cuenta
--      recién creada, y es exactamente el valor que escribe
--      `app/api/admin/users/[userId]/route.ts` al pasar a alguien a gratuito.
--   3. El corte `approved_at < '2026-09-01'` estaba **en el futuro** cuando se
--      escribió (2026-08-16), de modo que no excluía a nadie: toda alta creada
--      antes de esa fecha lo cumplía.
--
-- Efecto medido en producción: cada despliegue devolvía premium a todas las
-- cuentas gratuitas -altas nuevas y degradaciones manuales por igual-. Una
-- cuenta verificada como `free`/`default` a las 20:32 apareció como
-- `premium`/`grandfathered` tras dos despliegues, sin intervención humana.
--
-- Se revoca a todos los heredados, no sólo a los mal concedidos: el aviso de 14
-- días que exige `docs/runbooks/telegram_stars_subscriptions.md` nunca llegó a
-- enviarse, así que nadie fue informado de tener acceso heredado y revocarlo no
-- rompe ninguna promesa comunicada. A partir de aquí, conceder premium es
-- exclusivamente trabajo del código en runtime -cobro con Stars o acción de un
-- administrador-, nunca de una migración que se reejecuta sola.
--
-- Idempotente y seguro de reejecutar indefinidamente: tras la primera pasada no
-- queda ninguna fila `grandfathered` y ningún código escribe ya ese valor.
-- Nunca toca a quien pagó (`stars`), a los administradores (`admin`) ni a los
-- reembolsados (`refunded`). El valor sigue en el dominio del CHECK de `0004`
-- por si quedara alguna fila histórica que revocar.

UPDATE "miniapp_users"
   SET "plan" = 'free',
       "plan_source" = 'default',
       "plan_expires_at" = NULL,
       "plan_updated_at" = now()
 WHERE "plan_source" = 'grandfathered';
