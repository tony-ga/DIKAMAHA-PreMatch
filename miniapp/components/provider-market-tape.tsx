"use client";

import { record } from "@/lib/client-api";

const MARKET_LABELS: Record<string, string> = {
  moneyline: "Resultado 1X2",
  spread: "Hándicap",
  total: "Total de goles",
};

function quote(value: unknown): string {
  const row = record(value);
  return [row.line, row.odds].filter((item) => item !== undefined && String(item).trim()).map(String).join(" · ") || "—";
}

function sideLabel(side: string, homeName: string, awayName: string): string {
  return {
    home: homeName, away: awayName, draw: "Empate",
    over: "Más de", under: "Menos de",
  }[side] ?? side;
}

export function ProviderMarketTape({ value, homeName, awayName }: {
  value: unknown;
  homeName: string;
  awayName: string;
}) {
  const market = record(value);
  const providers = Array.isArray(market.providers) ? market.providers.map(record) : [];
  if (!providers.length) return null;

  return (
    <section className="market-tape">
      <div className="panel-heading">
        <div><p className="eyebrow">APERTURA · CIERRE · LIVE</p><h3>Movimiento del mercado</h3></div>
        <span className="mode-badge benchmark">AISLADO</span>
      </div>
      {providers.map((provider, providerIndex) => {
        const markets = record(provider.markets);
        return (
          <div className="market-provider" key={`${String(provider.provider_id)}-${providerIndex}`}>
            <div className="market-provider-title"><strong>{String(provider.provider_name || "Proveedor")}</strong><span>{String(provider.details || "Líneas publicadas")}</span></div>
            {Object.entries(markets).map(([marketName, marketValue]) => {
              const sides = record(marketValue);
              return (
                <div className="market-table-wrap" key={marketName}>
                  <table className="market-tape-table">
                    <caption>{MARKET_LABELS[marketName] ?? marketName}</caption>
                    <thead><tr><th scope="col">Selección</th><th scope="col">Apertura</th><th scope="col">Cierre</th><th scope="col">Live</th></tr></thead>
                    <tbody>{Object.entries(sides).map(([side, cuts]) => {
                      const values = record(cuts);
                      return <tr key={side}><th scope="row">{sideLabel(side, homeName, awayName)}</th><td>{quote(values.open)}</td><td>{quote(values.close)}</td><td className={values.live ? "live-quote" : ""}>{quote(values.live)}</td></tr>;
                    })}</tbody>
                  </table>
                </div>
              );
            })}
          </div>
        );
      })}
      <p className="financial-isolation">Información descriptiva del proveedor. No es SPI, probabilidad implícita, recomendación ni feature de los modelos DIKAMAHA.</p>
    </section>
  );
}
