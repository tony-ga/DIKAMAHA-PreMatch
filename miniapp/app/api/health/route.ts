import { NextResponse } from "next/server";

import { databaseHealth } from "@/lib/db";

export async function GET() {
  const database = await databaseHealth();
  return NextResponse.json(
    { status: database ? "ready" : "not_ready", database },
    { status: database ? 200 : 503 },
  );
}
