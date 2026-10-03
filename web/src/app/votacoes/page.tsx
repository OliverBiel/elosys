import Link from "next/link";
import { listThemes, listVotes, listYears, votesAvailable, VOTES_PAGE_SIZE } from "@/lib/votes-queries";
import { HOUSE_LABEL, OF_HOUSE, type House } from "@/lib/vote-meaning";
import { PageHeader } from "@/components/shell/shell-context";
import { PaginationLinks } from "@/components/ui/pagination-links";
import { EmptyState } from "@/components/ui/empty-state";
import { SourceZone } from "@/components/source-zone";
import { VoteFilters, type VoteFilterState } from "@/components/votes/vote-filters";
import {
  VoteResultBadge, VoteScore, formatVoteDate, truncate,
} from "@/components/votes/vote-result";

export const dynamic = "force-dynamic";

const one = (v: string | string[] | undefined) => (typeof v === "string" ? v : undefined);

export default async function VotacoesPage({ searchParams }: PageProps<"/votacoes">) {
  const sp = await searchParams;
  const house: House = one(sp.casa) === "senado" ? "senado" : "camara";
  const years = listYears(house);
  const procedural = one(sp.proc) === "1";
  const themes = house === "camara" ? listThemes(procedural) : [];
  const yearParam = Number(one(sp.ano));
  const themeParam = Number(one(sp.tema));
  const state: VoteFilterState = {
    house,
    year: years.includes(yearParam) ? yearParam : undefined,
    theme: themes.some((t) => t.code === themeParam) ? themeParam : undefined,
    q: one(sp.q)?.trim() ?? "",
    procedural,
  };
  const page = Math.max(1, Number(one(sp.page)) || 1);

  const available = votesAvailable(house);
  const { rows, total } = listVotes({
    house,
    year: state.year,
    theme: state.theme,
    q: state.q,
    includeProcedural: state.procedural,
    page,
  });
  const totalPages = Math.max(1, Math.ceil(total / VOTES_PAGE_SIZE));

  const hrefFor = (s: VoteFilterState, p = 1) => {
    const usp = new URLSearchParams();
    if (s.house !== "camara") usp.set("casa", s.house);
    if (s.year) usp.set("ano", String(s.year));
    if (s.theme && s.house === "camara") usp.set("tema", String(s.theme));
    if (s.q) usp.set("q", s.q);
    if (s.procedural) usp.set("proc", "1");
    if (p > 1) usp.set("page", String(p));
    const qs = usp.toString();
    return `/votacoes${qs ? `?${qs}` : ""}`;
  };
  const filtered = state.year != null || state.theme != null || state.q !== "";

  return (
    <div className="flex flex-col gap-8">
      <PageHeader group="Votações" current="Votações nominais" />

      <section>
        <h1 className="text-[26px] leading-tight font-medium tracking-tight">
          Votações nominais no Plenário — como cada parlamentar votou
        </h1>
        <div className="mt-4 flex max-w-2xl flex-col gap-2 text-[13px] leading-relaxed" style={{ color: "var(--muted)" }}>
          <p>
            Uma votação <strong style={{ color: "var(--fg-2)" }}>nominal</strong> é aquela em que o
            painel registra o voto de cada deputado ou senador pelo nome. A maior parte das votações
            da Câmara é <strong style={{ color: "var(--fg-2)" }}>simbólica</strong> (sem registro
            individual) e não aparece aqui.
          </p>
          <p>
            Em votação <strong style={{ color: "var(--fg-2)" }}>secreta</strong>, a fonte informa
            quem votou e o placar total, mas não como cada um votou. Cada linha leva à página da
            votação, com o que o Sim significava e a descrição oficial.
          </p>
          <p>
            Por padrão mostramos só votações sobre o conteúdo (texto, PEC, emendas, destaques…). Os
            requerimentos de andamento (adiar, retirar de pauta, urgência) ficam ocultos; marque
            &quot;mostrar procedimentais&quot; para vê-los.
          </p>
        </div>

        <div className="mono mt-6" style={{ fontSize: 11, color: "var(--muted)" }}>
          <span style={{ color: "var(--fg-1)" }}>{total.toLocaleString("pt-BR")}</span> votações nominais
          {" · "}
          {HOUSE_LABEL[house]}
          {state.procedural ? " · incluindo procedimentais" : " · sem procedimentais"}
        </div>

        <VoteFilters
          state={state}
          years={years}
          themes={themes}
          houseHref={(h) => hrefFor({ house: h, q: state.q, procedural: state.procedural })}
        />
      </section>

      <section>
        {!available ? (
          <EmptyState
            icon="◌"
            title={`votações ${OF_HOUSE[house]} ainda não coletadas`}
            hint={
              <code>
                elosys {house === "camara" ? "camara-votes" : "senado-votes"} --db elosys.db
              </code>
            }
          />
        ) : rows.length === 0 ? (
          <EmptyState
            icon="◌"
            title={filtered ? "nenhuma votação para esse filtro." : "nenhuma votação encontrada."}
            hint={
              !state.procedural ? "Tente marcar \"mostrar procedimentais\" ou limpar os filtros." : undefined
            }
          />
        ) : (
          <div className="table-wrap">
            <div className="overflow-x-auto">
              <table className="table min-w-[860px]">
                <thead>
                  <tr>
                    <th>data</th>
                    <th>proposição</th>
                    <th>o que foi votado</th>
                    <th>resultado</th>
                    <th className="text-right">Sim × Não</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => {
                    const href = `/votacoes/${house}/${encodeURIComponent(r.id)}`;
                    return (
                      <SourceZone key={r.id} provenance={r.provenance} as="tr">
                        <td className="mono whitespace-nowrap align-top" style={{ fontSize: 12 }}>
                          {formatVoteDate(r.date)}
                        </td>
                        <td className="max-w-[300px] align-top">
                          <Link href={href} className="font-medium hover:underline">
                            {r.propositions.length > 0 ? r.propositions.join(", ") : "sem proposição associada"}
                          </Link>
                          {r.ementa ? (
                            <div className="mt-1 text-[12px] leading-snug" style={{ color: "var(--muted)" }} title={r.ementa}>
                              {truncate(r.ementa, 140)}
                            </div>
                          ) : null}
                        </td>
                        <td className="max-w-[320px] align-top">
                          <span className="badge">{r.kindLabel}</span>
                          {r.description ? (
                            <div
                              className="mt-1 text-[12px] leading-snug"
                              style={{ color: "var(--muted-2)" }}
                              title={r.description}
                            >
                              {truncate(r.description, 160)}
                            </div>
                          ) : null}
                        </td>
                        <td className="align-top">
                          <VoteResultBadge result={r.result} />
                        </td>
                        <td className="num align-top">
                          <Link href={href} className="hover:underline" aria-label="ver votação">
                            <VoteScore yes={r.yes} no={r.no} isSecret={r.isSecret} />
                          </Link>
                        </td>
                      </SourceZone>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {totalPages > 1 ? (
              <div className="table-footer">
                <PaginationLinks page={page} totalPages={totalPages} makeHref={(p) => hrefFor(state, p)} />
              </div>
            ) : null}
          </div>
        )}
      </section>
    </div>
  );
}
