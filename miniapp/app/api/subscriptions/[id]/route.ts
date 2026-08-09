import { and, eq, ne } from "drizzle-orm";
import { NextRequest, NextResponse } from "next/server";

import { database } from "@/lib/db";
import { alertSubscriptions } from "@/lib/db/schema";
import { authError, authorizeRequest, jsonError } from "@/lib/http";
import { subscriptionPatchSchema, subscriptionSchema } from "@/lib/validation";

type Context = { params: Promise<{ id: string }> };

export async function PATCH(request: NextRequest, context: Context) {
  try {
    const session = await authorizeRequest(request, true);
    const { id } = await context.params;
    const parsed = subscriptionPatchSchema.safeParse(await request.json());
    if (!parsed.success) return jsonError("subscription_invalid", 422);
    const [current] = await database().select().from(alertSubscriptions)
      .where(and(
        eq(alertSubscriptions.id, id),
        eq(alertSubscriptions.userId, session.userId),
      ));
    if (!current) return jsonError("subscription_not_found", 404);
    const complete = subscriptionSchema.safeParse({
      ruleType: current.ruleType,
      fixtureId: current.fixtureId ?? undefined,
      leagueSlug: current.leagueSlug,
      marketKey: current.marketKey ?? undefined,
      period: current.period ?? undefined,
      selection: current.selection ?? undefined,
      comparator: current.comparator ?? undefined,
      threshold: current.threshold === null ? undefined : Number(current.threshold),
      cooldownSeconds: current.cooldownSeconds,
      enabled: current.enabled,
      ...parsed.data,
    });
    if (!complete.success) return jsonError("subscription_invalid", 422);
    if (complete.data.enabled === true) {
      const active = await database().select({ id: alertSubscriptions.id })
        .from(alertSubscriptions)
        .where(and(
          eq(alertSubscriptions.userId, session.userId),
          eq(alertSubscriptions.enabled, true),
          ne(alertSubscriptions.id, id),
        ));
      if (active.length >= 20) return jsonError("subscription_limit_reached", 409);
    }
    const values = {
      ...parsed.data,
      threshold: parsed.data.threshold?.toString(),
      updatedAt: new Date(),
    };
    const [updated] = await database().update(alertSubscriptions)
      .set(values)
      .where(and(
        eq(alertSubscriptions.id, id),
        eq(alertSubscriptions.userId, session.userId),
      ))
      .returning();
    if (!updated) return jsonError("subscription_not_found", 404);
    return NextResponse.json({ subscription: updated });
  } catch (error) {
    return authError(error);
  }
}

export async function DELETE(request: NextRequest, context: Context) {
  try {
    const session = await authorizeRequest(request, true);
    const { id } = await context.params;
    const [deleted] = await database().delete(alertSubscriptions)
      .where(and(
        eq(alertSubscriptions.id, id),
        eq(alertSubscriptions.userId, session.userId),
      ))
      .returning({ id: alertSubscriptions.id });
    if (!deleted) return jsonError("subscription_not_found", 404);
    return NextResponse.json({ status: "deleted" });
  } catch (error) {
    return authError(error);
  }
}
