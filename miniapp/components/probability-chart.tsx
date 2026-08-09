"use client";

import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

export default function ProbabilityChart({ values }: { values: Array<{ name: string; value: number; color?: string }> }) {
  const colors = ["var(--mint)", "var(--signal)", "var(--muted)"];
  return (
    <div className="chart-shell" aria-label="Gráfica comparativa de probabilidades">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={values} margin={{ top: 8, right: 6, left: -24, bottom: 0 }}>
          <CartesianGrid stroke="var(--line)" vertical={false} />
          <XAxis dataKey="name" tick={{ fill: "var(--muted)", fontSize: 11 }} axisLine={false} tickLine={false} />
          <YAxis domain={[0, 1]} tickFormatter={(value) => `${Math.round(value * 100)}%`} tick={{ fill: "var(--muted)", fontSize: 10 }} axisLine={false} tickLine={false} />
          <Tooltip formatter={(value) => `${Math.round(Number(value) * 100)}%`} contentStyle={{ background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 12 }} />
          <Bar dataKey="value" radius={[8, 8, 2, 2]}>{values.map((row, index) => <Cell key={row.name} fill={row.color ?? colors[index % colors.length]} />)}</Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
