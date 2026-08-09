"use client";

import Image from "next/image";
import { useState } from "react";

function initials(label: string) {
  return label.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join("").toUpperCase() || "?";
}

export function EntityImage({ source, label, kind = "team", size = 44 }: {
  source?: string | null;
  label: string;
  kind?: "team" | "player";
  size?: number;
}) {
  const [failed, setFailed] = useState(false);
  const className = kind === "player" ? "entity-image player-image" : "entity-image team-image";
  if (!source || failed) return <span className={`${className} fallback`} style={{ width: size, height: size }}>{initials(label)}</span>;
  return (
    <span className={className} style={{ width: size, height: size }}>
      <Image
        src={`/api/media?url=${encodeURIComponent(source)}`}
        alt={label}
        width={size}
        height={size}
        unoptimized
        onError={() => setFailed(true)}
      />
    </span>
  );
}
