"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useAuth } from "@/components/providers";
import { api } from "@/lib/client-api";

type Favorite = { entityType: string; entityId: string };

export function FavoriteButton({ entityType, entityId, label, metadata = {} }: {
  entityType: "fixture" | "team" | "league";
  entityId: string;
  label: string;
  metadata?: Record<string, unknown>;
}) {
  const { csrfToken } = useAuth();
  const client = useQueryClient();
  const favorites = useQuery({
    queryKey: ["favorites"],
    queryFn: () => api<{ favorites: Favorite[] }>("/api/favorites"),
  });
  const saved = favorites.data?.favorites.some((item) => item.entityType === entityType && item.entityId === entityId) ?? false;
  const mutation = useMutation({
    mutationFn: () => saved
      ? api(`/api/favorites?entityType=${entityType}&entityId=${encodeURIComponent(entityId)}`, { method: "DELETE" }, csrfToken)
      : api("/api/favorites", { method: "POST", body: JSON.stringify({ entityType, entityId, label, metadata }) }, csrfToken),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["favorites"] }),
  });
  return (
    <button
      className="icon-button"
      onClick={() => mutation.mutate()}
      aria-label={saved ? "Eliminar de favoritos" : "Guardar en favoritos"}
      disabled={mutation.isPending}
    >
      {saved ? "★" : "☆"}
    </button>
  );
}
