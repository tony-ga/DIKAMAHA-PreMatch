"use client";

import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

export default function ProbabilityChart({ values }: { values: Array<{ name: string; value: number }> }) {
  return (
    <div className="chart-shell" aria-label="Gráfica comparativa de probabilidades">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={values} margin={{ top: 8, right: 6, left: -24, bottom: 0 }}>
          <CartesianGrid stroke="rgba(176,228,204,.08)" vertical={false} />
          <XAxis dataKey="name" tick={{ fill: "#86a79a", fontSize: 11 }} axisLine={false} tickLine={false} />
          <YAxis domain={[0, 1]} tickFormatter={(value) => `${Math.round(value * 100)}%`} tick={{ fill: "#86a79a", fontSize: 10 }} axisLine={false} tickLine={false} />
          <Tooltip formatter={(value) => `${Math.round(Number(value) * 100)}%`} contentStyle={{ background: "#0e1c1a", border: "1px solid rgba(176,228,204,.2)", borderRadius: 12 }} />
          <Bar dataKey="value" fill="#b0e4cc" radius={[8, 8, 2, 2]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
