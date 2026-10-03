import Link from "next/link";
import type { Provenance } from "@/lib/queries";
import type { VoteCastRow } from "@/lib/votes-queries";
import type { House, VoteValue } from "@/lib/vote-meaning";
import { SourceZone } from "@/components/source-zone";

const VALUE_COLOR: Partial<Record<VoteValue, string>> = {
  yes: "var(--green)",
  no: "var(--red)",
};

export function VoteCastsTable({
  house, casts, provenance,
}: {
  house: House;
  casts: VoteCastRow[];
  provenance: Provenance | null;
}) {
  return (
    <div className="table-wrap">
      <div className="overflow-x-auto">
        <table className="table min-w-[640px]">
          <thead>
            <tr>
              <th>{house === "camara" ? "deputado(a)" : "senador(a)"}</th>
              <th>partido</th>
              <th>UF</th>
              <th>voto</th>
            </tr>
          </thead>
          <tbody>
            {casts.map((c) => (
              <SourceZone key={c.memberId} provenance={provenance} as="tr">
                <td>
                  <Link href={`/votacoes/parlamentares/${house}/${c.memberId}`} className="hover:underline">
                    {c.name ?? `código ${c.memberId}`}
                  </Link>
                  {c.personId != null ? (
                    <Link
                      href={`/politico/${c.personId}`}
                      className="link-primary mono ml-2 text-[10.5px]"
                      title="perfil completo no EloSys (candidaturas, bens, doações)"
                    >
                      perfil
                    </Link>
                  ) : null}
                </td>
                <td className="mono text-[12px]">{c.party ?? "—"}</td>
                <td className="mono text-[12px]">{c.state ?? "—"}</td>
                <td className="text-[13px]" title={c.raw ? `valor na fonte: ${c.raw}` : undefined}>
                  <span style={{ color: VALUE_COLOR[c.value] ?? "var(--fg-2)" }}>{c.label}</span>
                </td>
              </SourceZone>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
