"use client";

import {
  Area,
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  ErrorBar,
  Legend,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";

import type {
  DailyRatePoint,
  LeagueRatePoint,
  MarketRatePoint,
  ReliabilityPoint,
  ShadowRatePoint,
} from "@/lib/track-record-charts";

const PERCENT_AXIS = { domain: [0, 1] as [number, number], tickFormatter: (value: number) => `${Math.round(value * 100)}%` };
const AXIS_TICK = { fill: "var(--muted)", fontSize: 10 };
const TOOLTIP_STYLE = { background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 12 };

function percentLabel(value: unknown): string {
  const number = Number(value);
  return Number.isFinite(number) ? `${Math.round(number * 100)}%` : "—";
}

/**
 * Aciertos vs. referencia, por mercado oficial.
 *
 * Dos barras por mercado -no una- porque un porcentaje sólo se lee frente a
 * algo: 58% de acierto en 1X2 no dice nada por sí solo si no se ve al lado
 * que el reparto base de esa liga ya da 50%. La referencia es la que ya
 * calcula `settlement_store.py` (`baseline_rate`), no una cuota de casa de
 * apuestas ni nada relacionado con apuestas.
 */
export function MarketRateChart({ points }: { points: MarketRatePoint[] }) {
  if (!points.length) return null;
  return (
    <div className="chart-shell" aria-label="Aciertos por mercado oficial, comparados con la referencia">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={points} margin={{ top: 8, right: 6, left: -24, bottom: 0 }} barGap={4}>
          <CartesianGrid stroke="var(--line)" vertical={false} />
          <XAxis dataKey="label" tick={AXIS_TICK} axisLine={false} tickLine={false} />
          <YAxis {...PERCENT_AXIS} tick={AXIS_TICK} axisLine={false} tickLine={false} />
          <Tooltip
            formatter={(value: unknown, name) => [percentLabel(value), name]}
            contentStyle={TOOLTIP_STYLE}
          />
          <Legend
            wrapperStyle={{ fontSize: 11, color: "var(--muted)" }}
            formatter={(value) => <span style={{ color: "var(--text)" }}>{value}</span>}
          />
          <Bar dataKey="rate" name="Acertado" fill="var(--mint)" radius={[6, 6, 2, 2]} />
          <Bar dataKey="baseline" name="Referencia" fill="var(--line)" radius={[6, 6, 2, 2]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/**
 * Tendencia diaria del acierto agregado, sobre el mismo periodo de la Fase 121.
 *
 * Un área simple, una sola serie: la ventana ya puede tener hasta 200
 * partidos, y trazar una línea por mercado encima haría ilegible el gráfico
 * en una pantalla de teléfono sin añadir una lectura que la barra de arriba
 * no dé ya desglosada.
 */
export function DailyTrendChart({ points }: { points: DailyRatePoint[] }) {
  if (points.length < 2) return null;
  return (
    <div className="chart-shell" aria-label="Tendencia diaria del acierto verificado">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={points} margin={{ top: 8, right: 6, left: -24, bottom: 0 }}>
          <CartesianGrid stroke="var(--line)" vertical={false} />
          <XAxis dataKey="label" tick={AXIS_TICK} axisLine={false} tickLine={false} minTickGap={24} />
          <YAxis {...PERCENT_AXIS} tick={AXIS_TICK} axisLine={false} tickLine={false} />
          <Tooltip
            formatter={(value: unknown, _name, item) => {
              const point = item?.payload as DailyRatePoint | undefined;
              return [`${percentLabel(value)} (${point?.hits ?? 0}/${point?.total ?? 0})`, "Acierto del día"];
            }}
            contentStyle={TOOLTIP_STYLE}
          />
          <Area type="monotone" dataKey="rate" stroke="var(--mint)" strokeWidth={2} fill="var(--mint)" fillOpacity={0.22} dot={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

/**
 * Acierto por liga, ordenado por volumen de partidos verificados.
 *
 * Barras horizontales: los slugs de liga (`esp.1`, `mex.1`...) son más largos
 * que los nombres de mercado, y en vertical se solapan en una pantalla
 * angosta. El orden es por `total`, no por `rate` -ver el porqué en
 * `leagueHitRateSeries`-.
 */
export function LeagueRateChart({ points }: { points: LeagueRatePoint[] }) {
  if (!points.length) return null;
  const height = Math.max(160, points.length * 34);
  return (
    <div className="chart-shell" style={{ height }} aria-label="Acierto por liga">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          data={points} layout="vertical"
          margin={{ top: 4, right: 24, left: 8, bottom: 0 }}
        >
          <CartesianGrid stroke="var(--line)" horizontal={false} />
          <XAxis type="number" {...PERCENT_AXIS} tick={AXIS_TICK} axisLine={false} tickLine={false} />
          <YAxis type="category" dataKey="league" width={64} tick={AXIS_TICK} axisLine={false} tickLine={false} />
          <Tooltip
            formatter={(value: unknown, _name, item) => {
              const point = item?.payload as LeagueRatePoint | undefined;
              return [`${percentLabel(value)} (${point?.hits ?? 0}/${point?.total ?? 0})`, "Acierto"];
            }}
            contentStyle={TOOLTIP_STYLE}
          />
          <Bar dataKey="rate" fill="var(--mint)" radius={[2, 6, 6, 2]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/**
 * Diagrama de fiabilidad: confianza declarada vs. tasa observada.
 *
 * `verificar_afirmacion` contra el corpus de matemáticas confirma (book2.pdf
 * p.613, Murphy p.450) que ésta -probabilidad declarada en el eje X, frecuencia
 * observada en el eje Y, con una diagonal de referencia- es la forma estándar
 * de visualizar calibración: un punto sobre la diagonal es un tramo donde el
 * modelo dijo la verdad; por debajo, sobreconfianza; por arriba, infraconfianza.
 * La barra de error es el IC95% de Wilson que ya calcula `prospective_reliability`
 * -no una aproximación normal-, así que un punto lejos de la diagonal pero con
 * una barra que la toca no es una desviación confirmada.
 */
export function ReliabilityChart({ points }: { points: ReliabilityPoint[] }) {
  if (!points.length) return null;
  return (
    <div className="chart-shell" aria-label="Confianza declarada frente a tasa observada, por tramo">
      <ResponsiveContainer width="100%" height="100%">
        <ScatterChart margin={{ top: 8, right: 16, left: -16, bottom: 0 }}>
          <CartesianGrid stroke="var(--line)" />
          <XAxis
            type="number" dataKey="declared" name="Declarada" {...PERCENT_AXIS}
            tick={AXIS_TICK} axisLine={false} tickLine={false}
          />
          <YAxis
            type="number" dataKey="observed" name="Observada" {...PERCENT_AXIS}
            tick={AXIS_TICK} axisLine={false} tickLine={false}
          />
          <ZAxis type="number" dataKey="total" range={[40, 220]} name="Muestra" />
          <ReferenceLine
            segment={[{ x: 0, y: 0 }, { x: 1, y: 1 }]}
            stroke="var(--muted)" strokeDasharray="4 4"
          />
          <Tooltip
            cursor={{ strokeDasharray: "3 3" }}
            contentStyle={TOOLTIP_STYLE}
            formatter={(value: unknown, name, item) => {
              if (name === "Muestra") {
                const point = item?.payload as ReliabilityPoint | undefined;
                return [String(point?.total ?? value), "Muestra"];
              }
              return [percentLabel(value), name];
            }}
            labelFormatter={(_label, payload) => {
              const point = payload?.[0]?.payload as ReliabilityPoint | undefined;
              return point?.label ?? "";
            }}
          />
          <Scatter data={points} fill="var(--mint)">
            <ErrorBar
              dataKey={(point: ReliabilityPoint) => [point.errorLow, point.errorHigh]}
              direction="y" width={3} stroke="var(--muted)"
            />
          </Scatter>
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  );
}

/**
 * Volumen y tasa de las líneas shadow, sin promoción.
 *
 * Mismo tipo de gráfica que las oficiales pero en `--muted`, no `--mint`: el
 * color es la única señal de "esto no está confirmado" que sobrevive a un
 * vistazo rápido, antes incluso de leer la etiqueta "experimental" del panel.
 */
export function ShadowRateChart({ points }: { points: ShadowRatePoint[] }) {
  if (!points.length) return null;
  const height = Math.max(160, points.length * 30);
  return (
    <div className="chart-shell" style={{ height }} aria-label="Volumen y tasa de líneas experimentales">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          data={points} layout="vertical"
          margin={{ top: 4, right: 24, left: 8, bottom: 0 }}
        >
          <CartesianGrid stroke="var(--line)" horizontal={false} />
          <XAxis type="number" {...PERCENT_AXIS} tick={AXIS_TICK} axisLine={false} tickLine={false} />
          <YAxis type="category" dataKey="label" width={96} tick={AXIS_TICK} axisLine={false} tickLine={false} />
          <Tooltip
            formatter={(value: unknown, _name, item) => {
              const point = item?.payload as ShadowRatePoint | undefined;
              return [`${percentLabel(value)} (${point?.hits ?? 0}/${point?.total ?? 0})`, "Experimental"];
            }}
            contentStyle={TOOLTIP_STYLE}
          />
          <Bar dataKey="rate" fill="var(--muted)" radius={[2, 6, 6, 2]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
