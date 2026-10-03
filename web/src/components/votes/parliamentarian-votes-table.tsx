import Link from "next/link";
import type { ParliamentarianVoteRow } from "@/lib/votes-queries";
import { isRealParty } from "@/lib/votes-queries";
import type { House } from "@/lib/vote-meaning";
import { SourceZone } from "@/components/source-zone";
import { voteHref } from "./parliamentarian-shared";
import { formatVoteDate } from "./vote-result";

function followedCell(v: boolean | null) {
  if (v == null) return <span style={{ color: "var(--muted-2)" }}>—</span>;
  return v ? <span className="badge badge--green">Sim</span> : <span className="badge badge--red">Não</span>;
}

function valueTone(r: ParliamentarianVoteRow): string {
  if (r.value === "absent") return "var(--red)";
  if (r.value === "yes" || r.value === "no" || r.value === "obstruction" || r.value === "abstention") {
    return "var(--fg-1)";
  }
  return "var(--muted)";
}

/** One row per vote that concerns the parliamentarian, newest first; each row cites its own file. */
export function ParliamentarianVotesTable({
  house, rows, compact = false,
}: {
  house: House;
  rows: ParliamentarianVoteRow[];
  /** Compact = the /politico summary: no orientation/result columns. */
  compact?: boolean;
}) {
  return (
    <div className="table-wrap">
      <div className="overflow-x-auto">
        <table className={`table ${compact ? "min-w-[720px]" : "min-w-[1080px]"}`}>
          <thead>
            <tr>
              <th>data</th>
              <th>votação</th>
              <th>o que o Sim significava</th>
              <th>voto</th>
              {compact ? null : <th>orientação do partido</th>}
              <th>seguiu o partido?</th>
              {compact ? null : <th>resultado</th>}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <SourceZone key={r.voteId} provenance={r.provenance} as="tr">
                <td className="mono whitespace-nowrap" style={{ fontSize: 11.5 }}>{formatVoteDate(r.date)}</td>
                <td className="max-w-[300px]">
                  <span className="badge mb-1">{r.kindLabel}</span>
                  {r.propositions.length > 0 ? (
                    <div className="mono text-[11.5px] font-medium" style={{ color: "var(--fg-1)" }}>
                      {r.propositions.join(", ")}
                    </div>
                  ) : null}
                  <Link href={voteHref(house, r.voteId)} className="block text-[12.5px] leading-snug hover:underline">
                    {r.description ?? `Votação ${r.voteId}`}
                  </Link>
                </td>
                <td className="max-w-[260px] text-[12px] leading-snug" style={{ color: "var(--muted)" }}>
                  {r.meaning}
                </td>
                <td className="max-w-[200px] text-[12.5px]">
                  <span style={{ color: valueTone(r) }}>{r.label}</span>
                  {r.party ? (
                    <div className="mono" style={{ fontSize: 10, color: "var(--muted-2)" }}>
                      {isRealParty(r.party) ? `partido na data: ${r.party}` : `sem partido (${r.party})`}
                    </div>
                  ) : null}
                </td>
                {compact ? null : (
                  <td className="max-w-[200px] text-[12px]">
                    {r.partyOrientation ? r.partyOrientation.label : <span style={{ color: "var(--muted-2)" }}>—</span>}
                    <div className="mono" style={{ fontSize: 10, color: "var(--muted-2)" }}>
                      {r.government.government ? `${r.government.government.label}: ` : "Governo: "}
                      {r.government.label}
                    </div>
                  </td>
                )}
                <td>{followedCell(r.followedParty)}</td>
                {compact ? null : (
                  <td className="max-w-[160px] text-[12px]">
                    {r.result.label}
                    {r.result.note ? (
                      <div style={{ fontSize: 10.5, color: "var(--muted-2)" }}>{r.result.note}</div>
                    ) : null}
                  </td>
                )}
              </SourceZone>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
