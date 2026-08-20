import {
  bigint,
  boolean,
  date,
  index,
  integer,
  jsonb,
  numeric,
  pgTable,
  primaryKey,
  text,
  timestamp,
  uniqueIndex,
  uuid,
} from "drizzle-orm/pg-core";

/** Estados de una cuenta. `pending` es el estado inicial de todo alta nueva. */
export type AccountStatus = "pending" | "active" | "blocked";
export type AccountRole = "user" | "admin";

/** Niveles de producto. Sólo dos, y `free` es siempre un nivel servible. */
export type AccountPlan = "free" | "premium";

/**
 * De dónde viene el plan actual.
 *
 * Existe para que un backfill se pueda repetir sin pisar una compra real, y
 * para que la UI pueda ser honesta con quien heredó el acceso en el corte en
 * lugar de sorprenderlo el día del vencimiento.
 */
// `stripe` entra en Fase 133: el mismo plan comprado desde el sitio web. La
// procedencia se conserva por pasarela para poder atribuir un reembolso a la
// correcta -y para que la exclusión mutua de DEC-220 sea comprobable-.
export type PlanSource =
  | "default" | "stars" | "stripe" | "grandfathered" | "admin" | "refunded";

export const miniappUsers = pgTable("miniapp_users", {
  telegramUserId: bigint("telegram_user_id", { mode: "number" }).primaryKey(),
  username: text("username"),
  firstName: text("first_name").notNull(),
  lastName: text("last_name"),
  languageCode: text("language_code"),
  firstSeenAt: timestamp("first_seen_at", { withTimezone: true }).defaultNow().notNull(),
  lastSeenAt: timestamp("last_seen_at", { withTimezone: true }).defaultNow().notNull(),
  // El acceso vivía en la variable de entorno `TELEGRAM_ALLOWED_USER_IDS`, así
  // que dar de alta a alguien exigía redesplegar el servicio. Aquí pasa a ser
  // un UPDATE.
  status: text("status").$type<AccountStatus>().default("pending").notNull(),
  role: text("role").$type<AccountRole>().default("user").notNull(),
  // Nivel de producto. Dejó de ser un gancho inerte en la Fase 125: junto a
  // `planExpiresAt` decide qué se sirve. La autoridad es `resolveEntitlement`
  // (`lib/auth/entitlements.ts`), que lee esta fila; el `plan` que viaja en la
  // cookie es sólo una pista para el primer pintado.
  plan: text("plan").$type<AccountPlan>().default("free").notNull(),
  // Nulo significa "sin suscripción vigente". Un premium con esta columna
  // vencida es free desde la lectura siguiente, sin esperar a ningún barrido.
  planExpiresAt: timestamp("plan_expires_at", { withTimezone: true }),
  planSource: text("plan_source").$type<PlanSource>().default("default").notNull(),
  planUpdatedAt: timestamp("plan_updated_at", { withTimezone: true })
    .defaultNow().notNull(),
  approvedAt: timestamp("approved_at", { withTimezone: true }),
  approvedBy: bigint("approved_by", { mode: "number" }),
});

export type MiniappUser = typeof miniappUsers.$inferSelect;

export const miniappFavorites = pgTable("miniapp_favorites", {
  userId: bigint("user_id", { mode: "number" })
    .notNull()
    .references(() => miniappUsers.telegramUserId, { onDelete: "cascade" }),
  entityType: text("entity_type").notNull(),
  entityId: text("entity_id").notNull(),
  label: text("label").notNull(),
  metadata: jsonb("metadata").$type<Record<string, unknown>>().default({}).notNull(),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
}, (table) => [
  primaryKey({ columns: [table.userId, table.entityType, table.entityId] }),
]);

export const alertSubscriptions = pgTable("alert_subscriptions", {
  id: uuid("id").defaultRandom().primaryKey(),
  userId: bigint("user_id", { mode: "number" })
    .notNull()
    .references(() => miniappUsers.telegramUserId, { onDelete: "cascade" }),
  ruleType: text("rule_type").notNull(),
  fixtureId: text("fixture_id"),
  leagueSlug: text("league_slug").notNull(),
  marketKey: text("market_key"),
  period: text("period"),
  selection: text("selection"),
  comparator: text("comparator"),
  threshold: numeric("threshold", { precision: 8, scale: 6 }),
  cooldownSeconds: integer("cooldown_seconds").default(300).notNull(),
  enabled: boolean("enabled").default(true).notNull(),
  lastObservation: jsonb("last_observation").$type<Record<string, unknown> | null>(),
  lastEvaluatedAt: timestamp("last_evaluated_at", { withTimezone: true }),
  lastTriggeredAt: timestamp("last_triggered_at", { withTimezone: true }),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
  updatedAt: timestamp("updated_at", { withTimezone: true }).defaultNow().notNull(),
});

export const alertDeliveries = pgTable("alert_deliveries", {
  id: uuid("id").defaultRandom().primaryKey(),
  subscriptionId: uuid("subscription_id")
    .notNull()
    .references(() => alertSubscriptions.id, { onDelete: "cascade" }),
  eventKey: text("event_key").notNull(),
  status: text("status").notNull(),
  errorCode: text("error_code"),
  deliveredAt: timestamp("delivered_at", { withTimezone: true }),
}, (table) => [
  uniqueIndex("alert_delivery_subscription_event_uidx")
    .on(table.subscriptionId, table.eventKey),
]);

export type MarketVerdict = {
  predicted: string;
  actual: string;
  hit: boolean;
};

export const predictionSettlements = pgTable("prediction_settlements", {
  fixtureKey: text("fixture_key").primaryKey(),
  leagueSlug: text("league_slug").notNull(),
  matchId: bigint("match_id", { mode: "number" }).notNull(),
  competitionId: text("competition_id").notNull(),
  kickoffTs: timestamp("kickoff_ts", { withTimezone: true }).notNull(),
  settledAt: timestamp("settled_at", { withTimezone: true }).defaultNow().notNull(),
  homeTeamName: text("home_team_name").notNull(),
  awayTeamName: text("away_team_name").notNull(),
  scoreHome: integer("score_home").notNull(),
  scoreAway: integer("score_away").notNull(),
  predictionHash: text("prediction_hash").notNull(),
  officialVerdicts: jsonb("official_verdicts")
    .$type<Record<string, MarketVerdict>>().default({}).notNull(),
  shadowVerdicts: jsonb("shadow_verdicts")
    .$type<Record<string, MarketVerdict>>().default({}).notNull(),
  contractVersion: text("contract_version"),
});

/**
 * Tarjeta pre-match congelada para compartir por link.
 *
 * `payload` es la tarjeta ya resuelta (`ShareCard`, `lib/share-card.ts`), no
 * el fixture: el link comparte lo que el modelo dijo antes del kickoff y
 * reabrirlo no puede devolver otra cosa. Una fila por partido, para que dos
 * personas que comparten el mismo encuentro difundan la misma imagen.
 */
export const sharedPredictionCards = pgTable("shared_prediction_cards", {
  fixtureKey: text("fixture_key").primaryKey(),
  token: text("token").notNull(),
  leagueSlug: text("league_slug").notNull(),
  matchId: bigint("match_id", { mode: "number" }).notNull(),
  homeTeamName: text("home_team_name").notNull(),
  awayTeamName: text("away_team_name").notNull(),
  kickoffTs: timestamp("kickoff_ts", { withTimezone: true }).notNull(),
  payload: jsonb("payload").$type<Record<string, unknown>>().default({}).notNull(),
  createdBy: bigint("created_by", { mode: "number" }),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
}, (table) => [
  uniqueIndex("shared_prediction_cards_token_uidx").on(table.token),
]);

/**
 * Precio vigente del nivel de pago.
 *
 * Vive en la base y no en el entorno porque la economía de Stars puede moverse
 * y el punto de equilibrio del proyecto está en ~15 suscriptores: ajustar el
 * precio no puede exigir publicar una versión. `MINIAPP_PREMIUM_STARS_PRICE`
 * sólo siembra la fila la primera vez.
 */
export const billingPlans = pgTable("billing_plans", {
  code: text("code").primaryKey(),
  starsAmount: integer("stars_amount").notNull(),
  // Telegram sólo admite 2592000 (30 días) para suscripciones Stars.
  periodSeconds: integer("period_seconds").notNull(),
  active: boolean("active").default(true).notNull(),
  updatedAt: timestamp("updated_at", { withTimezone: true }).defaultNow().notNull(),
});

export type SubscriptionStatus = "active" | "canceled" | "expired" | "refunded";

/**
 * Suscripción Stars viva de un usuario, una fila por persona.
 *
 * `latestChargeId` persigue el cargo más reciente porque
 * `editUserStarSubscription` exige ése para cancelar: el de la primera compra
 * deja de servir en cuanto llega la primera renovación.
 */
export const starSubscriptions = pgTable("star_subscriptions", {
  userId: bigint("user_id", { mode: "number" })
    .primaryKey()
    .references(() => miniappUsers.telegramUserId, { onDelete: "cascade" }),
  planCode: text("plan_code").default("premium_monthly").notNull(),
  latestChargeId: text("latest_charge_id").notNull(),
  firstChargeId: text("first_charge_id").notNull(),
  starsAmount: integer("stars_amount").notNull(),
  currentPeriodEnd: timestamp("current_period_end", { withTimezone: true }).notNull(),
  status: text("status").$type<SubscriptionStatus>().default("active").notNull(),
  cancelRequestedAt: timestamp("cancel_requested_at", { withTimezone: true }),
  chargeCount: integer("charge_count").default(1).notNull(),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
  updatedAt: timestamp("updated_at", { withTimezone: true }).defaultNow().notNull(),
}, (table) => [
  uniqueIndex("star_subscriptions_latest_charge_uidx").on(table.latestChargeId),
]);

export type PaymentKind = "charge" | "refund";
export type PaymentIngestSource = "bot_forward" | "reconcile" | "manual";

/**
 * Libro mayor de cargos y reembolsos Stars.
 *
 * La clave primaria es la natural de Telegram, y eso es lo que convierte
 * "aplicar un pago" en una operación idempotente: el reenvío del bot, su
 * reintento y la reconciliación por `getStarTransactions` pueden llegar en
 * cualquier orden y convergen al mismo estado.
 */
export const starPayments = pgTable("star_payments", {
  telegramPaymentChargeId: text("telegram_payment_charge_id").primaryKey(),
  userId: bigint("user_id", { mode: "number" })
    .notNull()
    .references(() => miniappUsers.telegramUserId, { onDelete: "cascade" }),
  kind: text("kind").$type<PaymentKind>().notNull(),
  starsAmount: integer("stars_amount").notNull(),
  invoicePayload: text("invoice_payload"),
  isRecurring: boolean("is_recurring").default(false).notNull(),
  isFirstRecurring: boolean("is_first_recurring").default(false).notNull(),
  subscriptionExpiration: timestamp("subscription_expiration", { withTimezone: true }),
  // `reconcile` significa que el reenvío del bot se perdió. Es una señal
  // operativa, no una estadística: ver el runbook.
  ingestSource: text("ingest_source").$type<PaymentIngestSource>().notNull(),
  rawPayload: jsonb("raw_payload").$type<Record<string, unknown>>().default({}).notNull(),
  appliedAt: timestamp("applied_at", { withTimezone: true }).defaultNow().notNull(),
}, (table) => [
  index("star_payments_user_idx").on(table.userId, table.appliedAt),
]);

/**
 * Facturas emitidas.
 *
 * Ata un payload firmado a un usuario y a un precio concretos. Sin esta tabla
 * el payload sería sólo una afirmación firmada y no habría forma de auditar
 * qué se le ofreció a quién ni por cuánto.
 */
export const starInvoices = pgTable("star_invoices", {
  nonce: text("nonce").primaryKey(),
  userId: bigint("user_id", { mode: "number" })
    .notNull()
    .references(() => miniappUsers.telegramUserId, { onDelete: "cascade" }),
  planCode: text("plan_code").notNull(),
  starsAmount: integer("stars_amount").notNull(),
  origin: text("origin").$type<"miniapp" | "bot">().notNull(),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
  consumedAt: timestamp("consumed_at", { withTimezone: true }),
});

/**
 * Contador diario de predicciones del plan gratuito.
 *
 * `dailyLimit` se congela en la fila al crearla: subir el tope a mitad de día
 * no puede encoger retroactivamente un día que ya se le prometió a alguien.
 */
export const predictionQuotaDays = pgTable("prediction_quota_days", {
  userId: bigint("user_id", { mode: "number" })
    .notNull()
    .references(() => miniappUsers.telegramUserId, { onDelete: "cascade" }),
  usageDate: date("usage_date").notNull(),
  used: integer("used").default(0).notNull(),
  dailyLimit: integer("daily_limit").notNull(),
  updatedAt: timestamp("updated_at", { withTimezone: true }).defaultNow().notNull(),
}, (table) => [
  primaryKey({ columns: [table.userId, table.usageDate] }),
]);

/**
 * Partidos que un usuario gratuito desbloqueó hoy.
 *
 * Existe porque la Mini App reconsulta al recuperar el foco
 * (`refetchOnWindowFocus`, `components/providers.tsx`): una cuota contada por
 * petición se consumiría sola. La promesa es "3 predicciones al día de tu
 * elección", no "3 peticiones HTTP", así que reabrir un partido ya concedido
 * es gratis.
 */
export const predictionGrants = pgTable("prediction_grants", {
  userId: bigint("user_id", { mode: "number" })
    .notNull()
    .references(() => miniappUsers.telegramUserId, { onDelete: "cascade" }),
  usageDate: date("usage_date").notNull(),
  // `league_slug:match_id`, la misma forma que produce `shareFixtureKey`, para
  // que Mini App, bot y tarjeta compartida cuenten lo mismo.
  fixtureKey: text("fixture_key").notNull(),
  surface: text("surface").$type<"miniapp" | "bot" | "share">().notNull(),
  grantedAt: timestamp("granted_at", { withTimezone: true }).defaultNow().notNull(),
}, (table) => [
  primaryKey({ columns: [table.userId, table.usageDate, table.fixtureKey] }),
]);

export type AlertSubscription = typeof alertSubscriptions.$inferSelect;
export type PredictionSettlement = typeof predictionSettlements.$inferSelect;
export type SharedPredictionCard = typeof sharedPredictionCards.$inferSelect;
export type BillingPlan = typeof billingPlans.$inferSelect;
export type StarSubscription = typeof starSubscriptions.$inferSelect;
export type StarPayment = typeof starPayments.$inferSelect;
