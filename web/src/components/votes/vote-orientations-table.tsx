import type { VoteOrientationRow } from "@/lib/votes-queries";
import { SourceZone } from "@/components/source-zone";

// vote-meaning.ts has no bench_kind label (schema.sql CHECK values); UI-only wording.
const BENCH_KIND_LABEL: Record<string, string> = {
  government: "liderança do Governo",
  majority: "liderança da Maioria",
  minority: "liderança da Minoria",
  opposition: "liderança da Oposição",
  party: "partido",
  federation: "federação",
  bloc: "bloco",
  other: "outro",
};

const LEADERSHIP_ORDER = ["government", "majority", "minority", "opposition"];

function rank(kind: string): number {
  const i = LEADERSHIP_ORDER.indexOf(kind);
  return i === -1 ? LEADERSHIP_ORDER.length : i;
}

/** Orientations exactly as published, one row per bench label. */
export function VoteOrientationsTable({ rows }: { rows: VoteOrientationRow[] }) {
  const sorted = [...rows].sort(
    (a, b) => rank(a.benchKind) - rank(b.benchKind) || a.benchLabel.localeCompare(b.benchLabel, "pt-BR")
  );
  return (
    <div className="table-wrap">
      <div className="overflow-x-auto">
        <table className="table min-w-[560px]">
          <thead>
            <tr>
              <th>bancada (como publicado)</th>
              <th>tipo</th>
              <th>orientação</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((o, i) => (
              <SourceZone key={`${o.benchLabel}-${i}`} provenance={o.provenance} as="tr">
                <td className="mono text-[12.5px]">{o.benchLabel}</td>
                <td className="text-[12.5px]" style={{ color: "var(--muted)" }}>
                  {BENCH_KIND_LABEL[o.benchKind] ?? o.benchKind}
                </td>
                <td className="text-[13px]" title={`valor na fonte: ${o.raw}`}>
                  {o.label}
                </td>
              </SourceZone>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
