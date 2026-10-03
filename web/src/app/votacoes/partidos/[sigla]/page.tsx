import Link from "next/link";
import {
  getPartySummary,
  getSenadoOrientationCoverage,
  listThemes,
  listYears,
  votesAvailable,
  type PartyVoteRow,
  type VoteListItem,
} from "@/lib/votes-queries";
import {
  GOVERNMENT_PERIODS, IN_HOUSE, OF_HOUSE, PARTY_NAME_NOTE, majorityLabel, type House,
} from "@/lib/vote-meaning";
import { PageHeader } from "@/components/shell/shell-context";
import { EmptyState } from "@/components/ui/empty-state";
import { PaginationLinks } from "@/components/ui/pagination-links";
import { SourceZone } from "@/components/source-zone";
import { PartyFilters } from "@/components/votes/party-filters";
import {
  HouseTabs, HowCalculated, KpiRatio, Kpi, Note, RatioValue, SELECT_CLASS, SenadoCoverageNote,
  VoteHead, fmtInt, governmentName,
} from "@/components/votes/party-ui";
import { defaultBasis, one, parseState, periodOptions, scopeOf, stateQuery, voteHref, voteIndex } from "../_lib/scope";

export const dynamic = "force-dynamic";

const PAGE_SIZE = 50;

type Side = "sim" | "nao";

/** Sim/Não by the party orientation; without a Sim/Não/Obstrução one, by the majority vote. */
function sideOf(r: PartyVoteRow): { side: Side; via: "orientation" | "majority" } | null {
  const o = r.orientation.value;
  if (o === "yes") return { side: "sim", via: "orientation" };
  if (o === "no" || o === "obstruction") return { side: "nao", via: "orientation" };
  if (r.majority === "yes") return { side: "sim", via: "majority" };
  if (r.majority === "no" || r.majority === "obstruction") return { side: "nao", via: "majority" };
  return null;
}

function decode(s: string): string {
  try {
    return decodeURIComponent(s);
  } catch {
    return s;
  }
}

export default async function PartidoPage({ params, searchParams }: PageProps<"/votacoes/partidos/[sigla]">) {
  const [{ sigla }, sp] = await Promise.all([params, searchParams]);
  const party = decode(sigla);
  const casa = one(sp.casa) === "senado" ? "senado" : "camara";
  const years = listYears(casa);
  const state = parseState(sp, years);
  const { house } = state;
  const themes = house === "camara" ? listThemes(state.procedural) : [];
  const themeParam = Number(one(sp.tema));
  const theme = themes.find((t) => t.code === themeParam);
  const ladoParam = one(sp.lado);
  const lado: Side | "todos" = ladoParam === "sim" || ladoParam === "nao" ? ladoParam : "todos";
  const page = Math.max(1, Number(one(sp.page)) || 1);
  const scope = scopeOf(state, theme?.code);

  const summary = getPartySummary(house, party, scope);
  const sides = summary.rows.map((r) => ({ r, s: sideOf(r) }));
  const counts = {
    todos: sides.length,
    sim: sides.filter((x) => x.s?.side === "sim").length,
    nao: sides.filter((x) => x.s?.side === "nao").length,
  };
  const filtered = lado === "todos" ? sides : sides.filter((x) => x.s?.side === lado);
  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const pageRows = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const index = voteIndex(house, scope, pageRows.map((x) => x.r.voteId));
  const coverage = house === "senado" ? getSenadoOrientationCoverage() : [];

  const base = `/votacoes/partidos/${encodeURIComponent(party)}`;
  const extra = (o: { lado?: string; page?: number } = {}) => ({
    tema: theme ? String(theme.code) : undefined,
    lado: (o.lado ?? lado) === "todos" ? undefined : (o.lado ?? lado),
    page: o.page && o.page > 1 ? String(o.page) : undefined,
  });
  const houseHref = (h: House) => `${base}${stateQuery({ ...state, house: h, basis: defaultBasis(h) })}`;
  const sideHref = (l: string) => `${base}${stateQuery(state, extra({ lado: l }))}`;

  return (
    <div className="flex flex-col gap-8">
      <PageHeader group="Votações · Partidos" current={party} />

      <section>
        <div className="mono" style={{ fontSize: 11, color: "var(--muted-2)" }}>
          <Link href={`/votacoes/partidos${stateQuery(state)}`} className="hover:underline">
            ← todos os partidos
          </Link>
        </div>
        <h1 className="mt-2 text-[26px] leading-tight font-medium tracking-tight">
          {party} — como o partido votou {IN_HOUSE[house]}
        </h1>
        <p className="mt-4 max-w-2xl text-[13px] leading-relaxed" style={{ color: "var(--muted)" }}>
          Votações nominais do Plenário em que o partido {party} teve ao menos um parlamentar na lista.
          Para cada uma: o que o partido <strong style={{ color: "var(--fg-2)" }}>mandou votar</strong>{" "}
          (orientação), o que a <strong style={{ color: "var(--fg-2)" }}>maioria da bancada votou</strong>{" "}
          de fato e quantos seguiram a orientação. {PARTY_NAME_NOTE[house]}{" "}
          {house === "camara" ? "Votos sob outro nome não entram." : ""}
        </p>
        <HowCalculated keys={["fidelity", "majorityPosition", "government", house === "camara" ? "camaraBench" : "senadoOrientation", "meaning"]} />

        <div className="mt-5 flex flex-col gap-3">
          <HouseTabs house={house} hrefFor={houseHref} />
          <PartyFilters
            action={base}
            hidden={{ casa: house === "senado" ? "senado" : undefined, lado: lado === "todos" ? undefined : lado }}
            periods={periodOptions(years)}
            period={state.period.value}
            procedural={state.procedural}
          >
            {house === "camara" && themes.length > 0 ? (
              <select name="tema" defaultValue={theme?.code ?? ""} className={SELECT_CLASS} aria-label="tema">
                <option value="">todos os temas</option>
                {themes.map((t) => (
                  <option key={t.code} value={t.code}>{t.name}</option>
                ))}
              </select>
            ) : null}
          </PartyFilters>
          {house === "senado" ? (
            <p className="text-[12px]" style={{ color: "var(--muted-2)" }}>
              O Senado não publica temas por proposição; o filtro de tema existe só para a Câmara.
            </p>
          ) : null}
        </div>
      </section>

      {house === "senado" ? <SenadoCoverageNote coverage={coverage} /> : null}

      {!votesAvailable(house) ? (
        <EmptyState icon="◌" title={`nenhuma votação ${OF_HOUSE[house]} coletada ainda`} />
      ) : summary.votes === 0 ? (
        <EmptyState
          icon="◌"
          title={`nenhuma votação do partido ${party} ${IN_HOUSE[house]} nesse período${theme ? " e tema" : ""}.`}
          hint={
            <>
              {house === "camara"
                ? "A sigla precisa ser a da época (ex.: PATRIOTA, PSC). "
                : "No Senado, use a sigla atual do partido (ex.: REPUBLICANOS, não PRB). "}
              <Link href={`/votacoes/partidos${stateQuery(state)}`} className="link-primary">
                ver partidos com votos
              </Link>
            </>
          }
        />
      ) : (
        <>
          <section className="flex flex-col gap-3">
            <div className="kpis">
              <Kpi
                label="votações"
                value={fmtInt(summary.votes)}
                sub={`${fmtInt(summary.votesOriented)} com orientação Sim, Não ou Obstrução do partido`}
              />
              <KpiRatio label="fidelidade (seguiu a orientação)" r={summary.fidelity} sub="votos de parlamentares" />
            </div>
            {/* One figure per government: a blended "% com o Governo" is never shown. */}
            {(
              [
                ["orientação do partido igual à do", summary.withGovernmentByOrientation],
                ["maioria da bancada votou como o", summary.withGovernmentByMajority],
              ] as const
            ).map(([what, ratios]) => (
              <div key={what} className="kpis">
                {GOVERNMENT_PERIODS.map((g) => (
                  <KpiRatio key={g.id} label={`${what} ${governmentName(g)}`} r={ratios[g.id]} sub="votações" />
                ))}
              </div>
            ))}
            <p className="max-w-3xl text-[12px] leading-relaxed" style={{ color: "var(--muted-2)" }}>
              Fidelidade = soma dos votos dos parlamentares iguais à orientação ÷ soma dos votos dados, em
              todas as votações que contam (não é a média simples das votações). Regra completa em
              &quot;como calculamos&quot;.
            </p>
          </section>

          <section className="flex flex-col gap-3">
            <div className="flex flex-wrap items-center gap-2" role="group" aria-label="lado">
              {(
                [
                  ["todos", `todas (${fmtInt(counts.todos)})`],
                  ["sim", `o partido apoiou: Sim (${fmtInt(counts.sim)})`],
                  ["nao", `o partido rejeitou: Não/Obstrução (${fmtInt(counts.nao)})`],
                ] as const
              ).map(([v, label]) => (
                <Link key={v} href={sideHref(v)} className={`btn${lado === v ? " btn--primary" : ""}`}>
                  {label}
                </Link>
              ))}
            </div>
            <Note>
              &quot;Apoiou&quot; e &quot;rejeitou&quot; seguem a orientação do partido; quando ele não orientou
              Sim, Não ou Obstrução (liberou, não orientou ou sem orientação identificada), vale o voto da
              maioria da bancada, marcado como <em>pela maioria</em>. Apoiar o Sim não é sempre apoiar a
              proposta: leia &quot;o que o Sim significava&quot; em cada linha.
            </Note>

            {pageRows.length === 0 ? (
              <EmptyState icon="◌" title="nenhuma votação nesse grupo." compact />
            ) : (
              <div className="table-wrap">
                <div className="overflow-x-auto">
                  <table className="table min-w-[960px]">
                    <thead>
                      <tr>
                        <th>votação</th>
                        <th>o que o Sim significava</th>
                        <th>{party}: orientação × votos</th>
                        <th>Governo</th>
                        <th>resultado</th>
                      </tr>
                    </thead>
                    <tbody>
                      {pageRows.map(({ r, s }) => (
                        <PartyVoteTr key={r.voteId} house={house} r={r} side={s} info={index.get(r.voteId)} />
                      ))}
                    </tbody>
                  </table>
                </div>
                {totalPages > 1 ? (
                  <div className="table-footer">
                    <PaginationLinks
                      page={page}
                      totalPages={totalPages}
                      makeHref={(p) => `${base}${stateQuery(state, extra({ page: p }))}`}
                    />
                  </div>
                ) : null}
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}

function positionsText(r: PartyVoteRow): string {
  const p = r.positions;
  const parts = [
    p.yes ? `Sim ${p.yes}` : "",
    p.no ? `Não ${p.no}` : "",
    p.obstruction ? `Obstrução ${p.obstruction}` : "",
    p.abstention ? `Abstenção ${p.abstention}` : "",
    r.notVoted ? `não registrou voto ${r.notVoted}` : "",
  ].filter(Boolean);
  if (parts.length) return parts.join(" · ");
  return r.majority === null && r.majorityLabel !== majorityLabel(null)
    ? "votos não divulgados"
    : "ninguém votou Sim, Não, Obstrução ou Abstenção";
}

function PartyVoteTr({
  house, r, side, info,
}: {
  house: House;
  r: PartyVoteRow;
  side: ReturnType<typeof sideOf>;
  info: VoteListItem | undefined;
}) {
  const o = r.orientation;
  const viaBench = house === "camara" && o.benchLabel && o.matchBasis && o.matchBasis !== "party";
  return (
    <SourceZone provenance={info?.provenance ?? null} as="tr">
      <td className="max-w-[300px] align-top">
        <VoteHead
          href={voteHref(house, r.voteId)}
          voteId={r.voteId}
          date={r.date}
          kindLabel={r.kindLabel}
          description={r.description}
          info={info}
        />
      </td>
      <td className="max-w-[220px] align-top text-[12px]" style={{ color: "var(--fg-2)" }}>{r.meaning}</td>
      <td className="align-top text-[12px]">
        <div>
          <span style={{ color: "var(--muted-2)" }}>orientação: </span>
          <span style={{ color: "var(--fg-1)" }}>{o.label}</span>
          {viaBench ? <span style={{ color: "var(--muted-2)" }}> (via {o.benchLabel})</span> : null}
        </div>
        <div>
          <span style={{ color: "var(--muted-2)" }}>maioria votou: </span>
          <span style={{ color: "var(--fg-1)" }}>{r.majorityLabel}</span>
        </div>
        {r.majorityCounted ? null : (
          <div style={{ fontSize: 10.5, color: "var(--muted-2)" }}>
            a liderança orientou obstrução: a maioria dos poucos que votaram fica fora das comparações
          </div>
        )}
        <div className="mono" style={{ fontSize: 10.5, color: "var(--muted-2)" }}>{positionsText(r)}</div>
        <div className="mt-1 flex items-center gap-1.5">
          <span style={{ color: "var(--muted-2)" }}>seguiu a orientação:</span>
          {r.followed ? <RatioValue r={r.followed} align="left" /> : <span style={{ color: "var(--muted-2)" }}>não se aplica</span>}
        </div>
        {side ? (
          <span className={`badge mt-1 ${side.side === "sim" ? "badge--green" : "badge--red"}`}>
            {side.side === "sim" ? "apoiou (Sim)" : "rejeitou (Não/Obstr.)"}
            {side.via === "majority" ? " · pela maioria" : ""}
          </span>
        ) : null}
      </td>
      <td className="align-top text-[12px]">
        <div style={{ color: "var(--fg-1)" }}>{r.government.label}</div>
        <div style={{ fontSize: 10.5, color: "var(--muted-2)" }}>{r.government.government?.label ?? "Governo"}</div>
      </td>
      <td className="align-top text-[12px]">
        <div style={{ color: "var(--fg-1)" }}>{r.result.label}</div>
        {r.result.note ? <div style={{ fontSize: 10.5, color: "var(--muted-2)" }}>{r.result.note}</div> : null}
      </td>
    </SourceZone>
  );
}
