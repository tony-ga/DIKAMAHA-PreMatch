import Link from "next/link";
import type { ReactNode } from "react";

import type { Fixture } from "@/lib/client-api";
import { EntityImage } from "@/components/entity-image";

export function PageHeader({ eyebrow, title, action }: {
  eyebrow: string;
  title: string;
  action?: ReactNode;
}) {
  return (
    <div className="page-header">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h1>{title}</h1>
      </div>
      {action}
    </div>
  );
}

export function Metric({ label, value, accent = false }: {
  label: string;
  value: string | number;
  accent?: boolean;
}) {
  return (
    <article className={accent ? "metric accent" : "metric"}>
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

export function FixtureCard({ fixture, live = false }: { fixture: Fixture; live?: boolean }) {
  const href = live
    ? `/live/${fixture.match_id}?league=${encodeURIComponent(fixture.league_slug)}`
    : `/predictions/${fixture.match_id}?league=${encodeURIComponent(fixture.league_slug)}&home=${fixture.home_team_id}&away=${fixture.away_team_id}&kickoff=${encodeURIComponent(fixture.kickoff_ts)}`;
  const kickoff = new Date(fixture.kickoff_ts);
  return (
    <Link href={href} className="fixture-card">
      <div className="fixture-meta">
        <span>{fixture.league_slug}</span>
        {live ? <span className="live-pill"><i /> {fixture.display_clock ?? "LIVE"}</span> : <time>{kickoff.toLocaleString("es-MX", { dateStyle: "short", timeStyle: "short" })}</time>}
      </div>
      <div className="score-row">
        <div className="fixture-teams">
          <div><EntityImage source={fixture.home_team_logo} label={fixture.home_team_name ?? "Local"} size={34} /><strong>{fixture.home_team_name ?? `Local ${fixture.home_team_id}`}</strong></div>
          <div><EntityImage source={fixture.away_team_logo} label={fixture.away_team_name ?? "Visitante"} size={34} /><strong>{fixture.away_team_name ?? `Visitante ${fixture.away_team_id}`}</strong></div>
        </div>
        {live ? (
          <div className="score">
            <b>{fixture.home_score ?? 0}</b>
            <b>{fixture.away_score ?? 0}</b>
          </div>
        ) : <span className="arrow">→</span>}
      </div>
    </Link>
  );
}

export function StatePanel({ title, children, action }: {
  title: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <section className="state-panel">
      <div className="state-icon">◇</div>
      <h2>{title}</h2>
      <div className="muted">{children}</div>
      {action}
    </section>
  );
}

export function ShadowBadge({ official = false }: { official?: boolean }) {
  return <span className={official ? "mode-badge official" : "mode-badge"}>{official ? "OFICIAL" : "SHADOW"}</span>;
}

export function SectionTitle({ children, aside }: { children: ReactNode; aside?: ReactNode }) {
  return <div className="section-title"><h2>{children}</h2>{aside}</div>;
}

export function FeatureCard({ href, eyebrow, title, description, meta }: {
  href: string;
  eyebrow: string;
  title: string;
  description: string;
  meta?: string;
}) {
  return (
    <Link href={href} className="feature-card">
      <p className="eyebrow">{eyebrow}</p>
      <h3>{title}</h3>
      <p>{description}</p>
      <span>{meta ?? "Abrir módulo"} →</span>
    </Link>
  );
}

export function SegmentedControl({ label, options, value, onChange }: {
  label: string;
  options: Array<{ value: string; label: string }>;
  value: string;
  onChange(value: string): void;
}) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map((option) => (
        <button
          type="button"
          key={option.value}
          className={value === option.value ? "active" : ""}
          aria-pressed={value === option.value}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
