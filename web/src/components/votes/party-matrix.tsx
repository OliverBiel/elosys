import Link from "next/link";
import { matrixCell, type AgreementMatrix, type Ratio } from "@/lib/votes-queries";
import { formatPct } from "@/lib/format";
import { MIN_N, fmtInt } from "./party-ui";

export type GovernmentColumn = { label: string; byParty: Map<string, Ratio | null> };

// Sequential, one hue: fill opacity grows with the %; text stays in text tokens.
function fill(pct: number): string {
  return `rgba(var(--link-primary-rgb), ${(0.05 + 0.4 * (pct / 100)).toFixed(3)})`;
}

const CELL = "h-[46px] min-w-[52px] px-1 text-center align-middle";

/** Share of N above which a cell is marked: both parties mostly read the same bench row. */
export const SHARED_BENCH_MARK = 0.5;

function Cell({
  r, title, href, edge = false, shared = null,
}: { r: Ratio | null; title: string; href?: string; edge?: boolean; shared?: number | null }) {
  const sep: React.CSSProperties = edge ? { borderLeft: "1px solid var(--border-2)" } : {};
  if (!r || r.n === 0 || r.pct == null) {
    return (
      <td className={CELL} style={{ color: "var(--muted-2)", ...sep }} title={`${title}: nenhuma votação comparável`}>
        <span className="mono text-[11px]">—</span>
      </td>
    );
  }
  const low = r.n < MIN_N;
  const marked = shared != null && shared > 0 && shared >= SHARED_BENCH_MARK * r.n;
  const body = low ? (
    <>
      <div className="text-[9.5px] leading-tight" style={{ color: "var(--muted-2)" }}>poucos dados</div>
      <div className="mono text-[9.5px]" style={{ color: "var(--muted-2)" }}>N={r.n}</div>
    </>
  ) : (
    <>
      <div className="mono text-[12px]" style={{ color: "var(--fg-1)" }}>
        {Math.round(r.pct)}%{marked ? <sup style={{ color: "var(--muted)" }}>‡</sup> : null}
      </div>
      <div className="mono text-[9.5px]" style={{ color: "var(--muted-2)" }}>N={fmtInt(r.n)}</div>
    </>
  );
  const benchNote =
    shared != null && shared > 0
      ? ` · ${fmtInt(shared)} com a mesma bancada (federação/bloco): mesma orientação por definição`
      : "";
  const full = `${title}: ${formatPct(r.pct)} — iguais em ${fmtInt(r.agree)} de ${fmtInt(r.n)} votações${benchNote}`;
  return (
    <td
      className={CELL}
      style={{
        background: low ? "transparent" : fill(r.pct),
        border: "2px solid var(--bg)",
        outline: marked ? "1px dashed var(--muted-2)" : undefined,
        outlineOffset: -4,
        ...sep,
      }}
      title={low ? `${full} (poucos dados)` : full}
    >
      {href ? (
        <Link href={href} className="block hover:underline">
          {body}
        </Link>
      ) : (
        body
      )}
    </td>
  );
}

function PairCell({ matrix, a, b, href }: { matrix: AgreementMatrix; a: string; b: string; href: string }) {
  const c = matrixCell(matrix, a, b);
  const shared = matrix.basis === "orientation" && c ? c.sharedBench : null;
  return <Cell r={c} shared={shared} title={`${a} × ${b}`} href={href} />;
}

const vertical: React.CSSProperties = { writingMode: "vertical-rl", transform: "rotate(180deg)" };

export function PartyMatrix({
  matrix, governments, rowHref, pairHref,
}: {
  matrix: AgreementMatrix;
  governments: GovernmentColumn[];
  rowHref: (party: string) => string;
  pairHref: (a: string, b: string) => string;
}) {
  const parties = matrix.parties.map((p) => p.party);
  return (
    <div className="table-wrap">
      <div className="overflow-x-auto">
        <table className="border-collapse text-[12px]" style={{ minWidth: "100%" }}>
          <thead>
            <tr>
              <th
                className="sticky left-0 z-10 px-3 text-left align-bottom text-[11px] font-medium"
                style={{ background: "var(--bg)", color: "var(--muted-2)" }}
              >
                partido
              </th>
              {parties.map((p) => (
                <th key={p} className="px-1 pb-2 align-bottom text-[11px] font-medium" style={{ color: "var(--fg-2)" }}>
                  <span style={vertical}>{p}</span>
                </th>
              ))}
              {governments.map((g) => (
                <th
                  key={g.label}
                  className="px-2 pb-2 align-bottom text-[11px] font-medium"
                  style={{ color: "var(--fg-1)", borderLeft: "1px solid var(--border-2)" }}
                >
                  <span style={vertical}>% com {g.label}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {parties.map((a) => (
              <tr key={a}>
                <th
                  scope="row"
                  className="sticky left-0 z-10 px-3 text-left text-[12px] font-medium whitespace-nowrap"
                  style={{ background: "var(--bg)" }}
                >
                  <Link href={rowHref(a)} className="link-primary">{a}</Link>
                </th>
                {parties.map((b) =>
                  a === b ? (
                    <td
                      key={b}
                      className={CELL}
                      style={{ border: "2px solid var(--bg)", background: "var(--hover)", color: "var(--muted-2)" }}
                      title={`${a} com ele mesmo: sempre 100%`}
                    >
                      <span className="mono text-[11px]">100%</span>
                    </td>
                  ) : (
                    <PairCell
                      key={b}
                      matrix={matrix}
                      a={a}
                      b={b}
                      href={pairHref(a, b)}
                    />
                  )
                )}
                {governments.map((g) => (
                  <Cell key={g.label} r={g.byParty.get(a) ?? null} title={`${a} × ${g.label}`} edge />
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
