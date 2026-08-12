# Propuesta: cuentas de usuario multi-canal

**Estado:** propuesta, no implementada. Ningún código ni migración de este
documento debe ejecutarse sin aprobación explícita.

## Objetivo

Reemplazar el modelo actual de identidad (un usuario = un `telegram_user_id`)
por un modelo de identidad federada que permita a la misma persona entrar por
Telegram hoy y vincular Google, teléfono o Discord más adelante sin duplicar
su cuenta ni perder historial (favoritos, alertas, settlements).

## Estado actual (línea base)

- `miniapp_users.telegram_user_id` (`bigint`) es la clave primaria de usuario.
- `miniapp_favorites.user_id` y `alert_subscriptions.user_id` referencian
  directamente `miniapp_users.telegram_user_id` (`onDelete: cascade`).
- La sesión (`lib/auth/session.ts`) firma un token HMAC cuyo `userId` **es**
  el `telegram_user_id` numérico.
- El login (`app/api/session/telegram/route.ts`) valida `initData` con HMAC
  contra `TELEGRAM_BOT_TOKEN` (`lib/auth/telegram.ts`) y hace upsert directo
  en `miniapp_users`.
- No existe ningún otro proveedor de identidad ni tabla de cuentas separada
  de la identidad de Telegram.

Esto es correcto para un solo canal, pero acoplar identidad de usuario a
identidad de proveedor es exactamente lo que hay que deshacer antes de sumar
Google, teléfono o Discord.

## Modelo propuesto

Separar "persona" de "método de acceso":

- `users`: la cuenta interna, agnóstica de canal.
- `user_identities`: cada método de login vinculado a un `user`, uno por fila.

```
users
  id            uuid PK default gen_random_uuid()
  display_name  text            -- nombre mostrado, editable
  status        text not null default 'active'   -- active | suspended | merged
  merged_into   uuid null references users(id)    -- si status = 'merged'
  created_at    timestamptz not null default now()
  updated_at    timestamptz not null default now()

user_identities
  id                  uuid PK default gen_random_uuid()
  user_id             uuid not null references users(id) on delete cascade
  provider            text not null   -- 'telegram' | 'google' | 'phone' | 'discord'
  provider_user_id    text not null   -- telegram_user_id, google sub, E.164, discord id
  verified            boolean not null default true
  provider_metadata   jsonb not null default '{}'  -- username, email, locale, etc.
  linked_at           timestamptz not null default now()
  last_used_at        timestamptz not null default now()

  unique (provider, provider_user_id)   -- una identidad de proveedor -> un solo user
```

Reglas del modelo:

- Ningún dato de negocio (favoritos, alertas, settlements) referencia
  `provider_user_id` directamente. Todo referencia `users.id`.
- `(provider, provider_user_id)` es único: la misma cuenta de Google no
  puede terminar vinculada a dos `users` distintos.
- Un `user` puede tener cero identidades verificadas de un proveedor y una o
  más de otros; puede tener varias identidades del mismo proveedor solo si el
  proveedor lo permite de forma nativa (no es el caso inicial).
- `verified` distingue proveedores que ya verifican por diseño (Telegram,
  Google, Discord vía OAuth) de teléfono, donde `verified=true` solo se marca
  tras confirmar el OTP.

## Migración desde el estado actual

No se ejecuta en esta propuesta; se documenta la ruta para cuando se
apruebe:

1. Crear `users` y `user_identities`.
2. Backfill 1:1: por cada fila de `miniapp_users`, crear un `users` nuevo y
   un `user_identities` con `provider='telegram'`,
   `provider_user_id = telegram_user_id::text`, `verified=true`.
3. Añadir `miniapp_favorites.owner_user_id` y
   `alert_subscriptions.owner_user_id` (uuid, FK a `users.id`), poblarlos por
   join contra el backfill del paso 2, y solo después de verificar paridad de
   conteos, retirar las columnas `user_id` viejas (o dejarlas como
   solo-lectura histórica; decisión operativa, no técnica).
4. `miniapp_users` se conserva como tabla de perfil de Telegram (username,
   nombre, idioma) referenciada por `user_identities.provider_user_id`, no
   como identidad primaria.
5. Cambiar `session.userId` de `telegram_user_id` numérico a `users.id`
   (uuid). Esto es un cambio de contrato de sesión: invalida todas las
   sesiones activas al desplegar (aceptable, son sesiones de 12h).

Cada paso debe ser una migración numerada independiente siguiendo la
convención existente (`sql/migrations/0NN_*.sql` + `0NN_verify_*.sql`), igual
que las migraciones 001–013 ya en el repo.

## Flujo de login por canal

- **Telegram** (ya resuelto, sin cambios de validación): `initData` → HMAC
  contra `TELEGRAM_BOT_TOKEN` → resolver o crear
  `user_identities(provider='telegram')` → resolver o crear `users` →
  emitir sesión con `users.id`.
- **Google**: Google Identity Services en el cliente entrega un `id_token`
  JWT. El backend lo verifica contra las claves públicas de Google
  (`aud` = client id de DIKAMAHA, `exp` no vencido, firma RS256 válida) y usa
  el claim `sub` (estable, no el email) como `provider_user_id`. El email se
  guarda en `provider_metadata`, no como identificador.
- **Teléfono**: flujo OTP de dos pasos — `POST /session/phone/start` (envía
  código vía proveedor SMS) y `POST /session/phone/verify` (confirma código,
  ventana de expiración corta, límite de intentos). Solo tras verificar se
  crea `user_identities(provider='phone', verified=true)`. Tiene costo por
  SMS; no se activa hasta que haya un caso de negocio que lo justifique.
- **Discord** (si se prioriza): OAuth2 estándar, `id` de Discord como
  `provider_user_id`, mismo patrón que Google.

Todos comparten el mismo emisor de sesión (`issueSession`) una vez resuelto
el `users.id`; no se duplica lógica de sesión por proveedor.

## Vinculación de cuentas (linking)

Caso: un usuario ya tiene sesión activa (por cualquier proveedor) y quiere
añadir otro método de acceso.

- Endpoint `POST /api/session/link` — requiere sesión vigente + CSRF válido.
- Valida la credencial del nuevo proveedor igual que en login (HMAC,
  `id_token`, OTP).
- Si `(provider, provider_user_id)` no existe: crea `user_identities` para
  el `users.id` de la sesión actual. Éxito.
- Si `(provider, provider_user_id)` ya existe y pertenece al mismo
  `users.id`: no-op idempotente.
- Si ya existe y pertenece a **otro** `users.id`: rechazar
  (`identity_already_linked_elsewhere`). No fusionar cuentas de forma
  automática — dos personas podrían compartir un número de teléfono
  reciclado o un error de usuario podría fusionar datos de dos personas
  reales. La fusión manual queda fuera de alcance de v1 y, si se necesita,
  debe ser un flujo de soporte explícito y auditado, no automático.

## Reglas de control

- Ningún endpoint de negocio (favoritos, alertas) debe volver a aceptar un
  identificador de proveedor como `user_id`; siempre `users.id`.
- Ninguna identidad se marca `verified=true` sin verificación criptográfica
  o de posesión (HMAC de Telegram, firma de `id_token`, OTP confirmado).
- El linking nunca fusiona dos `users` existentes de forma implícita.
- `merged_into` es el único mecanismo de fusión, y solo se escribe mediante
  una operación explícita y auditada (fuera de alcance de v1).
- Cambiar el contrato de `session.userId` de número a uuid es un cambio
  incompatible: requiere invalidar sesiones activas en el despliegue, no
  una migración silenciosa.

## Fuera de alcance de esta propuesta

- Fusión de cuentas duplicadas.
- Teléfono/SMS como proveedor activo (se deja diseñado, no implementado).
- Cualquier flujo de recuperación de cuenta sin proveedor externo
  (contraseña propia) — no se contempla; toda identidad depende de un
  proveedor externo que ya verifica.

## Próximos pasos si se aprueba

1. Migraciones `users` + `user_identities` (paso 1–2 de la sección de
   migración), sin tocar `miniapp_users` todavía.
2. Backfill y verificación de paridad (paso 2–3).
3. Repunteo de FKs de `miniapp_favorites` / `alert_subscriptions` (paso 3).
4. Cambio de contrato de sesión (paso 5) + endpoint de login Telegram
   adaptado a `users.id`.
5. Recién en una fase posterior, nuevo proveedor (Google primero, por ser el
   más probable para el canal web).

Cada paso debería entrar como su propia fase con criterios de salida y
evidencia, siguiendo la misma disciplina que el resto del proyecto — no se
implementa nada de lo anterior sin luz verde explícita.
