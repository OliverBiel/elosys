import Link from "next/link";
import {
  getAgreementMatrix,
  getPartyVsGovernment,
  getScopeSources,
  getSenadoOrientationCoverage,
  listYears,
  votesAvailable,
  type PartyGovernmentRow,
} from "@/lib/votes-queries";
import { OF_HOUSE, PARTY_NAME_NOTE, type House } from "@/lib/vote-meaning";
import { PageHeader } from "@/components/shell/shell-context";
import { EmptyState } from "@/components/ui/empty-state";
import { PartyMatrix, SHARED_BENCH_MARK, type GovernmentColumn } from "@/components/votes/party-matrix";
import { PartyFilters } from "@/components/votes/party-filters";
import { SourcesList } from "@/components/votes/sources-list";
import {
  HouseTabs, HowCalculated, MIN_N, Note, SenadoCoverageNote, fmtInt, governmentName,
} from "@/components/votes/party-ui";
import {
  BASIS_LABEL, defaultBasis, parseState, partyHref, periodOptions, scopeOf, stateQuery,
} from "./_lib/scope";

export const dynamic = "force-dynamic";

export default async function PartidosPage({ searchParams }: PageProps<"/votacoes/partidos">) {
  const sp = await searchParams;
  const casa = sp.casa === "senado" ? "senado" : "camara";
  const years = listYears(casa);
  const state = parseState(sp, years);
  const { house, basis } = state;
  const scope = scopeOf(state);
  const available = votesAvailable(house);

  const matrix = getAgreementMatrix(house, { ...scope, basis });
  const gov = getPartyVsGovernment(house, scope);
  const pick = (r: PartyGovernmentRow) => (basis === "orientation" ? r.byOrientation : r.byMajority);
  // One column per government in scope: a blended "% com o Governo" is never shown.
  const governments: GovernmentColumn[] = gov.governments.map((g) => ({
    label: governmentName(g),
    byParty: new Map(gov.rows.map((r) => [r.party, pick(r)[g.id]])),
  }));
  const anyShared =
    basis === "orientation" &&
    matrix.cells.some((c) => c.sharedBench > 0 && c.n >= MIN_N && c.sharedBench >= SHARED_BENCH_MARK * c.n);
  const coverage = house === "senado" ? getSenadoOrientationCoverage() : [];
  const sources = available ? getScopeSources(house, scope) : [];

  const houseHref = (h: House) =>
    `/votacoes/partidos${stateQuery({ ...state, house: h, basis: defaultBasis(h) })}`;
  const pairHref = (a: string, b: string) =>
    `/votacoes/comparar${stateQuery(state, { modo: "partidos", p: [a, b] })}`;

  return (
    <div className="flex flex-col gap-8">
      <PageHeader group="Votações" current="Partidos" />

      <section>
        <h1 className="text-[26px] leading-tight font-medium tracking-tight">
          Quais partidos votam juntos — e quanto cada um acompanha o Governo
        </h1>
        <p className="mt-4 max-w-2xl text-[13px] leading-relaxed" style={{ color: "var(--muted)" }}>
          Cada célula mostra em quantas votações nominais do Plenário dois partidos tiveram{" "}
          <strong style={{ color: "var(--fg-2)" }}>a mesma posição</strong>. A posição do partido pode
          ser a <strong style={{ color: "var(--fg-2)" }}>orientação oficial</strong> (o que a liderança
          mandou votar) ou o <strong style={{ color: "var(--fg-2)" }}>voto da maioria da bancada</strong>{" "}
          (o que os parlamentares de fato votaram). {PARTY_NAME_NOTE[house]}
        </p>
        <HowCalculated
          keys={basis === "orientation" ? ["agreement", "government", "camaraBench"] : ["agreement", "majorityPosition", "government"]}
          extra={`Células com menos de ${MIN_N} votações comparadas aparecem em cinza ("poucos dados"): o % é instável demais para comparar.`}
        />

        <div className="mt-5 flex flex-col gap-3">
          <HouseTabs house={house} hrefFor={houseHref} />
          <PartyFilters
            action="/votacoes/partidos"
            hidden={{ casa: house === "senado" ? "senado" : undefined }}
            periods={periodOptions(years)}
            period={state.period.value}
            basis={basis}
            procedural={state.procedural}
          />
        </div>
      </section>

      {house === "senado" ? <SenadoCoverageNote coverage={coverage} /> : null}

      <section className="flex flex-col gap-3">
        {!available ? (
          <EmptyState
            icon="◌"
            title={`nenhuma votação ${OF_HOUSE[house]} coletada ainda`}
            hint={<code>{house === "camara" ? "elosys camara-votes --db elosys.db" : "elosys senado-votes --db elosys.db"}</code>}
          />
        ) : matrix.parties.length === 0 ? (
          <EmptyState icon="◌" title="nenhuma votação com posição de partido nesse período." />
        ) : (
          <>
            <div className="mono flex flex-wrap gap-x-4 gap-y-1" style={{ fontSize: 11, color: "var(--muted)" }}>
              <span>
                <span style={{ color: "var(--fg-1)" }}>{fmtInt(matrix.votes)}</span> votações ·{" "}
                {state.period.label}
                {state.procedural ? " · com procedimentais" : " · sem procedimentais"}
              </span>
              <span>
                posição = <span style={{ color: "var(--fg-1)" }}>{BASIS_LABEL[basis]}</span>
              </span>
              <span>
                {gov.governments.map((g) => g.label).join(" e ") || "Governo"} orientou Sim, Não ou Obstrução em{" "}
                <span style={{ color: "var(--fg-1)" }}>{fmtInt(gov.votesWithGovernmentOrientation)}</span>
              </span>
            </div>
            {governments.length > 1 ? (
              <Note>
                O período cobre dois governos. A concordância com o Governo aparece em uma coluna por
                governo, nunca misturada: cada votação é comparada com a orientação do governo da data.
              </Note>
            ) : null}
            <PartyMatrix
              matrix={matrix}
              governments={governments}
              rowHref={(p) => partyHref(p, state)}
              pairHref={pairHref}
            />
            <p className="max-w-3xl text-[12px] leading-relaxed" style={{ color: "var(--muted-2)" }}>
              % = votações com a mesma posição ÷ N; N = votações em que os dois tiveram posição Sim, Não
              ou Obstrução. Cor mais forte = mais concordância. Clique num partido para ver como ele votou,
              ou numa célula para ver as votações em que os dois{" "}
              <Link href={`/votacoes/comparar${stateQuery(state, { modo: "partidos" })}`} className="link-primary">
                divergiram
              </Link>
              .
            </p>
            {anyShared ? (
              <p className="max-w-3xl text-[12px] leading-relaxed" style={{ color: "var(--muted-2)" }}>
                ‡ com contorno tracejado = em pelo menos {Math.round(SHARED_BENCH_MARK * 100)}% das votações
                comparadas os dois partidos estavam na mesma bancada (federação ou bloco) e receberam a
                mesma orientação por definição; a concordância ali não mostra afinidade própria. Passe o
                mouse na célula para ver quantas, ou troque para &quot;voto da maioria da bancada&quot;.
              </p>
            ) : null}
            <SourcesList
              sources={sources}
              label="fontes da matriz (votos, orientações e bancadas das votações do período)"
            />
          </>
        )}
      </section>
    </div>
  );
}
