import Link from "next/link";
import {
  DEFINITIONS, type DefinitionKey, type GovernmentPeriod, HOUSE_LABEL, type House,
} from "@/lib/vote-meaning";
import type { OrientationCoverageYear, Ratio, VoteListItem } from "@/lib/votes-queries";
import { formatPct } from "@/lib/format";
import { formatVoteDate } from "./vote-result";

/** Below this many compared votes a percentage is shown greyed as "poucos dados". */
export const MIN_N = 10;

export const SELECT_CLASS =
  "mono rounded-[var(--r-md)] border border-[var(--border-1)] bg-[var(--card-tone)] px-2.5 py-1.5 " +
  "text-[12px] text-[var(--fg-1)] outline-none focus:border-[var(--border-2)]";

/** "Governo Lula (2023–)", "Governo Bolsonaro (2019–2022)". */
export function governmentName(g: GovernmentPeriod): string {
  return `${g.label} (${g.start.slice(0, 4)}–${g.end ? g.end.slice(0, 4) : ""})`;
}

export const fmtInt = (n: number) => n.toLocaleString("pt-BR");

/** Percentage + N. N < MIN_N greyed with "poucos dados"; N = 0 is a dash. */
export function RatioValue({ r, align = "right" }: { r: Ratio; align?: "left" | "right" }) {
  if (r.n === 0 || r.pct == null) {
    return (
      <span className="mono" style={{ color: "var(--muted-2)" }} title="nenhuma votação comparável">
        —
      </span>
    );
  }
  const low = r.n < MIN_N;
  return (
    <span className={`inline-flex flex-col ${align === "right" ? "items-end" : "items-start"}`}>
      <span className="mono" style={{ color: low ? "var(--muted-2)" : "var(--fg-1)" }}>{formatPct(r.pct)}</span>
      <span className="mono" style={{ fontSize: 10, color: "var(--muted-2)" }}>
        {fmtInt(r.agree)} de {fmtInt(r.n)}
        {low ? " · poucos dados" : ""}
      </span>
    </span>
  );
}

export function Kpi({ label, value, sub }: { label: string; value: React.ReactNode; sub?: React.ReactNode }) {
  return (
    <div className="kpi">
      <div className="kpi__label">{label}</div>
      <div className="kpi__value">{value}</div>
      {sub ? <div className="kpi__sub" style={{ fontFamily: "var(--font-sans)", fontSize: 11 }}>{sub}</div> : null}
    </div>
  );
}

export function KpiRatio({ label, r, sub }: { label: string; r: Ratio | null; sub?: React.ReactNode }) {
  if (!r) return <Kpi label={label} value={<span style={{ color: "var(--muted-2)" }}>—</span>} sub="sem votações desse governo no período" />;
  const low = r.n > 0 && r.n < MIN_N;
  return (
    <Kpi
      label={label}
      value={
        r.pct == null ? (
          <span style={{ color: "var(--muted-2)" }}>—</span>
        ) : (
          <span style={{ color: low ? "var(--muted-2)" : undefined }}>{formatPct(r.pct)}</span>
        )
      }
      sub={
        <>
          {r.n === 0 ? "nenhuma votação comparável" : `${fmtInt(r.agree)} de ${fmtInt(r.n)}`}
          {low ? " · poucos dados" : ""}
          {sub ? <> · {sub}</> : null}
        </>
      }
    />
  );
}

/** "como calculamos": the DEFINITIONS text of every metric shown, folded by default. */
export function HowCalculated({ keys, extra }: { keys: DefinitionKey[]; extra?: React.ReactNode }) {
  return (
    <details className="mt-3 max-w-3xl text-[12px] leading-relaxed" style={{ color: "var(--muted)" }}>
      <summary className="cursor-pointer select-none" style={{ color: "var(--fg-2)" }}>
        como calculamos
      </summary>
      <ul className="mt-2 flex list-disc flex-col gap-1.5 pl-5">
        {keys.map((k) => (
          <li key={k}>{DEFINITIONS[k]}</li>
        ))}
        {extra ? <li>{extra}</li> : null}
      </ul>
    </details>
  );
}

export function Note({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="max-w-3xl rounded-[var(--r-md)] border px-3.5 py-2.5 text-[12px] leading-relaxed"
      style={{ borderColor: "var(--border-2)", color: "var(--muted)", background: "var(--card-tone)" }}
    >
      {children}
    </div>
  );
}

/** Senado: party orientation is only partly published; coverage per year from the data layer. */
export function SenadoCoverageNote({ coverage }: { coverage: OrientationCoverageYear[] }) {
  return (
    <Note>
      {DEFINITIONS.senadoOrientation}
      {coverage.length > 0 ? (
        <div className="mono mt-2 flex flex-wrap gap-x-3 gap-y-1" style={{ fontSize: 11 }}>
          <span style={{ color: "var(--muted-2)" }}>votações com orientação publicada:</span>
          {coverage.map((c) => (
            <span key={c.year}>
              {c.year}{" "}
              <span style={{ color: "var(--fg-1)" }}>{c.pct == null ? "—" : formatPct(c.pct)}</span>
              <span style={{ color: "var(--muted-2)" }}> ({c.votesWithPartyOrientation}/{c.votes})</span>
            </span>
          ))}
        </div>
      ) : null}
    </Note>
  );
}

export function HouseTabs({ house, hrefFor }: { house: House; hrefFor: (h: House) => string }) {
  return (
    <div className="flex flex-wrap items-center gap-2" role="group" aria-label="casa legislativa">
      {(["camara", "senado"] as const).map((h) => (
        <Link key={h} href={hrefFor(h)} className={`btn${house === h ? " btn--primary" : ""}`}>
          {HOUSE_LABEL[h]}
        </Link>
      ))}
    </div>
  );
}

/** First cell of a vote row: date, kind, proposition (from listVotes), ementa, official text. */
export function VoteHead({
  href, voteId, date, kindLabel, description, info,
}: {
  href: string;
  voteId: string;
  date: string;
  kindLabel: string;
  description: string | null;
  info: VoteListItem | undefined;
}) {
  return (
    <>
      <div className="mono" style={{ fontSize: 10.5, color: "var(--muted-2)" }}>
        {formatVoteDate(date)} · {kindLabel}
      </div>
      <Link href={href} className="link-primary font-medium">
        {info?.propositions.length ? info.propositions.join(", ") : `votação ${voteId}`}
      </Link>
      {info?.ementa ? (
        <div className="mt-0.5 line-clamp-3 text-[12px]" style={{ color: "var(--fg-2)" }}>{info.ementa}</div>
      ) : null}
      {description ? (
        <div className="mt-1 line-clamp-2 text-[11px]" style={{ color: "var(--muted-2)" }} title={description}>
          {description}
        </div>
      ) : null}
    </>
  );
}
