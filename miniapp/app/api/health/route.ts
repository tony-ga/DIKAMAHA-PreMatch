import { NextResponse } from "next/server";

import { databaseHealth } from "@/lib/db";
import { dikamahaRequest } from "@/lib/dikamaha";

export async function GET() {
  const [database, upstream] = await Promise.all([
    databaseHealth(),
    dikamahaRequest("/v1/explorer/leagues")
      .then((payload) => Array.isArray((payload as { leagues?: unknown }).leagues))
      .catch(() => false),
  ]);
  const ready = database && upstream;
  return NextResponse.json(
    { status: ready ? "ready" : "not_ready", database, upstream },
    { status: ready ? 200 : 503 },
  );
}
