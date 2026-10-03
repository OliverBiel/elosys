import Link from "next/link";
import type { Provenance } from "@/lib/queries";
import type { PartyBreakdown } from "@/lib/votes-queries";
import { DEFINITIONS, type House, type VoteValue } from "@/lib/vote-meaning";
import { SourceZone } from "@/components/source-zone";
import { formatPct } from "@/lib/format";
import { MIN_N } from "./party-ui";

const COUNTED: VoteValue[] = ["yes", "no", "abstention", "obstruction", "presiding"];
const COUNTED_SECRET: VoteValue[] = [...COUNTED, "secret"];

function Count({ n }: { n: number | undefined }) {
  return <span style={{ color: n ? "var(--fg-1)" : "var(--muted-2)" }}>{n ?? 0}</span>;
}

/** One row per party (as named at vote time); counts come from the cast list. */
export function VotePartyTable({
  house, parties, provenance, legislature,
}: {
  house: House;
  parties: PartyBreakdown[];
  provenance: Provenance | null;
  /** The vote's legislature: the party link opens that period, which contains this vote. */
  legislature: number | null;
}) {
  const period = legislature ? `&periodo=leg${legislature}` : "";
  // Secret votes: every cast is 'secret', so it gets its own column instead of "outros".
  const secret = parties.some((p) => (p.counts.secret ?? 0) > 0);
  const counted = secret ? COUNTED_SECRET : COUNTED;
  return (
    <div className="flex flex-col gap-3">
      <div className="table-wrap">
        <div className="overflow-x-auto">
          <table className="table min-w-[900px]">
            <thead>
              <tr>
                <th>partido</th>
                <th>orientação aplicada</th>
                <th className="text-right">Sim</th>
                <th className="text-right">Não</th>
                <th className="text-right">Abst.</th>
                <th className="text-right">Obstr.</th>
                <th className="text-right">Presid.</th>
                {secret ? <th className="text-right">Secreto</th> : null}
                <th className="text-right" title="não registrou voto, ausências…">outros</th>
                <th>maioria do partido</th>
                <th className="text-right">% que seguiu</th>
              </tr>
            </thead>
            <tbody>
              {parties.map((p) => {
                const shown = counted.reduce((s, v) => s + (p.counts[v] ?? 0), 0);
                const o = p.orientation;
                // match_basis 'party' = own bench ("Solidaried"); only blocs/feds add info.
                const viaBench = house === "camara" && o.benchLabel && o.matchBasis !== "party";
                return (
                  <SourceZone key={p.party} provenance={provenance} as="tr">
                    <td>
                      <Link
                        href={`/votacoes/partidos/${encodeURIComponent(p.party)}?casa=${house}${period}`}
                        className="font-medium hover:underline"
                      >
                        {p.party}
                      </Link>
                    </td>
                    <td className="text-[13px]">
                      <span style={{ color: o.value == null ? "var(--muted-2)" : "var(--fg-1)" }}>{o.label}</span>
                      {viaBench ? (
                        <div className="mono text-[10.5px]" style={{ color: "var(--muted-2)" }}>
                          via bancada {o.benchLabel}
                        </div>
                      ) : null}
                    </td>
                    <td className="num"><Count n={p.counts.yes} /></td>
                    <td className="num"><Count n={p.counts.no} /></td>
                    <td className="num"><Count n={p.counts.abstention} /></td>
                    <td className="num"><Count n={p.counts.obstruction} /></td>
                    <td className="num"><Count n={p.counts.presiding} /></td>
                    {secret ? <td className="num"><Count n={p.counts.secret} /></td> : null}
                    <td className="num"><Count n={p.listed - shown} /></td>
                    <td className="text-[13px]">{p.majorityLabel}</td>
                    <td className="num">
                      {p.followed && p.followed.pct != null ? (
                        <span title={`${p.followed.agree} de ${p.followed.n} votos iguais à orientação`}>
                          <span style={{ color: p.followed.n < MIN_N ? "var(--muted-2)" : undefined }}>
                            {formatPct(p.followed.pct)}
                          </span>
                          <span className="ml-1 text-[10.5px]" style={{ color: "var(--muted-2)" }}>
                            ({p.followed.agree} de {p.followed.n}{p.followed.n < MIN_N ? " · poucos dados" : ""})
                          </span>
                        </span>
                      ) : (
                        <span
                          style={{ color: "var(--muted-2)" }}
                          title="O partido não orientou Sim, Não ou Obstrução nesta votação"
                        >
                          —
                        </span>
                      )}
                    </td>
                  </SourceZone>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
      <div className="flex max-w-3xl flex-col gap-1.5 text-[11.5px] leading-relaxed" style={{ color: "var(--muted-2)" }}>
        <p>{DEFINITIONS.fidelity}</p>
        <p>{DEFINITIONS.majorityPosition}</p>
        <p>{house === "camara" ? DEFINITIONS.camaraBench : DEFINITIONS.senadoOrientation}</p>
      </div>
    </div>
  );
}
