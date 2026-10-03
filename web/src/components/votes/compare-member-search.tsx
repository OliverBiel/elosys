import Link from "next/link";
import type { ParliamentarianIdentity, ParliamentarianListItem } from "@/lib/votes-queries";
import type { House } from "@/lib/vote-meaning";
import { SourceZone } from "@/components/source-zone";

const memberWord = (house: House) => (house === "camara" ? "deputado(a)" : "senador(a)");

/** Selected members (with remove links) + a GET search box whose results are "add" links. */
export function CompareMemberSearch({
  house, action, hidden, q, results, selected, max, addHref, removeHref, memberHref,
}: {
  house: House;
  action: string;
  hidden: Record<string, string | string[] | undefined>;
  q: string;
  results: ParliamentarianListItem[];
  selected: ParliamentarianIdentity[];
  max: number;
  addHref: (id: number) => string;
  removeHref: (id: number) => string;
  memberHref: (id: number) => string;
}) {
  const chosen = new Set(selected.map((m) => m.id));
  const full = selected.length >= max;
  return (
    <div className="flex flex-col gap-3">
      {selected.length > 0 ? (
        <div className="flex flex-wrap items-center gap-2">
          {selected.map((m) => (
            <SourceZone key={m.id} provenance={m.provenance} inline>
              <span className="badge" style={{ gap: 6, padding: "4px 8px" }}>
                <Link href={memberHref(m.id)} className="hover:underline" style={{ color: "var(--fg-1)" }}>
                  {m.name ?? `#${m.id}`}
                </Link>
                <span style={{ color: "var(--muted-2)" }}>
                  {[m.lastParty, m.lastState].filter(Boolean).join("-")}
                </span>
                <Link href={removeHref(m.id)} aria-label={`remover ${m.name ?? m.id}`} className="hover:underline">
                  ×
                </Link>
              </span>
            </SourceZone>
          ))}
        </div>
      ) : null}
      <form className="flex flex-wrap items-center gap-2" action={action}>
        {Object.entries(hidden).flatMap(([k, v]) =>
          (v == null ? [] : Array.isArray(v) ? v : [v]).map((x, i) => (
            <input key={`${k}-${i}`} type="hidden" name={k} value={x} />
          ))
        )}
        <div className="input" style={{ width: 280 }}>
          <input name="q" defaultValue={q} placeholder={`buscar ${memberWord(house)} pelo nome…`} />
        </div>
        <button type="submit" className="btn">buscar</button>
        {full ? (
          <span className="text-[12px]" style={{ color: "var(--muted-2)" }}>
            máximo de {max}; remova um para trocar
          </span>
        ) : null}
      </form>
      {q ? (
        results.length === 0 ? (
          <p className="text-[12px]" style={{ color: "var(--muted-2)" }}>
            ninguém com esse nome votou nesse período.
          </p>
        ) : (
          <ul className="flex flex-wrap gap-2">
            {results.map((r) => (
              <li key={r.id}>
                {chosen.has(r.id) || full ? (
                  <span className="badge" style={{ opacity: 0.6 }}>
                    {r.name} · {[r.party, r.state].filter(Boolean).join("-")}
                  </span>
                ) : (
                  <Link href={addHref(r.id)} className="badge hover:underline" style={{ color: "var(--fg-1)" }}>
                    + {r.name} · {[r.party, r.state].filter(Boolean).join("-")}
                  </Link>
                )}
              </li>
            ))}
          </ul>
        )
      ) : null}
    </div>
  );
}
