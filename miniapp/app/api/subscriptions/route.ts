import { desc, eq } from "drizzle-orm";
import { NextRequest, NextResponse } from "next/server";

import { resolveEntitlement } from "@/lib/auth/entitlements";
import { database } from "@/lib/db";
import { alertSubscriptions } from "@/lib/db/schema";
import { authError, authorizeRequest, jsonError } from "@/lib/http";
import { subscriptionSchema } from "@/lib/validation";

export async function GET(request: NextRequest) {
  try {
    const session = await authorizeRequest(request);
    const rows = await database().select().from(alertSubscriptions)
      .where(eq(alertSubscriptions.userId, session.userId))
      .orderBy(desc(alertSubscriptions.createdAt));
    return NextResponse.json({ subscriptions: rows });
  } catch (error) {
    return authError(error);
  }
}

export async function POST(request: NextRequest) {
  try {
    const session = await authorizeRequest(request, true);
    const parsed = subscriptionSchema.safeParse(await request.json());
    if (!parsed.success) return jsonError("subscription_invalid", 422);
    const rows = await database().select({ enabled: alertSubscriptions.enabled })
      .from(alertSubscriptions)
      .where(eq(alertSubscriptions.userId, session.userId));
    // Mismo tope duplicado que en favoritos: ruta y trigger
    // (`enforce_alert_subscription_limit`) deben leer el mismo plan.
    const entitlement = await resolveEntitlement(session.userId);
    if (
      entitlement.plan !== "premium"
      && parsed.data.enabled
      && rows.filter((row) => row.enabled).length >= 20
    ) {
      return jsonError("subscription_limit_reached", 409);
    }
    const [created] = await database().insert(alertSubscriptions).values({
      userId: session.userId,
      ...parsed.data,
      threshold: parsed.data.threshold?.toString(),
    }).returning();
    return NextResponse.json({ subscription: created }, { status: 201 });
  } catch (error) {
    return authError(error);
  }
}
