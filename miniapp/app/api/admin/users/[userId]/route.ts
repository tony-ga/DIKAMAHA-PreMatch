import { sql } from "drizzle-orm";
import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";

import { invalidateEntitlement, resolveEntitlement } from "@/lib/auth/entitlements";
import { database } from "@/lib/db";
import { authError, authorizeRequest, jsonError } from "@/lib/http";

const schema = z.object({
  status: z.enum(["active", "blocked"]).optional(),
  role: z.enum(["user", "admin"]).optional(),
  // `premium` concede un plan perpetuo con `plan_source='admin'` -el mismo
  // valor que ya usa el runbook por SQL directo-, revisable a mano y sin
  // caducidad automática, porque no proviene de un cobro real. `free` revoca
  // cualquier plan, sea cual sea su procedencia.
  plan: z.enum(["free", "premium"]).optional(),
});

/**
 * Alta, bloqueo, rol y plan manual de una cuenta.
 *
 * Antes de esta ruta, todo esto -incluida la aprobación de una cuenta
 * `pending`- se hacía con un `UPDATE` manual desde el panel de datos de
 * Railway (ver `docs/runbooks/telegram_stars_subscriptions.md`, "Alta y baja
 * manual"). Esa vía sigue siendo válida; ésta es la misma operación desde la
 * Mini App.
 */
export async function PATCH(
  request: NextRequest,
  { params }: { params: Promise<{ userId: string }> },
) {
  try {
    const session = await authorizeRequest(request, true);
    const admin = await resolveEntitlement(session.userId);
    if (admin.role !== "admin") throw new Error("admin_required");

    const { userId: rawUserId } = await params;
    const userId = Number(rawUserId);
    if (!Number.isSafeInteger(userId) || userId <= 0) {
      return jsonError("user_id_invalid", 422);
    }

    const parsed = schema.safeParse(await request.json());
    if (!parsed.success) return jsonError("admin_user_update_invalid", 422);
    const changes = parsed.data;
    if (Object.keys(changes).length === 0) {
      return jsonError("admin_user_update_empty", 422);
    }

    // Nadie puede quitarse a sí mismo el rol de administrador desde este
    // panel: haría falta volver a SQL para deshacerlo, y es exactamente el
    // tipo de error que se comete una sola vez.
    if (changes.role === "user" && userId === session.userId) {
      return jsonError("admin_cannot_demote_self", 409);
    }

    if (changes.status) {
      await database().execute(sql`
        UPDATE "miniapp_users"
           SET "status" = ${changes.status},
               "approved_at" = CASE WHEN ${changes.status} = 'active'
                                     THEN COALESCE("approved_at", now())
                                     ELSE "approved_at" END,
               "approved_by" = CASE WHEN ${changes.status} = 'active'
                                     THEN COALESCE("approved_by", ${session.userId})
                                     ELSE "approved_by" END
         WHERE "telegram_user_id" = ${userId}
      `);
    }
    if (changes.role) {
      await database().execute(sql`
        UPDATE "miniapp_users" SET "role" = ${changes.role}
         WHERE "telegram_user_id" = ${userId}
      `);
    }
    if (changes.plan === "premium") {
      await database().execute(sql`
        UPDATE "miniapp_users"
           SET "plan" = 'premium', "plan_source" = 'admin',
               "plan_expires_at" = NULL, "plan_updated_at" = now()
         WHERE "telegram_user_id" = ${userId}
      `);
    } else if (changes.plan === "free") {
      await database().execute(sql`
        UPDATE "miniapp_users"
           SET "plan" = 'free', "plan_source" = 'default',
               "plan_expires_at" = NULL, "plan_updated_at" = now()
         WHERE "telegram_user_id" = ${userId}
      `);
    }

    invalidateEntitlement(userId);
    console.info(JSON.stringify({
      event: "admin_user_updated", target_user_id: userId,
      by_user_id: session.userId, changes,
    }));
    return NextResponse.json({ status: "updated" });
  } catch (error) {
    return authError(error);
  }
}
