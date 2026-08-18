import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * Invariantes de las migraciones, verificadas sin base de datos.
 *
 * `scripts/migrate.ts` **no lleva registro de migraciones aplicadas**: lee el
 * directorio y reejecuta todos los `.sql` en cada despliegue. Eso convierte a
 * cada migración en código que corre indefinidamente, no en un evento único, y
 * es la propiedad que hay que defender aquí.
 *
 * Existe por un fallo real en producción: `0005_grandfather_active_accounts.sql`
 * concedía premium a toda cuenta `plan='free'` con `plan_source='default'`.
 * Como ese es el estado de cualquier alta nueva -y el que escribe el panel de
 * administración al degradar a alguien-, cada despliegue devolvía premium a
 * toda la base gratuita. Nadie lo notó hasta que una cuenta de prueba recuperó
 * sola todas las funciones de pago.
 */

const DIRECTORY = resolve(process.cwd(), "drizzle");

function migrations(): Array<{ name: string; sql: string }> {
  return readdirSync(DIRECTORY)
    .filter((name) => name.endsWith(".sql"))
    .sort()
    .map((name) => ({
      name,
      // Los comentarios de estos archivos citan las cláusulas que describen,
      // así que analizarlos sin quitarlos daría falsos positivos.
      sql: readFileSync(resolve(DIRECTORY, name), "utf8").replaceAll(/--[^\n]*/g, ""),
    }));
}

/** Cláusula `SET` de cada `UPDATE`, sin el `WHERE` que la filtra. */
function assignments(sql: string): string[] {
  return [...sql.matchAll(/\bUPDATE\b[\s\S]*?;/gi)].flatMap((statement) => {
    const text = statement[0];
    const set = text.search(/\bSET\b/i);
    if (set < 0) return [];
    const where = text.search(/\bWHERE\b/i);
    return [text.slice(set, where > set ? where : undefined)];
  });
}

describe("seguridad de las migraciones", () => {
  it("encuentra migraciones que analizar", () => {
    expect(migrations().length).toBeGreaterThan(0);
  });

  it("ninguna migración concede premium", () => {
    // La regla que faltaba. Conceder premium depende de un hecho externo -un
    // cobro con Stars, la decisión de un administrador- que una migración no
    // puede comprobar, y que además reevaluaría en cada despliegue. Es trabajo
    // exclusivo del código en runtime.
    for (const { name, sql } of migrations()) {
      for (const clause of assignments(sql)) {
        expect(clause, `${name} asigna premium en un UPDATE`).not.toMatch(/'premium'/i);
      }
    }
  });

  it("ninguna migración vuelve a conceder acceso heredado", () => {
    for (const { name, sql } of migrations()) {
      for (const clause of assignments(sql)) {
        expect(clause, `${name} asigna plan_source='grandfathered'`)
          .not.toMatch(/'grandfathered'/i);
      }
    }
  });

  it("la revocación del acceso heredado sigue presente", () => {
    // Si alguien borra este archivo, las filas `grandfathered` que puedan
    // quedar en una base antigua dejarían de revocarse en su próximo
    // despliegue.
    const revoke = migrations().find((row) => row.name.includes("revoke_grandfathered"));
    expect(revoke).toBeDefined();
    expect(revoke!.sql).toMatch(/plan_source"?\s*=\s*'grandfathered'/i);
  });
});
