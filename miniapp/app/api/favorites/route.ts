import { and, desc, eq } from "drizzle-orm";
import { NextRequest, NextResponse } from "next/server";

import { resolveEntitlement } from "@/lib/auth/entitlements";
import { database } from "@/lib/db";
import { miniappFavorites } from "@/lib/db/schema";
import { authError, authorizeRequest, jsonError } from "@/lib/http";
import { favoriteSchema } from "@/lib/validation";

export async function GET(request: NextRequest) {
  try {
    const session = await authorizeRequest(request);
    const rows = await database().select().from(miniappFavorites)
      .where(eq(miniappFavorites.userId, session.userId))
      .orderBy(desc(miniappFavorites.createdAt));
    return NextResponse.json({ favorites: rows });
  } catch (error) {
    return authError(error);
  }
}

export async function POST(request: NextRequest) {
  try {
    const session = await authorizeRequest(request, true);
    const parsed = favoriteSchema.safeParse(await request.json());
    if (!parsed.success) return jsonError("favorite_invalid", 422);
    const existing = await database().select({
      entityType: miniappFavorites.entityType,
      entityId: miniappFavorites.entityId,
    })
      .from(miniappFavorites)
      .where(eq(miniappFavorites.userId, session.userId));
    const duplicate = existing.some((row) => (
      row.entityType === parsed.data.entityType
      && row.entityId === parsed.data.entityId
    ));
    // El tope vive duplicado -aquí y en el trigger `enforce_miniapp_favorite_limit`-
    // desde la migración 0000. Las dos comprobaciones tienen que consultar el
    // mismo plan: relajar sólo ésta dejaría al trigger rechazando igual y el
    // suscriptor recibiría un 500 en vez de un 409.
    const entitlement = await resolveEntitlement(session.userId);
    if (entitlement.plan !== "premium" && !duplicate && existing.length >= 10) {
      return jsonError("favorite_limit_reached", 409);
    }
    await database().insert(miniappFavorites).values({
      userId: session.userId,
      ...parsed.data,
    }).onConflictDoUpdate({
      target: [miniappFavorites.userId, miniappFavorites.entityType, miniappFavorites.entityId],
      set: { label: parsed.data.label, metadata: parsed.data.metadata },
    });
    return NextResponse.json({ status: "saved" }, { status: 201 });
  } catch (error) {
    return authError(error);
  }
}

export async function DELETE(request: NextRequest) {
  try {
    const session = await authorizeRequest(request, true);
    const entityType = request.nextUrl.searchParams.get("entityType") ?? "";
    const entityId = request.nextUrl.searchParams.get("entityId") ?? "";
    await database().delete(miniappFavorites).where(and(
      eq(miniappFavorites.userId, session.userId),
      eq(miniappFavorites.entityType, entityType),
      eq(miniappFavorites.entityId, entityId),
    ));
    return NextResponse.json({ status: "deleted" });
  } catch (error) {
    return authError(error);
  }
}
