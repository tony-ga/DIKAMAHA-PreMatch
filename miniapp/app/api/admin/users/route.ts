import { sql } from "drizzle-orm";
import { NextRequest, NextResponse } from "next/server";

import { resolveEntitlement } from "@/lib/auth/entitlements";
import { database } from "@/lib/db";
import { authError, authorizeRequest } from "@/lib/http";

/**
 * Lista de cuentas para el panel de administración.
 *
 * Hasta esta ruta, ver quién usa la Mini App -y quién es premium, y quién es
 * administrador- exigía una consulta SQL directa contra Railway. Es la misma
 * fila que ya gobierna `resolveEntitlement`; esto sólo la expone.
 */
export async function GET(request: NextRequest) {
  try {
    const session = await authorizeRequest(request);
    const entitlement = await resolveEntitlement(session.userId);
    if (entitlement.role !== "admin") throw new Error("admin_required");

    const rows = await database().execute<{
      telegram_user_id: number;
      username: string | null;
      first_name: string;
      status: string;
      role: string;
      plan: string;
      plan_source: string;
      plan_expires_at: string | Date | null;
      first_seen_at: string | Date;
      last_seen_at: string | Date;
    }>(sql`
      SELECT "telegram_user_id", "username", "first_name", "status", "role",
             "plan", "plan_source", "plan_expires_at",
             "first_seen_at", "last_seen_at"
        FROM "miniapp_users"
       ORDER BY "last_seen_at" DESC
       LIMIT 500
    `);

    return NextResponse.json({
      users: rows.map((row) => ({
        telegramUserId: row.telegram_user_id,
        username: row.username,
        firstName: row.first_name,
        status: row.status,
        role: row.role,
        plan: row.plan,
        planSource: row.plan_source,
        planExpiresAt: row.plan_expires_at
          ? new Date(row.plan_expires_at).toISOString() : null,
        firstSeenAt: new Date(row.first_seen_at).toISOString(),
        lastSeenAt: new Date(row.last_seen_at).toISOString(),
      })),
    });
  } catch (error) {
    return authError(error);
  }
}
