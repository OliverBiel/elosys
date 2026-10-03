import Link from "next/link";
import {
  compareDeputyWithParty,
  compareParliamentarians,
  getAgreementMatrix,
  getParliamentarian,
  getPartySummary,
  getSenadoOrientationCoverage,
  isRealParty,
  listParliamentarians,
  listParties,
  listYears,
  matrixCell,
  votesAvailable,
  type AgreementBasis,
  type PartyVoteRow,
  type VoteResult,
} from "@/lib/votes-queries";
import {
  DEFINITIONS,
  OF_HOUSE,
  PARTY_NAME_NOTE,
  isOrientedValue,
  isPositionValue,
  type House,
} from "@/lib/vote-meaning";
import { PageHeader } from "@/components/shell/shell-context";
import { EmptyState } from "@/components/ui/empty-state";
import { PaginationLinks } from "@/components/ui/pagination-links";
import { SourceZone } from "@/components/source-zone";
import { PartyFilters } from "@/components/votes/party-filters";
import { CompareMemberSearch } from "@/components/votes/compare-member-search";
import {
  HouseTabs, HowCalculated, KpiRatio, MIN_N, Note, RatioValue, SELECT_CLASS, SenadoCoverageNote, VoteHead,
  fmtInt,
} from "@/components/votes/party-ui";
import {
  BASIS_LABEL, defaultBasis, many, memberHref, one, parseState, partyHref, periodOptions, scopeOf, stateQuery,
  voteHref, voteIndex, type PartyPageState,
} from "../partidos/_lib/scope";

export const dynamic = "force-dynamic";

const PAGE_SIZE = 50;
const MAX = 4;
const MODES = ["partidos", "parlamentares", "parlamentar-partido"] as const;
type Mode = (typeof MODES)[number];

const modeLabel = (m: Mode, house: House) => {
  const who = house === "camara" ? "deputado" : "senador";
  if (m === "partidos") return "partido × partido";
  if (m === "parlamentares") return `${who} × ${who}`;
  return `${who} × partido`;
};

export default async function CompararPage({ searchParams }: PageProps<"/votacoes/comparar">) {
  const sp = await searchParams;
  const casa = one(sp.casa) === "senado" ? "senado" : "camara";
  const years = listYears(casa);
  const state = parseState(sp, years);
  const { house } = state;
  const modeParam = one(sp.modo);
  const mode: Mode = (MODES as readonly string[]).includes(modeParam ?? "") ? (modeParam as Mode) : "partidos";
  const page = Math.max(1, Number(one(sp.page)) || 1);
  const q = one(sp.q)?.trim() ?? "";
  const coverage = house === "senado" ? getSenadoOrientationCoverage() : [];

  const modeHref = (m: Mode) => `/votacoes/comparar${stateQuery(state, { modo: m === "partidos" ? undefined : m })}`;
  const houseHref = (h: House) =>
    `/votacoes/comparar${stateQuery({ ...state, house: h, basis: defaultBasis(h) }, { modo: mode === "partidos" ? undefined : mode })}`;

  return (
    <div className="flex flex-col gap-8">
      <PageHeader group="Votações" current="Comparar" />

      <section>
        <h1 className="text-[26px] leading-tight font-medium tracking-tight">
          Comparar quem vota junto — e onde se separam
        </h1>
        <p className="mt-4 max-w-2xl text-[13px] leading-relaxed" style={{ color: "var(--muted)" }}>
          Escolha 2 a {MAX} partidos ou parlamentares da mesma Casa. Mostramos em quantas das votações em
          comum eles tiveram a mesma posição e, abaixo, a lista das votações em que{" "}
          <strong style={{ color: "var(--fg-2)" }}>divergiram</strong> — é ali que se vê a diferença real.
        </p>

        <div className="mt-5 flex flex-col gap-3">
          <HouseTabs house={house} hrefFor={houseHref} />
          <div className="flex flex-wrap items-center gap-2" role="group" aria-label="modo de comparação">
            {MODES.map((m) => (
              <Link key={m} href={modeHref(m)} className={`btn${mode === m ? " btn--primary" : ""}`}>
                {modeLabel(m, house)}
              </Link>
            ))}
          </div>
        </div>
      </section>

      {house === "senado" && mode !== "parlamentares" ? <SenadoCoverageNote coverage={coverage} /> : null}

      {!votesAvailable(house) ? (
        <EmptyState icon="◌" title={`nenhuma votação ${OF_HOUSE[house]} coletada ainda`} />
      ) : mode === "partidos" ? (
        <PartiesMode state={state} sp={sp} page={page} years={years} />
      ) : mode === "parlamentares" ? (
        <MembersMode state={state} sp={sp} page={page} years={years} q={q} />
      ) : (
        <MemberPartyMode state={state} sp={sp} page={page} years={years} q={q} />
      )}
    </div>
  );
}

type ModeProps = {
  state: PartyPageState;
  sp: Record<string, string | string[] | undefined>;
  page: number;
  years: number[];
};

// ---------------------------------------------------------------- partido × partido

function partyPosition(r: PartyVoteRow, basis: AgreementBasis): string | null {
  if (basis === "orientation") return isOrientedValue(r.orientation.value) ? r.orientation.value : null;
  // Senado party that oriented obstruction: no countable majority (same as the matrix).
  return r.majorityCounted && isOrientedValue(r.majority) ? r.majority : null;
}

type PartyDivergence = {
  voteId: string;
  date: string;
  description: string | null;
  kindLabel: string;
  meaning: string;
  result: VoteResult;
  byParty: Map<string, PartyVoteRow>;
};

function PartiesMode({ state, sp, page, years }: ModeProps) {
  const { house, basis } = state;
  const scope = scopeOf(state);
  const options = listParties(house, scope).map((p) => p.party);
  const selected = [...new Set(many(sp.p).filter((p) => isRealParty(p)))].slice(0, MAX);
  const action = "/votacoes/comparar";

  const filters = (
    <PartyFilters
      action={action}
      hidden={{ casa: house === "senado" ? "senado" : undefined }}
      periods={periodOptions(years)}
      period={state.period.value}
      basis={basis}
      procedural={state.procedural}
    >
      {Array.from({ length: MAX }).map((_, i) => (
        <select key={i} name="p" defaultValue={selected[i] ?? ""} className={SELECT_CLASS} aria-label={`partido ${i + 1}`}>
          <option value="">{i < 2 ? `partido ${i + 1}…` : "— (opcional)"}</option>
          {[...new Set([...options, ...selected])].map((p) => (
            <option key={p} value={p}>{p}</option>
          ))}
        </select>
      ))}
    </PartyFilters>
  );

  if (selected.length < 2) {
    return (
      <section className="flex flex-col gap-4">
        {filters}
        <EmptyState icon="◌" title="escolha pelo menos 2 partidos." hint="ou clique numa célula da matriz em /votacoes/partidos" />
      </section>
    );
  }

  const matrix = getAgreementMatrix(house, { ...scope, basis });
  const pairs: Array<[string, string]> = [];
  for (let i = 0; i < selected.length; i++) {
    for (let j = i + 1; j < selected.length; j++) pairs.push([selected[i], selected[j]]);
  }

  // Same position rule as the matrix (partyPositions in votes-queries.ts), applied per vote.
  const summaries = selected.map((p) => getPartySummary(house, p, scope));
  const byVote = new Map<string, PartyDivergence>();
  summaries.forEach((s, i) => {
    for (const r of s.rows) {
      let v = byVote.get(r.voteId);
      if (!v) {
        v = {
          voteId: r.voteId, date: r.date, description: r.description, kindLabel: r.kindLabel,
          meaning: r.meaning, result: r.result, byParty: new Map(),
        };
        byVote.set(r.voteId, v);
      }
      v.byParty.set(selected[i], r);
    }
  });
  const diverged = [...byVote.values()]
    .filter((v) => {
      const pos = selected.map((p) => v.byParty.get(p)).flatMap((r) => (r ? [partyPosition(r, basis)] : []));
      const known = pos.filter((x) => x != null);
      return known.length >= 2 && new Set(known).size > 1;
    })
    .sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : b.voteId.localeCompare(a.voteId)));
  const totalPages = Math.max(1, Math.ceil(diverged.length / PAGE_SIZE));
  const rows = diverged.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const index = voteIndex(house, scope, rows.map((r) => r.voteId));

  return (
    <>
      <section className="flex flex-col gap-4">
        {filters}
        <HowCalculated
          keys={basis === "orientation" ? ["agreement", house === "camara" ? "camaraBench" : "senadoOrientation"] : ["agreement", "majorityPosition"]}
          extra={`Menos de ${MIN_N} votações comparadas = "poucos dados".`}
        />
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-[15px] font-medium">Concordância ({BASIS_LABEL[basis]}) · {state.period.label}</h2>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>partidos</th>
                <th className="text-right">mesma posição</th>
                <th className="text-right">divergiram</th>
              </tr>
            </thead>
            <tbody>
              {pairs.map(([a, b]) => {
                const c = matrixCell(matrix, a, b);
                const shared = basis === "orientation" && c ? c.sharedBench : 0;
                return (
                  <tr key={`${a}-${b}`}>
                    <td>
                      <Link href={partyHref(a, state)} className="link-primary">{a}</Link> ×{" "}
                      <Link href={partyHref(b, state)} className="link-primary">{b}</Link>
                      {shared ? (
                        <div className="text-[11px]" style={{ color: "var(--muted-2)" }}>
                          {fmtInt(shared)} das votações com a mesma bancada (federação/bloco): mesma
                          orientação por definição
                        </div>
                      ) : null}
                    </td>
                    <td className="num">
                      {c ? <RatioValue r={c} /> : <span style={{ color: "var(--muted-2)" }}>—</span>}
                    </td>
                    <td className="num">{c ? fmtInt(c.n - c.agree) : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-[15px] font-medium">
          Votações em que divergiram <span style={{ color: "var(--muted-2)" }}>({fmtInt(diverged.length)})</span>
        </h2>
        <p className="max-w-3xl text-[12px]" style={{ color: "var(--muted-2)" }}>
          Votações em que pelo menos dois dos partidos escolhidos tiveram posições diferentes (Sim, Não
          ou Obstrução, segundo o critério: {BASIS_LABEL[basis]}). Mais recentes primeiro.
        </p>
        {rows.length === 0 ? (
          <EmptyState icon="◌" title="nenhuma divergência nesse período." compact />
        ) : (
          <div className="table-wrap">
            <div className="overflow-x-auto">
              <table className="table" style={{ minWidth: 640 + 130 * selected.length }}>
                <thead>
                  <tr>
                    <th>votação</th>
                    <th>o que o Sim significava</th>
                    {selected.map((p) => (
                      <th key={p}>{p}</th>
                    ))}
                    <th>resultado</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((v) => (
                    <SourceZone key={v.voteId} provenance={index.get(v.voteId)?.provenance ?? null} as="tr">
                      <td className="max-w-[280px] align-top">
                        <VoteHead
                          href={voteHref(house, v.voteId)}
                          voteId={v.voteId}
                          date={v.date}
                          kindLabel={v.kindLabel}
                          description={v.description}
                          info={index.get(v.voteId)}
                        />
                      </td>
                      <td className="max-w-[200px] align-top text-[12px]" style={{ color: "var(--fg-2)" }}>{v.meaning}</td>
                      {selected.map((p) => (
                        <td key={p} className="align-top text-[12px]">
                          <PartyPositionCell r={v.byParty.get(p)} basis={basis} />
                        </td>
                      ))}
                      <td className="align-top text-[12px]">{v.result.label}</td>
                    </SourceZone>
                  ))}
                </tbody>
              </table>
            </div>
            {totalPages > 1 ? (
              <div className="table-footer">
                <PaginationLinks
                  page={page}
                  totalPages={totalPages}
                  makeHref={(n) => `/votacoes/comparar${stateQuery(state, { modo: undefined, p: selected, page: n > 1 ? String(n) : undefined })}`}
                />
              </div>
            ) : null}
          </div>
        )}
      </section>
    </>
  );
}

function PartyPositionCell({ r, basis }: { r: PartyVoteRow | undefined; basis: AgreementBasis }) {
  if (!r) return <span style={{ color: "var(--muted-2)" }}>sem parlamentar na votação</span>;
  const main = basis === "orientation" ? r.orientation.label : r.majorityLabel;
  const other = basis === "orientation" ? `maioria votou: ${r.majorityLabel}` : `orientação: ${r.orientation.label}`;
  const counted = partyPosition(r, basis) != null;
  return (
    <>
      <div style={{ color: counted ? "var(--fg-1)" : "var(--muted-2)" }}>{main}</div>
      <div style={{ fontSize: 10.5, color: "var(--muted-2)" }}>{other}</div>
    </>
  );
}

// ---------------------------------------------------------------- parlamentar × parlamentar

function parseIds(sp: Record<string, string | string[] | undefined>): number[] {
  return [...new Set(many(sp.id).map(Number).filter((n) => Number.isInteger(n) && n > 0))];
}

function MembersMode({ state, sp, page, years, q }: ModeProps & { q: string }) {
  const { house } = state;
  const scope = scopeOf(state);
  const ids = parseIds(sp).slice(0, MAX);
  const modo = "parlamentares";
  const cmp = compareParliamentarians(house, ids, scope);
  const selected = ids.length > 0 && cmp.members.length === 0
    ? ids.map((id) => getParliamentarian(house, id)).filter((m) => m != null)
    : cmp.members;
  const results = q ? listParliamentarians({ house, q, period: state.period.filter }).slice(0, 15) : [];
  const href = (next: number[], extra: Record<string, string | undefined> = {}) =>
    `/votacoes/comparar${stateQuery(state, { modo, id: next.map(String), ...extra })}`;
  const name = (id: number) => selected.find((m) => m.id === id)?.name ?? `#${id}`;

  const ready = cmp.members.length >= 2;
  const memberIds = cmp.members.map((m) => m.id);
  const diverged = ready
    ? cmp.votes.filter((v) => {
        const known = memberIds.map((id) => v.values[id]?.value).filter((x) => isPositionValue(x));
        return known.length >= 2 && new Set(known).size > 1;
      })
    : [];
  const totalPages = Math.max(1, Math.ceil(diverged.length / PAGE_SIZE));
  const rows = diverged.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const index = voteIndex(house, scope, rows.map((r) => r.voteId));

  return (
    <>
      <section className="flex flex-col gap-4">
        <PartyFilters
          action="/votacoes/comparar"
          hidden={{ casa: house === "senado" ? "senado" : undefined, modo, id: ids.map(String), q: q || undefined }}
          periods={periodOptions(years)}
          period={state.period.value}
          procedural={state.procedural}
        />
        <CompareMemberSearch
          house={house}
          action="/votacoes/comparar"
          hidden={{
            casa: house === "senado" ? "senado" : undefined,
            modo,
            periodo: state.period.value,
            proc: state.procedural ? "1" : undefined,
            id: ids.map(String),
          }}
          q={q}
          results={results}
          selected={selected}
          max={MAX}
          addHref={(id) => href([...ids, id], { q: q || undefined })}
          removeHref={(id) => href(ids.filter((x) => x !== id), { q: q || undefined })}
          memberHref={(id) => memberHref(house, id)}
        />
        <HowCalculated keys={["agreementParliamentarians"]} extra={`Menos de ${MIN_N} votações comparadas = "poucos dados". Ausência, voto secreto e presidência da sessão não entram.`} />
      </section>

      {!ready ? (
        <EmptyState icon="◌" title={`escolha pelo menos 2 ${house === "camara" ? "deputados" : "senadores"} na busca acima.`} />
      ) : (
        <>
          <section className="flex flex-col gap-3">
            <h2 className="text-[15px] font-medium">Concordância · {state.period.label}</h2>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>parlamentares</th>
                    <th className="text-right">votaram igual</th>
                    <th className="text-right">divergiram</th>
                  </tr>
                </thead>
                <tbody>
                  {cmp.pairs.map((p) => (
                    <tr key={`${p.a}-${p.b}`}>
                      <td>
                        <Link href={memberHref(house, p.a)} className="link-primary">{name(p.a)}</Link> ×{" "}
                        <Link href={memberHref(house, p.b)} className="link-primary">{name(p.b)}</Link>
                      </td>
                      <td className="num"><RatioValue r={p} /></td>
                      <td className="num">{fmtInt(p.n - p.agree)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="flex flex-col gap-3">
            <h2 className="text-[15px] font-medium">
              Votações em que divergiram <span style={{ color: "var(--muted-2)" }}>({fmtInt(diverged.length)})</span>
            </h2>
            <p className="max-w-3xl text-[12px]" style={{ color: "var(--muted-2)" }}>
              Votações em que pelo menos dois dos escolhidos votaram diferente (Sim, Não, Obstrução ou
              Abstenção). {PARTY_NAME_NOTE[house]}
            </p>
            {rows.length === 0 ? (
              <EmptyState icon="◌" title="nenhuma divergência nesse período." compact />
            ) : (
              <div className="table-wrap">
                <div className="overflow-x-auto">
                  <table className="table" style={{ minWidth: 600 + 140 * memberIds.length }}>
                    <thead>
                      <tr>
                        <th>votação</th>
                        <th>o que o Sim significava</th>
                        {memberIds.map((id) => (
                          <th key={id}>{name(id)}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((v) => (
                        <SourceZone key={v.voteId} provenance={index.get(v.voteId)?.provenance ?? null} as="tr">
                          <td className="max-w-[280px] align-top">
                            <VoteHead
                              href={voteHref(house, v.voteId)}
                              voteId={v.voteId}
                              date={v.date}
                              kindLabel={v.kindLabel}
                              description={v.description}
                              info={index.get(v.voteId)}
                            />
                          </td>
                          <td className="max-w-[200px] align-top text-[12px]" style={{ color: "var(--fg-2)" }}>{v.meaning}</td>
                          {memberIds.map((id) => {
                            const x = v.values[id];
                            return (
                              <td key={id} className="align-top text-[12px]">
                                {x ? (
                                  <>
                                    <div style={{ color: isPositionValue(x.value) ? "var(--fg-1)" : "var(--muted-2)" }}>{x.label}</div>
                                    <div style={{ fontSize: 10.5, color: "var(--muted-2)" }}>{x.party ?? ""}</div>
                                  </>
                                ) : (
                                  <span style={{ color: "var(--muted-2)" }}>fora da lista de votação</span>
                                )}
                              </td>
                            );
                          })}
                        </SourceZone>
                      ))}
                    </tbody>
                  </table>
                </div>
                {totalPages > 1 ? (
                  <div className="table-footer">
                    <PaginationLinks
                      page={page}
                      totalPages={totalPages}
                      makeHref={(n) => href(ids, { page: n > 1 ? String(n) : undefined })}
                    />
                  </div>
                ) : null}
              </div>
            )}
          </section>
        </>
      )}
    </>
  );
}

// ---------------------------------------------------------------- parlamentar × partido

function MemberPartyMode({ state, sp, page, years, q }: ModeProps & { q: string }) {
  const { house, basis } = state;
  const scope = scopeOf(state);
  const id = parseIds(sp)[0];
  const modo = "parlamentar-partido";
  const partyParam = one(sp.partido);
  const party = partyParam && isRealParty(partyParam) ? partyParam : undefined;
  const options = listParties(house, scope).map((p) => p.party);
  const cmp = id ? compareDeputyWithParty(house, id, { ...scope, party }) : null;
  const results = q ? listParliamentarians({ house, q, period: state.period.filter }).slice(0, 15) : [];
  const href = (next: number | undefined, extra: Record<string, string | undefined> = {}) =>
    `/votacoes/comparar${stateQuery(state, { modo, id: next ? String(next) : undefined, partido: party, ...extra })}`;

  const diverged = cmp
    ? cmp.rows.filter((r) => (basis === "orientation" ? r.followedOrientation === false : r.withMajority === false))
    : [];
  const totalPages = Math.max(1, Math.ceil(diverged.length / PAGE_SIZE));
  const rows = diverged.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const index = voteIndex(house, scope, rows.map((r) => r.voteId));
  const partyName = party ?? "o próprio partido";
  const who = house === "camara" ? "deputado(a)" : "senador(a)";

  return (
    <>
      <section className="flex flex-col gap-4">
        <PartyFilters
          action="/votacoes/comparar"
          hidden={{ casa: house === "senado" ? "senado" : undefined, modo, id: id ? String(id) : undefined, q: q || undefined }}
          periods={periodOptions(years)}
          period={state.period.value}
          basis={basis}
          procedural={state.procedural}
        >
          <select name="partido" defaultValue={party ?? ""} className={SELECT_CLASS} aria-label="partido comparado">
            <option value="">comparar com o próprio partido (o da época de cada votação)</option>
            {[...new Set([...options, ...(party ? [party] : [])])].map((p) => (
              <option key={p} value={p}>{p}</option>
            ))}
          </select>
        </PartyFilters>
        <CompareMemberSearch
          house={house}
          action="/votacoes/comparar"
          hidden={{
            casa: house === "senado" ? "senado" : undefined,
            modo,
            periodo: state.period.value,
            proc: state.procedural ? "1" : undefined,
            base: state.basis !== defaultBasis(house) ? (state.basis === "orientation" ? "orientacao" : "maioria") : undefined,
            partido: party,
            id: id ? String(id) : undefined,
          }}
          q={q}
          results={results}
          selected={cmp ? [cmp.identity] : []}
          max={1}
          addHref={(x) => href(x, { q: q || undefined })}
          removeHref={() => href(undefined, { q: q || undefined })}
          memberHref={(x) => memberHref(house, x)}
        />
        <HowCalculated
          keys={["fidelity", "withPartyMajority", house === "camara" ? "camaraBench" : "senadoOrientation"]}
          extra={`Menos de ${MIN_N} votações = "poucos dados".`}
        />
      </section>

      {!cmp ? (
        <EmptyState icon="◌" title={`escolha um(a) ${who} na busca acima.`} />
      ) : (
        <>
          <section className="flex flex-col gap-3">
            <h2 className="text-[15px] font-medium">
              <SourceZone provenance={cmp.identity.provenance} inline>
                <Link href={memberHref(house, cmp.identity.id)} className="link-primary">{cmp.identity.name ?? `#${cmp.identity.id}`}</Link>
              </SourceZone>{" "}
              × {partyName} · {state.period.label}
            </h2>
            <div className="kpis">
              <KpiRatio
                label={party ? `votou como o partido ${party} orientou` : "seguiu a orientação do próprio partido"}
                r={cmp.fidelity}
                sub="votações"
              />
              <KpiRatio
                label="votou como a maioria dos demais parlamentares do partido"
                r={cmp.withMajority}
                sub="votações"
              />
            </div>
            <p className="max-w-3xl text-[12px] leading-relaxed" style={{ color: "var(--muted-2)" }}>
              {DEFINITIONS.withPartyMajority}
            </p>
            {party ? (
              <Note>
                Comparando com o partido {party} em todas as votações, mesmo nas que o(a) {who} estava em outro
                partido. Para comparar com o partido de cada época, deixe o partido em branco.
              </Note>
            ) : null}
          </section>

          <section className="flex flex-col gap-3">
            <h2 className="text-[15px] font-medium">
              Votações em que divergiu{" "}
              <span style={{ color: "var(--muted-2)" }}>
                ({fmtInt(diverged.length)} de {fmtInt(basis === "orientation" ? cmp.fidelity.n : cmp.withMajority.n)})
              </span>
            </h2>
            <p className="max-w-3xl text-[12px]" style={{ color: "var(--muted-2)" }}>
              {basis === "orientation"
                ? "Votou diferente da orientação Sim, Não ou Obstrução do partido."
                : "Votou diferente da posição majoritária dos demais parlamentares do partido."}{" "}
              Troque a base no filtro acima para ver o outro critério.
            </p>
            {rows.length === 0 ? (
              <EmptyState icon="◌" title="nenhuma divergência nesse período." compact />
            ) : (
              <div className="table-wrap">
                <div className="overflow-x-auto">
                  <table className="table min-w-[900px]">
                    <thead>
                      <tr>
                        <th>votação</th>
                        <th>o que o Sim significava</th>
                        <th>voto</th>
                        <th>partido</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((r) => (
                        <SourceZone key={r.voteId} provenance={index.get(r.voteId)?.provenance ?? null} as="tr">
                          <td className="max-w-[300px] align-top">
                            <VoteHead
                              href={voteHref(house, r.voteId)}
                              voteId={r.voteId}
                              date={r.date}
                              kindLabel={r.kindLabel}
                              description={r.description}
                              info={index.get(r.voteId)}
                            />
                          </td>
                          <td className="max-w-[220px] align-top text-[12px]" style={{ color: "var(--fg-2)" }}>{r.meaning}</td>
                          <td className="align-top text-[12px]" style={{ color: "var(--fg-1)" }}>{r.label}</td>
                          <td className="align-top text-[12px]">
                            <div style={{ color: "var(--muted-2)" }}>{r.party ?? "sem partido"}</div>
                            <div>
                              <span style={{ color: "var(--muted-2)" }}>orientação: </span>
                              {r.orientation.label}
                            </div>
                            <div>
                              <span style={{ color: "var(--muted-2)" }}>maioria dos demais: </span>
                              {r.majorityLabel}
                            </div>
                          </td>
                        </SourceZone>
                      ))}
                    </tbody>
                  </table>
                </div>
                {totalPages > 1 ? (
                  <div className="table-footer">
                    <PaginationLinks
                      page={page}
                      totalPages={totalPages}
                      makeHref={(n) => href(id, { page: n > 1 ? String(n) : undefined })}
                    />
                  </div>
                ) : null}
              </div>
            )}
          </section>
        </>
      )}
    </>
  );
}
