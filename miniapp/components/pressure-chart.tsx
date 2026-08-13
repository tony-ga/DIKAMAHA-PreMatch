"use client";

import { Area, CartesianGrid, ComposedChart, Line, ReferenceDot, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { record } from "@/lib/client-api";

type Point = {
  minute: number;
  raw_score: number;
  smoothed_score: number;
  home_pressure: number;
  away_pressure: number;
};

function pointsFrom(value: unknown): Point[] {
  return Array.isArray(value) ? value.map(record).flatMap((row) => {
    const point = {
      minute: Number(row.minute),
      raw_score: Number(row.raw_score),
      smoothed_score: Number(row.smoothed_score),
      home_pressure: Number(row.home_pressure),
      away_pressure: Number(row.away_pressure),
    };
    return Object.values(point).every(Number.isFinite) ? [point] : [];
  }) : [];
}

/**
 * Motivo por el que la curva está vacía, en las palabras del backend.
 *
 * `aggregate_only` no es un estado de carga: el proveedor publicó los
 * conteos agregados del partido pero ninguna jugada con marca de tiempo,
 * así que la curva no se puede construir nunca para esa competición (DEC-176).
 * Distinguirlo de "todavía no ha pasado nada" evita que el usuario espere
 * indefinidamente un gráfico que no va a llegar.
 */
const EMPTY_REASONS: Record<string, string> = {
  aggregate_only:
    "Esta competición no publica la cronología por jugada, sólo los totales del partido. Los conteos oficiales sí están en «Acciones observadas», arriba.",
  insufficient_events:
    "Todavía no hay acciones suficientes para construir la curva.",
};

export default function PressureChart({ value, homeName, awayName }: { value: unknown; homeName: string; awayName: string }) {
  const dynamics = record(value);
  const granularity = String(dynamics.pressure_granularity ?? "insufficient_events");
  const currentMinute = Math.max(1, Number(dynamics.current_minute) || 1);
  const points = pointsFrom(dynamics.points).filter((point) => point.minute <= currentMinute);
  const goals = Array.isArray(dynamics.goal_markers) ? dynamics.goal_markers.map(record) : [];
  let maximum = 1;
  for (const point of points) maximum = Math.max(maximum, Math.abs(point.smoothed_score));
  const domain = Math.ceil(maximum * 1.25 * 10) / 10;
  const byMinute = new Map(points.map((point) => [point.minute, point.smoothed_score]));

  // `aggregate_only` manda sobre el conteo de puntos: aunque un gol suelto
  // produzca una curva técnicamente dibujable, sin tiros, córners ni faltas
  // con minuto esa línea no representa la presión del partido.
  if (!points.length || granularity === "aggregate_only") {
    return <article className="data-panel pressure-panel"><p className="eyebrow">DINÁMICA DE PARTIDO</p><h3>Presión por acciones</h3><p className="muted">{EMPTY_REASONS[granularity] ?? EMPTY_REASONS.insufficient_events}</p></article>;
  }

  return (
    <article className="data-panel pressure-panel">
      <div className="panel-heading"><div><p className="eyebrow">DINÁMICA DE PARTIDO</p><h3>Presión por acciones</h3></div><span className="provider-chip">MEDIA MÓVIL · 5 MIN</span></div>
      <div className="pressure-legend"><span><i className="home" />{homeName}</span><span><i className="away" />{awayName}</span><small>0 = equilibrio</small></div>
      <div className="pressure-chart" aria-label={`Curva de presión de ${homeName} y ${awayName}`}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={points} margin={{ top: 25, right: 8, left: -18, bottom: 0 }}>
            <CartesianGrid stroke="var(--line)" vertical={false} />
            <XAxis dataKey="minute" domain={[1, Math.max(15, currentMinute)]} type="number" tickFormatter={(minute) => `${minute}'`} tick={{ fill: "var(--muted)", fontSize: 10 }} axisLine={false} tickLine={false} />
            <YAxis domain={[-domain, domain]} tick={{ fill: "var(--muted)", fontSize: 10 }} axisLine={false} tickLine={false} />
            <ReferenceLine y={0} stroke="var(--text)" strokeOpacity={0.7} />
            <Tooltip labelFormatter={(minute) => `Minuto ${minute}`} formatter={(value) => [Number(value).toFixed(1), "Presión"]} contentStyle={{ background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 12 }} />
            <Area type="monotone" dataKey="home_pressure" name={homeName} stroke="none" fill="var(--mint)" fillOpacity={0.38} baseValue={0} />
            <Area type="monotone" dataKey="away_pressure" name={awayName} stroke="none" fill="var(--signal)" fillOpacity={0.42} baseValue={0} />
            <Line type="monotone" dataKey="smoothed_score" stroke="var(--text)" strokeWidth={2.2} dot={false} />
            {goals.map((goal, index) => {
              const minute = Number(goal.minute);
              const score = byMinute.get(minute) ?? 0;
              return <ReferenceDot key={`${minute}-${index}`} x={minute} y={score} r={4} fill={goal.team_side === "home" ? "var(--mint)" : "var(--signal)"} stroke="var(--void)" label={{ value: "⚽", position: score >= 0 ? "top" : "bottom", fontSize: 13 }} />;
            })}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <p className="live-source-note">Índice heurístico de presentación: gol 25, tiro a puerta 8, tiro fuera 4, córner 3 y falta 1. No alimenta ninguna predicción.</p>
    </article>
  );
}
