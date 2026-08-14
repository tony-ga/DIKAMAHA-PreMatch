/**
 * Diseño del PNG compartible. Vive aparte de la ruta para poder renderizarlo
 * sin base de datos ni servidor -`scripts/render-share-card.ts` escribe un
 * archivo desde aquí-, que es la única forma de revisar de verdad un layout de
 * Satori: soporta un subconjunto de CSS y sus fallos son visuales, no errores.
 */
import { type ShareCard, type ShareCardPeriod, clip } from "@/lib/share-card";

// 1080x1540. La altura no es estetica: con tres periodos de tres lineas, el
// pie se solapaba con las ultimas filas a 1350 -Satori no recorta ni avisa,
// simplemente pinta encima-. El resto del layout tiene altura fija por
// construccion (titulo siempre a dos lineas, etiqueta 1X2 con alto reservado),
// asi que este valor vale para cualquier partido, no solo para los nombres
// cortos con los que se diseno.
export const SHARE_IMAGE_SIZE = { width: 1080, height: 1540 };

const PALETTE = {
  void: "#091413",
  panel: "#0e1c1a",
  panelHigh: "#132522",
  mint: "#b0e4cc",
  signal: "#408a71",
  muted: "#86a79a",
  text: "#edf9f4",
  line: "rgba(176, 228, 204, 0.16)",
};

function percent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function kickoffLabel(value: string): string {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "";
  return new Intl.DateTimeFormat("es-MX", {
    timeZone: "America/Mexico_City",
    day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
    hour12: false,
  }).format(date).replace(",", " ·");
}

/**
 * Capa de marca de agua: "DIKAMAHA" repetido en diagonal sobre todo el lienzo.
 *
 * Va detrás del contenido y con opacidad baja para que no compita con las
 * cifras, pero cubre el area completa: recortar la tarjeta para quitarla
 * costaria recortar tambien los datos. Satori no tiene `repeating-linear-
 * gradient` ni pseudo-elementos, asi que la rejilla se escribe a mano.
 */
function Watermark() {
  const rows = [0, 1, 2, 3, 4, 5, 6, 7];
  return (
    <div style={{
      position: "absolute", top: 0, left: 0, width: "100%", height: "100%",
      display: "flex", flexDirection: "column", justifyContent: "space-around",
      transform: "rotate(-24deg)", opacity: 0.05,
    }}>
      {rows.map((row) => (
        <div key={row} style={{
          display: "flex", flexDirection: "row", justifyContent: "space-around",
          width: "150%", marginLeft: "-25%",
        }}>
          {[0, 1, 2].map((column) => (
            <span key={column} style={{
              fontSize: 58, fontWeight: 700, color: PALETTE.mint,
              letterSpacing: 10,
            }}>DIKAMAHA</span>
          ))}
        </div>
      ))}
    </div>
  );
}

function OutcomeColumn(
  { label, value, accent }: { label: string; value: number; accent?: boolean },
) {
  return (
    <div style={{
      display: "flex", flexDirection: "column", alignItems: "center",
      flex: 1, padding: "22px 12px", borderRadius: 20,
      backgroundColor: accent ? PALETTE.signal : PALETTE.panelHigh,
    }}>
      <span style={{
        fontSize: 52, fontWeight: 700,
        color: accent ? PALETTE.void : PALETTE.mint,
      }}>{percent(value)}</span>
      {/* Alto fijo de dos líneas: así el bloque mide lo mismo con "Puebla" que
          con un nombre que se parte en dos, y nada de lo que va debajo se
          desplaza según el partido. */}
      <div style={{
        display: "flex", height: 62, marginTop: 8, alignItems: "flex-start",
        justifyContent: "center",
      }}>
        <span style={{
          fontSize: 24, textAlign: "center",
          color: accent ? PALETTE.void : PALETTE.muted,
        }}>{clip(label, 22)}</span>
      </div>
    </div>
  );
}

function GoalMarket({ label, value }: { label: string; value: number }) {
  return (
    <div style={{
      display: "flex", flexDirection: "row", alignItems: "center",
      justifyContent: "space-between", flex: 1, padding: "18px 26px",
      borderRadius: 18, backgroundColor: PALETTE.panelHigh,
    }}>
      <span style={{ fontSize: 26, color: PALETTE.muted }}>{label}</span>
      <span style={{ fontSize: 38, fontWeight: 700, color: PALETTE.mint }}>
        {percent(value)}
      </span>
    </div>
  );
}

function PeriodBlock({ period }: { period: ShareCardPeriod }) {
  return (
    <div style={{
      display: "flex", flexDirection: "column", marginTop: 18, flexShrink: 0,
    }}>
      <span style={{
        fontSize: 21, letterSpacing: 3, color: PALETTE.signal,
        textTransform: "uppercase",
      }}>{period.label}</span>
      <div style={{ display: "flex", flexDirection: "column", marginTop: 10 }}>
        {period.lines.map((line) => (
          <div key={line.metric} style={{
            display: "flex", flexDirection: "row", alignItems: "center",
            justifyContent: "space-between", padding: "11px 4px",
            borderBottom: `1px solid ${PALETTE.line}`,
          }}>
            <span style={{ fontSize: 26, color: PALETTE.text }}>
              {line.label}
            </span>
            <div style={{ display: "flex", flexDirection: "row", alignItems: "baseline" }}>
              <span style={{ fontSize: 30, fontWeight: 700, color: PALETTE.mint }}>
                {line.expected.toFixed(1)}
              </span>
              <span style={{ fontSize: 22, color: PALETTE.muted, marginLeft: 12 }}>
                entre {line.intervalLow} y {line.intervalHigh}
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export function ShareCardImage({ card }: { card: ShareCard }) {
  return (
    <div style={{
      position: "relative", width: "100%", height: "100%", display: "flex",
      flexDirection: "column", backgroundColor: PALETTE.void,
      padding: 56, color: PALETTE.text,
    }}>
      <Watermark />
      <div style={{
        display: "flex", flexDirection: "row", alignItems: "center",
        justifyContent: "space-between",
      }}>
        <div style={{ display: "flex", flexDirection: "row", alignItems: "center" }}>
          <div style={{
            width: 20, height: 20, borderRadius: 20,
            backgroundColor: PALETTE.mint, marginRight: 16,
          }} />
          <span style={{ fontSize: 34, fontWeight: 700, letterSpacing: 6 }}>
            DIKAMAHA
          </span>
        </div>
        <span style={{ fontSize: 22, letterSpacing: 3, color: PALETTE.signal }}>
          PRE-MATCH
        </span>
      </div>

      <div style={{ display: "flex", flexDirection: "column", marginTop: 38 }}>
        <span style={{ fontSize: 24, color: PALETTE.muted }}>
          {card.leagueSlug} · {kickoffLabel(card.kickoffTs)}
        </span>
        {/* Siempre dos líneas, una por equipo. Escribirlo en una sola dejaba
            que el salto dependiera del largo de los nombres, y con él la
            altura de todo lo que viene detrás. */}
        <span style={{
          fontSize: 50, fontWeight: 700, marginTop: 8, lineHeight: 1.2,
        }}>{clip(card.homeName, 24)}</span>
        <span style={{
          fontSize: 50, fontWeight: 700, lineHeight: 1.2, color: PALETTE.mint,
        }}>vs {clip(card.awayName, 22)}</span>
      </div>

      <div style={{ display: "flex", flexDirection: "row", gap: 14, marginTop: 34 }}>
        <OutcomeColumn label={card.homeName} value={card.probabilityHome}
          accent={card.headlineLabel === card.homeName} />
        <OutcomeColumn label="Empate" value={card.probabilityDraw}
          accent={card.headlineLabel === "Empate"} />
        <OutcomeColumn label={card.awayName} value={card.probabilityAway}
          accent={card.headlineLabel === card.awayName} />
      </div>

      <div style={{ display: "flex", flexDirection: "row", gap: 14, marginTop: 16 }}>
        <GoalMarket label="Más de 2.5 goles" value={card.probabilityOver25} />
        <GoalMarket label="Ambos marcan" value={card.probabilityBtts} />
      </div>

      <div style={{
        display: "flex", flexDirection: "column", flex: 1, marginTop: 16,
        padding: "20px 28px", borderRadius: 22, backgroundColor: PALETTE.panel,
      }}>
        {/* `flexShrink: 0` en la leyenda y en cada bloque: sin el, flex
            comprime los hijos cuando el contenido roza el alto disponible y
            Satori los superpone en vez de recortarlos -la leyenda acababa
            pintada sobre "PRIMERA MITAD"-. */}
        <span style={{
          fontSize: 21, color: PALETTE.muted, flexShrink: 0, marginBottom: 4,
        }}>
          Conteo esperado de ambos equipos · rango central del 60%
        </span>
        {card.periods.map((period) => (
          <PeriodBlock key={period.period} period={period} />
        ))}
      </div>

      <div style={{ display: "flex", flexDirection: "column", marginTop: "auto", paddingTop: 22 }}>
        <span style={{ fontSize: 22, color: PALETTE.muted }}>
          Probabilidades calculadas antes del inicio · no es asesoría ni
          recomendación de apuesta
        </span>
        <span style={{ fontSize: 26, fontWeight: 700, color: PALETTE.signal, marginTop: 6 }}>
          DIKAMAHA · PREDICCIÓN CONGELADA
        </span>
      </div>
    </div>
  );
}

