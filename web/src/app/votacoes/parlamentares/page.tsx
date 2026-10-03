import Link from "next/link";
import {
  getDeputySummary,
  listParliamentarians,
  votesAvailable,
  type Candidacy2026,
  type ParliamentarianListItem,
} from "@/lib/votes-queries";
import { DEFINITIONS, HOUSE_LABEL, type House } from "@/lib/vote-meaning";
import { getPersonPhotoUrl } from "@/lib/queries";
import { PageHeader } from "@/components/shell/shell-context";
import { SourceZone } from "@/components/source-zone";
import { SearchAvatar } from "@/components/search-avatar";
import { EmptyState } from "@/components/ui/empty-state";
import { PaginationLinks } from "@/components/ui/pagination-links";
import { PRESIDED_NOTE, listedVotes, presidedOften } from "@/components/votes/parliamentarian-kpis";
import {
  Definition,
  governmentRows,
  isHouse,
  parliamentarianHref,
  pctText,
  ratioSub,
} from "@/components/votes/parliamentarian-shared";
import { MIN_N, RatioValue, SELECT_CLASS } from "@/components/votes/party-ui";
import { formatVoteDate } from "@/components/votes/vote-result";

export const dynamic = "force-dynamic";

// Metrics cost ~40 ms per parliamentarian (one getDeputySummary each): a page stays small.
const PAGE_SIZE = 25;

const UFS = [
  "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA",
  "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO",
];
const ALL_UF = "todas";

function candidacyText(c: Candidacy2026): string {
  return `Candidato(a) a ${c.office} em 2026${c.number ? ` — nº ${c.number}` : ""}${c.party ? ` (${c.party})` : ""}`;
}

// Office filter of the 2026 candidates (politician_history.office = TSE DS_CARGO).
// Default = Deputado federal: the original ask was "candidatos a deputado 2026".
const OFFICES = [
  { value: "deputado-federal", label: "a deputado federal", office: "DEPUTADO FEDERAL" },
  { value: "senador", label: "a senador", office: "SENADOR" },
  { value: "governador", label: "a governador", office: "GOVERNADOR" },
  { value: "deputado-distrital", label: "a deputado distrital", office: "DEPUTADO DISTRITAL" },
  { value: "qualquer", label: "a qualquer cargo", office: undefined },
] as const;
type OfficeOption = (typeof OFFICES)[number];
const DEFAULT_OFFICE = OFFICES[0];

type Filters = { casa: House; uf: string; candidatos: boolean; cargo: OfficeOption; partido: string; q: string };

function hrefFor(f: Filters, page = 1): string {
  const usp = new URLSearchParams();
  if (f.casa !== "camara") usp.set("casa", f.casa);
  usp.set("uf", f.uf);
  if (!f.candidatos) usp.set("candidatos", "0");
  else if (f.cargo !== DEFAULT_OFFICE) usp.set("cargo", f.cargo.value);
  if (f.partido) usp.set("partido", f.partido);
  if (f.q) usp.set("q", f.q);
  if (page > 1) usp.set("page", String(page));
  return `/votacoes/parlamentares?${usp.toString()}`;
}

export default async function ParlamentaresPage({ searchParams }: PageProps<"/votacoes/parlamentares">) {
  const sp = await searchParams;
  const one = (k: string) => (typeof sp[k] === "string" ? (sp[k] as string) : "");
  const casa: House = isHouse(one("casa")) ? (one("casa") as House) : "camara";
  const ufParam = one("uf").toUpperCase();
  const uf = ufParam === ALL_UF.toUpperCase() ? ALL_UF : UFS.includes(ufParam) ? ufParam : "DF";
  const filters: Filters = {
    casa,
    uf,
    candidatos: one("candidatos") !== "0",
    cargo: OFFICES.find((o) => o.value === one("cargo")) ?? DEFAULT_OFFICE,
    partido: one("partido").trim(),
    q: one("q").trim(),
  };
  const page = Math.max(1, Math.floor(Number(one("page")) || 1));

  const available = votesAvailable(casa);
  const base = available
    ? listParliamentarians({
        house: casa,
        uf: uf === ALL_UF ? undefined : uf,
        candidates2026: filters.candidatos,
        office: filters.cargo.office,
        q: filters.q || undefined,
      })
    : [];
  const parties = [...new Set(base.map((m) => m.party).filter((p): p is string => !!p))].sort();
  const all = filters.partido ? base.filter((m) => m.party === filters.partido) : base;
  const totalPages = Math.max(1, Math.ceil(all.length / PAGE_SIZE));
  const rows = all.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE).map((m) => withMetrics(casa, m));

  return (
    <div className="flex flex-col gap-8">
      <PageHeader group="Votações" current="Parlamentares" />

      <section>
        <h1 className="text-[26px] leading-tight font-medium tracking-tight">
          Como cada parlamentar votou no Plenário
        </h1>
        <p className="mt-4 max-w-2xl text-[13px] leading-relaxed" style={{ color: "var(--muted)" }}>
          Votações nominais do Plenário da {HOUSE_LABEL.camara} e do {HOUSE_LABEL.senado} de 2019 a
          2026. Por padrão, só votações de mérito (sem requerimentos de procedimento). Partido e UF
          são os do último voto registrado; em cada votação vale o partido daquela data.
        </p>

        <div className="mt-5 flex flex-wrap items-center gap-2">
          {(["camara", "senado"] as const).map((h) => (
            <Link
              key={h}
              href={hrefFor({ ...filters, casa: h, partido: "" })}
              className={`btn${casa === h ? " btn--primary" : ""}`}
            >
              {HOUSE_LABEL[h]}
            </Link>
          ))}
        </div>

        <form className="mt-3 flex flex-wrap items-center gap-2" action="/votacoes/parlamentares">
          {casa !== "camara" ? <input type="hidden" name="casa" value={casa} /> : null}
          <select name="uf" defaultValue={uf} className={SELECT_CLASS} aria-label="UF">
            <option value={ALL_UF}>todas as UFs</option>
            {UFS.map((u) => (
              <option key={u} value={u}>{u}</option>
            ))}
          </select>
          <select
            name="candidatos"
            defaultValue={filters.candidatos ? "1" : "0"}
            className={SELECT_CLASS}
            aria-label="candidatos em 2026"
          >
            <option value="1">candidatos em 2026</option>
            <option value="0">todos que votaram</option>
          </select>
          <select name="cargo" defaultValue={filters.cargo.value} className={SELECT_CLASS} aria-label="cargo em 2026">
            {OFFICES.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
          <select name="partido" defaultValue={filters.partido} className={SELECT_CLASS} aria-label="partido">
            <option value="">todos os partidos</option>
            {parties.map((p) => (
              <option key={p} value={p}>{p}</option>
            ))}
          </select>
          <div className="input" style={{ width: 220 }}>
            <input name="q" defaultValue={filters.q} placeholder="buscar por nome…" />
          </div>
          <button type="submit" className="btn">filtrar</button>
        </form>

        <div className="mono mt-4" style={{ fontSize: 11, color: "var(--muted)" }}>
          <span style={{ color: "var(--fg-1)" }}>{all.length.toLocaleString("pt-BR")}</span>{" "}
          {all.length === 1 ? "parlamentar" : "parlamentares"}
          {filters.candidatos
            ? ` que ${all.length === 1 ? "votou" : "votaram"} ${casa === "camara" ? "na Câmara" : "no Senado"}` +
              ` e ${all.length === 1 ? "é candidato" : "são candidatos"} ` +
              `${filters.cargo.label} em 2026${uf === ALL_UF ? "" : ` por ${uf}`}`
            : uf === ALL_UF ? "" : ` com último voto registrado por ${uf}`}
        </div>
        {filters.candidatos ? (
          <p className="mt-1 max-w-2xl text-[11.5px] text-[var(--muted-2)]">
            Candidatura de 2026 segundo o TSE. Um parlamentar pode concorrer a outro cargo (um
            deputado pode disputar o Senado): troque o cargo acima para ver. &quot;Na urna&quot; é o
            nome que aparece na urna eletrônica. Só aparece quem está vinculado à ficha do TSE; para
            ver todos, escolha &quot;todos que votaram&quot;.
          </p>
        ) : (
          <p className="mt-1 max-w-2xl text-[11.5px] text-[var(--muted-2)]">
            O filtro de cargo só vale para &quot;candidatos em 2026&quot;.
          </p>
        )}
      </section>

      <section>
        {!available ? (
          <EmptyState
            title="votações dessa casa ainda não coletadas"
            hint={<code>{casa === "camara" ? "elosys camara-votes" : "elosys senado-votes"}</code>}
          />
        ) : rows.length === 0 ? (
          <EmptyState title="ninguém para esse filtro." />
        ) : (
          <>
            <div className="table-wrap">
              <div className="overflow-x-auto">
                <table className="table min-w-[880px]">
                  <thead>
                    <tr>
                      <th>parlamentar</th>
                      <th>partido · UF</th>
                      <th className="text-right">votou em</th>
                      <th className="text-right">fidelidade ao partido</th>
                      <th className="text-right">votou com o Governo</th>
                      <th className="text-right">votações</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => (
                      <SourceZone key={r.m.id} as="tr" provenance={r.provenance}>
                        <td>
                          <div className="flex items-center gap-2">
                            <SearchAvatar photoUrl={r.photoUrl} name={r.m.name ?? "?"} />
                            <div>
                              <Link href={parliamentarianHref(casa, r.m.id)} className="hover:underline">
                                {r.m.name ?? `parlamentar ${r.m.id}`}
                              </Link>
                              {r.m.candidacy2026 ? (
                                <div className="mt-0.5">
                                  <span className="badge badge--accent" style={{ fontSize: 9.5 }}>
                                    {candidacyText(r.m.candidacy2026)}
                                  </span>
                                  {r.m.candidacy2026.ballotName ? (
                                    <div className="mono mt-0.5" style={{ fontSize: 10, color: "var(--muted)" }}>
                                      na urna: &quot;{r.m.candidacy2026.ballotName}&quot;
                                    </div>
                                  ) : null}
                                </div>
                              ) : null}
                            </div>
                          </div>
                        </td>
                        <td className="mono" style={{ fontSize: 11.5 }}>
                          {r.m.party ?? "—"} · {r.m.state ?? "—"}
                          <div style={{ fontSize: 9.5, color: "var(--muted-2)" }}>
                            último voto {formatVoteDate(r.m.lastVoteDate)}
                          </div>
                        </td>
                        <td className="num">
                          {r.voted ? <RatioValue r={r.voted} /> : "—"}
                          {r.secondary ? (
                            <div style={{ fontSize: 10, color: "var(--muted-2)" }}>{r.secondary}</div>
                          ) : null}
                          {r.presidedOften ? (
                            <div style={{ fontSize: 10, color: "var(--muted)", whiteSpace: "normal" }} title={PRESIDED_NOTE}>
                              presidiu {r.presiding.toLocaleString("pt-BR")} sessões: em regra o presidente não vota
                            </div>
                          ) : null}
                        </td>
                        <td className="num">{r.fidelity ? <RatioValue r={r.fidelity} /> : "—"}</td>
                        <td className="num" style={{ whiteSpace: "normal" }}>
                          {r.governments.map((g) => (
                            <div key={g.id} className="mb-1">
                              <div style={{ fontSize: 10, color: "var(--muted-2)" }}>{g.label}</div>
                              {g.ratio ? <RatioValue r={g.ratio} /> : "—"}
                            </div>
                          ))}
                        </td>
                        <td className="num" style={{ color: "var(--muted)" }}>{r.total.toLocaleString("pt-BR")}</td>
                      </SourceZone>
                    ))}
                  </tbody>
                </table>
              </div>
              {totalPages > 1 ? (
                <div className="table-footer">
                  <PaginationLinks page={page} totalPages={totalPages} makeHref={(p) => hrefFor(filters, p)} />
                </div>
              ) : null}
            </div>

            <div className="mt-6 grid max-w-3xl gap-1">
              <Definition label='o que é "votou em"' text={DEFINITIONS.voted} />
              <Definition
                label={casa === "camara" ? "o que é presença na sessão" : 'o que é "não registrou voto"'}
                text={casa === "camara" ? DEFINITIONS.presenceCamara : DEFINITIONS.presenceSenado}
              />
              <Definition
                label="o que é fidelidade ao partido"
                text={`${DEFINITIONS.fidelity} ${casa === "camara" ? DEFINITIONS.camaraBench : DEFINITIONS.senadoOrientation}`}
              />
              <Definition label="o que é votar com o Governo" text={DEFINITIONS.government} />
              <p className="mt-1 text-[11px] text-[var(--muted-2)]">
                Percentuais sobre as votações de mérito de 2019 a 2026 em que o parlamentar aparece.
                Embaixo de cada %, quantas votações entraram na conta (&quot;2 de 2&quot;); com menos
                de {MIN_N}, o número fica em cinza com &quot;poucos dados&quot;. Abra o parlamentar
                para filtrar por ano, tema ou incluir procedimentais.
              </p>
            </div>
          </>
        )}
      </section>
    </div>
  );
}

function withMetrics(house: House, m: ParliamentarianListItem) {
  const s = getDeputySummary(house, m.id);
  const total = s ? listedVotes(s) : 0;
  return {
    m,
    photoUrl: m.personId != null ? getPersonPhotoUrl(m.personId) : null,
    provenance: s?.identity.provenance ?? null,
    voted: s ? s.presence.voted : null,
    // Secondary, labelled per house: never comparable across houses (DEFINITIONS.voted).
    secondary: !s
      ? null
      : house === "camara"
        ? s.presence.available
          ? `presença na sessão ${pctText(s.presence.rate)} · ${ratioSub(s.presence.rate, "votações")}`
          : null
        : `não registrou voto: ${(s.presence.counts.present_not_voted ?? 0).toLocaleString("pt-BR")}`,
    presiding: s?.presence.presiding ?? 0,
    presidedOften: s ? presidedOften(s.presence.presiding, total) : false,
    fidelity: s ? s.fidelity : null,
    governments: s ? governmentRows(s.withGovernment) : [],
    total,
  };
}
