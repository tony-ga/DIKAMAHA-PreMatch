import {
  bigint,
  boolean,
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
  // Gancho de titularidad para un futuro nivel de pago. Hoy sólo se transporta.
  plan: text("plan").default("free").notNull(),
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

export type AlertSubscription = typeof alertSubscriptions.$inferSelect;
export type PredictionSettlement = typeof predictionSettlements.$inferSelect;
