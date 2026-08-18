/**
 * Diseño del PNG compartible. Vive aparte de la ruta para poder renderizarlo
 * sin base de datos ni servidor -`scripts/render-share-card.ts` escribe un
 * archivo desde aquí-, que es la única forma de revisar de verdad un layout de
 * Satori: soporta un subconjunto de CSS y sus fallos son visuales, no errores.
 */
import {
  CARD_PERIODS, type ShareCard, type ShareCardCell, type ShareCardTeam,
  clip, initials,
} from "@/lib/share-card";

// 1080x1180. La v1 media 1540 de alto y aun asi no cabia en pantalla: apilaba
// nueve filas por periodo. Con una matriz por equipo el contenido baja a dos
// tablas de 3x3 y la altura tambien. Sigue siendo fija por construccion -el
// numero de filas y columnas no depende del partido, y los nombres se acotan
// con `clip`-, asi que ningun encuentro puede desbordarla.
export const SHARE_IMAGE_SIZE = { width: 1080, height: 1180 };

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

/** Ancho de la primera columna de cada tabla, la de la métrica. */
const METRIC_COLUMN = 250;

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
  return (
    <div style={{
      position: "absolute", top: 0, left: 0, width: "100%", height: "100%",
      display: "flex", flexDirection: "column", justifyContent: "space-around",
      transform: "rotate(-24deg)", opacity: 0.05,
    }}>
      {[0, 1, 2, 3, 4, 5, 6].map((row) => (
        <div key={row} style={{
          display: "flex", flexDirection: "row", justifyContent: "space-around",
          width: "150%", marginLeft: "-25%",
        }}>
          {[0, 1, 2].map((column) => (
            <span key={column} style={{
              fontSize: 56, fontWeight: 700, color: PALETTE.mint,
              letterSpacing: 10,
            }}>DIKAMAHA</span>
          ))}
        </div>
      ))}
    </div>
  );
}

/** Escudo del equipo, o sus iniciales cuando no hay PNG que pintar. */
function Crest({ team, size }: { team: ShareCardTeam; size: number }) {
  if (team.logo) {
    return <img src={team.logo} width={size} height={size} alt="" />;
  }
  return (
    <div style={{
      display: "flex", width: size, height: size, borderRadius: size,
      alignItems: "center", justifyContent: "center",
      backgroundColor: PALETTE.panelHigh, color: PALETTE.mint,
      fontSize: size * 0.4, fontWeight: 700,
    }}>{initials(team.name)}</div>
  );
}

/** Bloque destacado: quién gana, o si ambos marcan. */
function Verdict(
  { caption, value, probability, accent }: {
    caption: string; value: string; probability: number; accent?: boolean;
  },
) {
  return (
    <div style={{
      display: "flex", flexDirection: "column", flex: 1,
      padding: "18px 26px", borderRadius: 18,
      backgroundColor: accent ? PALETTE.signal : PALETTE.panelHigh,
    }}>
      <span style={{
        fontSize: 20, letterSpacing: 3,
        color: accent ? PALETTE.void : PALETTE.muted,
      }}>{caption}</span>
      <div style={{
        display: "flex", flexDirection: "row", alignItems: "baseline",
        justifyContent: "space-between", marginTop: 6,
      }}>
        <span style={{
          fontSize: 30, fontWeight: 700,
          color: accent ? PALETTE.void : PALETTE.text,
        }}>{clip(value, 22)}</span>
        <span style={{
          fontSize: 32, fontWeight: 700,
          color: accent ? PALETTE.void : PALETTE.mint,
        }}>{percent(probability)}</span>
      </div>
    </div>
  );
}

/** Una celda de la matriz: dirección, línea y probabilidad, o un hueco. */
function Cell({ cell }: { cell: ShareCardCell | null }) {
  if (!cell) {
    return (
      <div style={{
        display: "flex", flex: 1, justifyContent: "center",
      }}>
        <span style={{ fontSize: 24, color: PALETTE.signal }}>—</span>
      </div>
    );
  }
  return (
    <div style={{
      display: "flex", flex: 1, flexDirection: "row",
      alignItems: "baseline", justifyContent: "center",
    }}>
      <span style={{ fontSize: 23, color: PALETTE.text }}>
        {cell.direction === "under" ? "−" : "+"}{cell.line}
      </span>
      <span style={{
        fontSize: 25, fontWeight: 700, color: PALETTE.mint, marginLeft: 10,
      }}>{percent(cell.probability)}</span>
    </div>
  );
}

/** Tabla de un equipo: métricas en filas, periodos en columnas. */
function TeamTable({ team }: { team: ShareCardTeam }) {
  return (
    <div style={{
      display: "flex", flexDirection: "column", flexShrink: 0,
      padding: "18px 24px", borderRadius: 20, backgroundColor: PALETTE.panel,
    }}>
      <div style={{
        display: "flex", flexDirection: "row", alignItems: "center",
      }}>
        <Crest team={team} size={40} />
        <span style={{
          fontSize: 30, fontWeight: 700, marginLeft: 14, color: PALETTE.text,
        }}>{clip(team.name, 26)}</span>
      </div>

      <div style={{
        display: "flex", flexDirection: "row", marginTop: 14,
        paddingBottom: 8, borderBottom: `1px solid ${PALETTE.line}`,
      }}>
        <span style={{ width: METRIC_COLUMN }} />
        {CARD_PERIODS.map(([period, label]) => (
          <div key={period} style={{
            display: "flex", flex: 1, justifyContent: "center",
          }}>
            <span style={{
              fontSize: 19, letterSpacing: 2, color: PALETTE.signal,
            }}>{label}</span>
          </div>
        ))}
      </div>

      {team.rows.map((row) => (
        <div key={row.metric} style={{
          display: "flex", flexDirection: "row", alignItems: "center",
          padding: "13px 0", borderBottom: `1px solid ${PALETTE.line}`,
        }}>
          <span style={{
            width: METRIC_COLUMN, fontSize: 24, color: PALETTE.muted,
          }}>{row.label}</span>
          {row.cells.map((cell, index) => (
            <Cell key={CARD_PERIODS[index][0]} cell={cell} />
          ))}
        </div>
      ))}
    </div>
  );
}

export function ShareCardImage({ card }: { card: ShareCard }) {
  return (
    <div style={{
      position: "relative", width: "100%", height: "100%", display: "flex",
      flexDirection: "column", backgroundColor: PALETTE.void,
      padding: 48, color: PALETTE.text,
    }}>
      <Watermark />

      <div style={{
        display: "flex", flexDirection: "row", alignItems: "center",
        justifyContent: "space-between",
      }}>
        <div style={{ display: "flex", flexDirection: "row", alignItems: "center" }}>
          <div style={{
            width: 18, height: 18, borderRadius: 18,
            backgroundColor: PALETTE.mint, marginRight: 14,
          }} />
          <span style={{ fontSize: 30, fontWeight: 700, letterSpacing: 6 }}>
            DIKAMAHA
          </span>
        </div>
        <span style={{ fontSize: 20, letterSpacing: 3, color: PALETTE.signal }}>
          {card.leagueSlug} · {kickoffLabel(card.kickoffTs)}
        </span>
      </div>

      {/* Los dos escudos con su nombre, alto fijo: el bloque mide lo mismo
          con "Puebla" que con un nombre que se parte en dos líneas. El "vs" es
          un hijo más del flex, no un absoluto: posicionarlo a mano lo ataba al
          padding del lienzo y cualquier cambio de altura lo descolocaba. */}
      <div style={{
        display: "flex", flexDirection: "row", alignItems: "center",
        height: 120, marginTop: 14,
      }}>
        <div style={{
          display: "flex", flexDirection: "row", alignItems: "center", flex: 1,
        }}>
          <Crest team={card.home} size={72} />
          <span style={{
            fontSize: 32, fontWeight: 700, marginLeft: 16,
          }}>{clip(card.home.name, 30)}</span>
        </div>
        <span style={{
          fontSize: 22, color: PALETTE.signal, paddingLeft: 12, paddingRight: 12,
        }}>vs</span>
        <div style={{
          display: "flex", flexDirection: "row", alignItems: "center", flex: 1,
          justifyContent: "flex-end",
        }}>
          <span style={{
            fontSize: 32, fontWeight: 700, marginRight: 16, textAlign: "right",
          }}>{clip(card.away.name, 30)}</span>
          <Crest team={card.away} size={72} />
        </div>
      </div>

      <div style={{ display: "flex", flexDirection: "row", gap: 14, marginTop: 6 }}>
        {/* "GANA" prometia de mas: el resultado mas probable de tres puede
            rondar el 37%, y anunciarlo como una victoria seria afirmar algo que
            el modelo no dice. "Escenario principal" es el mismo vocabulario que
            ya usa la tarjeta del canal de Telegram. */}
        <Verdict caption="ESCENARIO PRINCIPAL" value={card.outcomeLabel}
          probability={card.outcomeProbability} accent />
        <Verdict caption="AMBOS MARCAN" value={card.bttsLabel}
          probability={card.bttsProbability} />
      </div>

      <span style={{
        fontSize: 19, color: PALETTE.muted, marginTop: 18, flexShrink: 0,
      }}>
        Córners, tiros y tarjetas por equipo · + más de / − menos de
      </span>

      <div style={{
        display: "flex", flexDirection: "column", gap: 12, marginTop: 10,
      }}>
        <TeamTable team={card.home} />
        <TeamTable team={card.away} />
      </div>

      <div style={{
        display: "flex", flexDirection: "column", marginTop: "auto",
        paddingTop: 18,
      }}>
        <span style={{ fontSize: 20, color: PALETTE.muted }}>
          Probabilidades calculadas antes del inicio · no es asesoría ni
          recomendación de apuesta
        </span>
        <span style={{
          fontSize: 24, fontWeight: 700, color: PALETTE.signal, marginTop: 4,
        }}>
          DIKAMAHA · PREDICCIÓN CONGELADA
        </span>
      </div>
    </div>
  );
}
