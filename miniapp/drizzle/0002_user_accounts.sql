-- Cuentas de usuario en base de datos.
--
-- Hasta aquí el acceso lo decidía la variable de entorno
-- `TELEGRAM_ALLOWED_USER_IDS`, de modo que dar de alta a una persona exigía
-- editar la configuración y **redesplegar el servicio**. Eso es lo que impedía
-- cualquier crecimiento: no hay flujo de alta posible si cada usuario nuevo
-- obliga a publicar una versión.
--
-- Este runner reaplica todas las migraciones en cada despliegue
-- (`preDeployCommand`), así que todo aquí es idempotente.

ALTER TABLE "miniapp_users"
  ADD COLUMN IF NOT EXISTS "status" text NOT NULL DEFAULT 'pending',
  ADD COLUMN IF NOT EXISTS "role" text NOT NULL DEFAULT 'user',
  ADD COLUMN IF NOT EXISTS "plan" text NOT NULL DEFAULT 'free',
  ADD COLUMN IF NOT EXISTS "approved_at" timestamptz,
  ADD COLUMN IF NOT EXISTS "approved_by" bigint;

DO $$
BEGIN
  -- `IF NOT EXISTS` no existe para constraints; sin esta guarda la segunda
  -- ejecución del runner fallaría con "constraint already exists".
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'miniapp_users_status_check'
  ) THEN
    ALTER TABLE "miniapp_users"
      ADD CONSTRAINT "miniapp_users_status_check"
      CHECK ("status" IN ('pending', 'active', 'blocked'));
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'miniapp_users_role_check'
  ) THEN
    ALTER TABLE "miniapp_users"
      ADD CONSTRAINT "miniapp_users_role_check"
      CHECK ("role" IN ('user', 'admin'));
  END IF;
END $$;

-- Backfill: toda fila preexistente pertenece a alguien que ya superó el
-- portero por variable de entorno -sólo se escribía tras validar `initData` y
-- comprobar la lista blanca-, así que ya estaba autorizado. Dejarlos en
-- `pending` les quitaría un acceso que hoy tienen.
--
-- Acotado a `approved_at IS NULL` para que sea idempotente y para no revertir
-- jamás un `blocked` decidido después por un administrador.
UPDATE "miniapp_users"
SET "status" = 'active', "approved_at" = now()
WHERE "approved_at" IS NULL AND "status" = 'pending';

-- La consulta de administración habitual es "quién está esperando aprobación".
CREATE INDEX IF NOT EXISTS "miniapp_users_status_idx"
  ON "miniapp_users" ("status");
