import { z } from "zod";

function safeApiUrl(value: string): boolean {
  // Tolerante a lo que no es una URL porque también valida
  // `MINIAPP_PUBLIC_WEB_URL`, que llega sin pasar antes por `z.url()`: sin esto
  // un dominio mal escrito reventaría el arranque con un TypeError en vez de
  // con el error de configuración correspondiente.
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return false;
  }
  if (url.protocol === "https:") return true;
  return process.env.NODE_ENV !== "production"
    && url.protocol === "http:"
    && ["127.0.0.1", "localhost", "::1"].includes(url.hostname);
}

const schema = z.object({
  TELEGRAM_BOT_TOKEN: z.string().min(20),
  TELEGRAM_ACCESS_MODE: z.enum(["private", "public"]).default("private"),
  TELEGRAM_ALLOWED_USER_IDS: z.string().default(""),
  // Nombre del bot sin `@`. Lo exige el Login Widget, que es lo único que
  // permite entrar desde un navegador: el WebView no lo necesita, así que sin
  // superficie web esto queda vacío y no estorba.
  TELEGRAM_BOT_USERNAME: z.string().default(""),
  // Dominio propio del sitio. Vacío = sólo Mini App, que es el estado previo a
  // la Fase 133. Su presencia es lo que declara abierta la superficie web.
  MINIAPP_PUBLIC_WEB_URL: z.string().default(""),
  DIKAMAHA_BOT_API_URL: z.string().url().refine(safeApiUrl, "dikamaha_api_url_https_required"),
  DIKAMAHA_API_KEY: z.string().min(16),
  DATABASE_URL: z.string().min(1),
  MINIAPP_SESSION_SECRET: z.string().min(32),
  MINIAPP_ENABLED: z.enum(["true", "false"]).default("false"),
  MINIAPP_ALERTS_ENABLED: z.enum(["true", "false"]).default("false"),
  // Interruptor de cobro. Con `false` -el valor por defecto- nadie queda
  // gateado y `resolveEntitlement` devuelve premium sin tocar la base: es el
  // rollback de toda la Fase 125 sin redesplegar código.
  MINIAPP_BILLING_ENABLED: z.enum(["true", "false"]).default("false"),
  // Secreto compartido bot ↔ Mini App. Sin default a propósito, igual que
  // `MINIAPP_SESSION_SECRET`: el servicio debe negarse a arrancar antes que
  // servir endpoints internos con una clave adivinable.
  MINIAPP_INTERNAL_API_KEY: z.string().min(32),
  // Firma de los payloads de factura. Deliberadamente distinta de la de
  // sesión: el bot necesita ésta y no debe poder falsificar una sesión.
  MINIAPP_BILLING_SECRET: z.string().min(32),
  // Sólo semilla para `billing_plans`. El precio vigente en runtime sale de la
  // tabla, no de aquí, porque `env()` se cachea a nivel de módulo y cambiarlo
  // exigiría reiniciar.
  MINIAPP_PREMIUM_STARS_PRICE: z.coerce.number().int().min(1).max(10000).default(250),
  MINIAPP_FREE_DAILY_PREDICTIONS: z.coerce.number().int().min(0).max(50).default(3),
  // Mismo huso que el track record diario (Fase 121). Si el día del contador y
  // el día del producto discreparan, "3 al día" significaría dos cosas.
  MINIAPP_QUOTA_TIMEZONE: z.string().default("America/Mexico_City"),
  MINIAPP_BILLING_RECONCILE_SECONDS: z.coerce.number().int().min(60).max(86400)
    .default(900),
  // Interruptor del cobro web, hermano de `MINIAPP_BILLING_ENABLED`: con
  // `false` -el valor por defecto- la web vende igual que hoy, remitiendo a
  // Telegram, y ninguna ruta de Stripe hace nada.
  MINIAPP_STRIPE_ENABLED: z.enum(["true", "false"]).default("false"),
  STRIPE_SECRET_KEY: z.string().default(""),
  STRIPE_WEBHOOK_SECRET: z.string().default(""),
  STRIPE_PRICE_ID: z.string().default(""),
})
  // Los defaults vacíos existen para que un despliegue sólo-Telegram siga
  // arrancando sin conocer ninguna de estas variables. Lo que no puede pasar es
  // encender una superficie a medio configurar: con el interruptor puesto y sin
  // secretos, el servicio debe negarse a arrancar en vez de fallar en la cara
  // del primer usuario que intente pagar.
  .superRefine((value, ctx) => {
    if (value.MINIAPP_PUBLIC_WEB_URL && !value.TELEGRAM_BOT_USERNAME) {
      ctx.addIssue({
        code: "custom",
        message: "telegram_bot_username_required_for_web",
        path: ["TELEGRAM_BOT_USERNAME"],
      });
    }
    if (value.MINIAPP_PUBLIC_WEB_URL && !safeApiUrl(value.MINIAPP_PUBLIC_WEB_URL)) {
      ctx.addIssue({
        code: "custom",
        message: "miniapp_public_web_url_https_required",
        path: ["MINIAPP_PUBLIC_WEB_URL"],
      });
    }
    if (value.MINIAPP_STRIPE_ENABLED !== "true") return;
    for (const key of ["STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "STRIPE_PRICE_ID"] as const) {
      if (!value[key]) {
        ctx.addIssue({ code: "custom", message: `${key.toLowerCase()}_required`, path: [key] });
      }
    }
    if (!value.MINIAPP_PUBLIC_WEB_URL) {
      ctx.addIssue({
        code: "custom",
        message: "miniapp_public_web_url_required_for_stripe",
        path: ["MINIAPP_PUBLIC_WEB_URL"],
      });
    }
  });

export type MiniappEnv = z.infer<typeof schema>;

let cached: MiniappEnv | undefined;

export function env(): MiniappEnv {
  cached ??= schema.parse(process.env);
  return cached;
}

/**
 * Interruptor único del cobro.
 *
 * Se consulta antes de cualquier lectura de titularidad, de modo que con el
 * cobro apagado el comportamiento del servicio es idéntico al de antes de la
 * Fase 125 y no se emite ni una consulta extra.
 */
export function billingEnabled(): boolean {
  return env().MINIAPP_BILLING_ENABLED === "true";
}

/**
 * Interruptor del cobro web.
 *
 * Depende del cobro general: si nada se gatea, no hay nada que vender, y
 * cobrar por un producto que se está regalando sería el peor fallo posible de
 * esta fase.
 */
export function stripeEnabled(): boolean {
  return billingEnabled() && env().MINIAPP_STRIPE_ENABLED === "true";
}

/** Origen público del sitio, sin barra final. Vacío = sin superficie web. */
export function publicWebUrl(): string {
  return env().MINIAPP_PUBLIC_WEB_URL.replace(/\/+$/, "");
}

export function allowedUserIds(value = env().TELEGRAM_ALLOWED_USER_IDS): Set<number> {
  const ids = value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean)
    .map(Number);
  if (ids.some((id) => !Number.isSafeInteger(id) || id <= 0)) {
    throw new Error("telegram_allowed_user_ids_invalid");
  }
  return new Set(ids);
}
