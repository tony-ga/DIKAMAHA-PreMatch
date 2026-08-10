"use client";

import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

type Point = { minute: number; home: number; draw: number; away: number };

export default function ProviderProbabilityHistoryChart({ points, homeName, awayName }: {
  points: Point[];
  homeName: string;
  awayName: string;
}) {
  return (
    <div className="provider-history-chart" aria-label="Curva de expectativa de resultado del proveedor externo">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={points} margin={{ top: 12, right: 8, left: -22, bottom: 0 }}>
          <CartesianGrid stroke="var(--line)" vertical={false} />
          <XAxis dataKey="minute" tickFormatter={(value) => `${value}'`} tick={{ fill: "var(--muted)", fontSize: 10 }} axisLine={false} tickLine={false} />
          <YAxis domain={[0, 1]} tickFormatter={(value) => `${Math.round(Number(value) * 100)}%`} tick={{ fill: "var(--muted)", fontSize: 10 }} axisLine={false} tickLine={false} />
          <ReferenceLine y={0.5} stroke="var(--line)" strokeDasharray="4 4" />
          <Tooltip
            labelFormatter={(value) => `Minuto ${value}`}
            formatter={(value, name) => [`${Math.round(Number(value) * 100)}%`, name]}
            contentStyle={{ background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 12 }}
          />
          <Line name={homeName} type="monotone" dataKey="home" stroke="var(--mint)" strokeWidth={2.5} dot={false} />
          <Line name="Empate" type="monotone" dataKey="draw" stroke="var(--muted)" strokeWidth={2} dot={false} />
          <Line name={awayName} type="monotone" dataKey="away" stroke="var(--signal)" strokeWidth={2.5} dot={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
