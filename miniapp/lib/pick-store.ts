/**
 * Almacen de selecciones del Constructor de Picks (DEC-208).
 *
 * Vive en `localStorage` del dispositivo: una seleccion es un borrador del
 * usuario, no una prediccion congelada. No se persiste en Postgres, no se
 * liquida y no entra en el historial de aciertos ni en ningun gate de
 * promocion; convertirla en registro serviria para prometer un seguimiento
 * que el proyecto no ha medido.
 *
 * Es un store externo minimo -sin contexto de React- porque los botones "+"
 * viven repartidos por tres componentes de mercado y el menu vive en otra
 * ruta: un provider tendria que envolver toda la aplicacion para coordinar
 * algo que cabe en un `Set`.
 */

import type { Pick } from "@/lib/pick-builder";

const STORAGE_KEY = "dikamaha.pick-builder.v1";

/** Tope de selecciones. Mas alla, la conjunta es ruido y la pantalla no cabe. */
export const MAX_PICKS = 12;

let picks: Pick[] = [];
let loaded = false;
const listeners = new Set<() => void>();

function isPick(value: unknown): value is Pick {
  if (!value || typeof value !== "object") return false;
  const item = value as Record<string, unknown>;
  if (typeof item.id !== "string" || typeof item.label !== "string") return false;
  if (typeof item.probability !== "number" || !Number.isFinite(item.probability)) return false;
  if (!item.match || typeof item.match !== "object") return false;
  return item.kind === "goal" || item.kind === "count";
}

function load(): void {
  if (loaded || typeof window === "undefined") return;
  loaded = true;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    // Una version anterior del formato, o un `localStorage` manipulado, no
    // deben romper la pantalla: lo que no valide se descarta en silencio.
    picks = Array.isArray(parsed) ? parsed.filter(isPick).slice(0, MAX_PICKS) : [];
  } catch {
    picks = [];
  }
}

function persist(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(picks));
  } catch {
    // Cuota llena o almacenamiento bloqueado: la sesion sigue funcionando en
    // memoria, solo no sobrevive a un recargado.
  }
}

function emit(): void {
  persist();
  for (const listener of listeners) listener();
}

export function subscribe(listener: () => void): () => void {
  load();
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

export function snapshot(): Pick[] {
  load();
  return picks;
}

/** Instantanea estable para el render del servidor: nunca hay selecciones. */
const SERVER_SNAPSHOT: Pick[] = [];

export function serverSnapshot(): Pick[] {
  return SERVER_SNAPSHOT;
}

export function hasPick(id: string): boolean {
  return snapshot().some((pick) => pick.id === id);
}

/** Agrega una seleccion. Devuelve `false` si ya estaba o si se llego al tope. */
export function addPick(pick: Pick): boolean {
  load();
  if (picks.some((item) => item.id === pick.id)) return false;
  if (picks.length >= MAX_PICKS) return false;
  picks = [...picks, pick];
  emit();
  return true;
}

export function removePick(id: string): void {
  load();
  const next = picks.filter((pick) => pick.id !== id);
  if (next.length === picks.length) return;
  picks = next;
  emit();
}

export function togglePick(pick: Pick): void {
  if (hasPick(pick.id)) removePick(pick.id);
  else addPick(pick);
}

export function clearPicks(): void {
  load();
  if (!picks.length) return;
  picks = [];
  emit();
}

/** Solo para pruebas: reinicia el modulo sin tocar `localStorage`. */
export function resetForTests(next: Pick[] = []): void {
  loaded = true;
  picks = next;
  for (const listener of listeners) listener();
}
