import Link from "next/link";
import type { ReactNode } from "react";

import type { Fixture } from "@/lib/client-api";

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
        <div>
          <strong>{fixture.home_team_name ?? `Local ${fixture.home_team_id}`}</strong>
          <strong>{fixture.away_team_name ?? `Visitante ${fixture.away_team_id}`}</strong>
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
