import type { ParliamentarianVoteRow } from "@/lib/votes-queries";
import { isRealParty } from "@/lib/votes-queries";
import { PARTY_NAME_NOTE, type House } from "@/lib/vote-meaning";
import { formatVoteDate } from "./vote-result";

type Run = { party: string; from: string; to: string; votes: number };

// Consecutive runs of the party written on each cast row (party at vote time), oldest first.
// Rows without a cast (Câmara absent / present-not-voted) carry no party and do not break a run.
function partyRuns(rows: ParliamentarianVoteRow[]): Run[] {
  const runs: Run[] = [];
  for (let i = rows.length - 1; i >= 0; i--) {
    const r = rows[i];
    if (!r.party) continue;
    const last = runs[runs.length - 1];
    if (last && last.party === r.party) {
      last.to = r.date;
      last.votes += 1;
    } else {
      runs.push({ party: r.party, from: r.date, to: r.date, votes: 1 });
    }
  }
  return runs;
}

export function ParliamentarianPartyHistory({ house, rows }: { house: House; rows: ParliamentarianVoteRow[] }) {
  const runs = partyRuns(rows);
  if (runs.length === 0) return null;
  return (
    <div>
      <div className="flex flex-wrap items-stretch gap-2">
        {runs.map((r, i) => (
          <div key={`${r.party}-${r.from}-${i}`} className="card" style={{ padding: "10px 14px" }}>
            <div className="text-[14px]">
              {isRealParty(r.party) ? r.party : <span style={{ color: "var(--muted)" }}>sem partido ({r.party})</span>}
            </div>
            <div className="mono mt-1" style={{ fontSize: 10.5, color: "var(--muted-2)" }}>
              {formatVoteDate(r.from)} → {formatVoteDate(r.to)}
            </div>
            <div className="mono" style={{ fontSize: 10.5, color: "var(--muted-2)" }}>
              {r.votes.toLocaleString("pt-BR")} {r.votes === 1 ? "voto" : "votos"}
            </div>
          </div>
        ))}
      </div>
      <p className="mt-2 max-w-2xl text-[11px] text-[var(--muted-2)]">
        Partido escrito pela fonte em cada voto registrado (todas as votações nominais, inclusive
        procedimentais). As datas são do primeiro e do último voto com aquele partido, não da
        filiação. {PARTY_NAME_NOTE[house]}
      </p>
    </div>
  );
}
