import { Suspense } from "react";
import Link from "next/link";
import { notFound } from "next/navigation";
import {
  getPersonHeader, getPersonFinance, getPersonCandidacies, getPersonCampaignOrgs,
  getPersonSocialMedia, getPersonAssets, getPersonSignals, getCycleEdgeAmounts,
  getPoliticianDonationNetwork, getDiscourseSignals, getDiscourseCount, getExpenseYears,
  getPersonPhotoUrl, getPersonPhotoProvenance, getPersonEarmarks,
} from "@/lib/queries";
import { SourceZone } from "@/components/source-zone";
import { PoliticianNetwork } from "@/components/politician-network";
import { ProfilePhoto } from "@/components/profile-photo";
import { CycleGraph } from "@/components/graph/cycle-graph";
import { AssetsCurveChart } from "@/components/assets-curve-chart-lazy";
import { AssetsYearCards } from "@/components/assets-year-cards";
import { SocialCard } from "@/components/social-card";
import { TweetCard } from "@/components/tweet-card";
import { FinanceTable } from "@/components/finance-table";
import { Skeleton } from "@/components/skeleton";
import { PageHeader } from "@/components/shell/shell-context";
import { YearSelect } from "@/components/ui/year-select";
import { EmptyState } from "@/components/ui/empty-state";
import { formatBRL, formatCnpj, formatCpf, resultTone } from "@/lib/format";
import { getPersonVotesSummary } from "@/lib/votes-queries";
import { HOUSE_LABEL } from "@/lib/vote-meaning";
import { ParliamentarianKpis } from "@/components/votes/parliamentarian-kpis";
import { ParliamentarianVotesTable } from "@/components/votes/parliamentarian-votes-table";
import { parliamentarianHref } from "@/components/votes/parliamentarian-shared";
import { SourcesList } from "@/components/votes/sources-list";

const DISCOURSE_SHOWN = 8;

export const dynamic = "force-dynamic";

export default async function PoliticoPage({ params, searchParams }: PageProps<"/politico/[id]">) {
  const { id } = await params;
  const personId = Number(id);
  if (!Number.isInteger(personId)) notFound();

  const sp = await searchParams;
  const anoParam = typeof sp.ano === "string" ? Number(sp.ano) : NaN;
  const financeYears = getExpenseYears();
  const year = Number.isInteger(anoParam) && financeYears.includes(anoParam) ? anoParam : undefined;

  const header = getPersonHeader(personId);
  if (!header) notFound();
  const { person, latestCandidacy } = header;
  const overviewFinance = getPersonFinance(personId, year);
  const displayName = person.canonicalName ?? "(nome indisponível)";
  const photoUrl = getPersonPhotoUrl(personId);
  const photoProvenance = photoUrl ? getPersonPhotoProvenance(personId) : null;

  return (
      <main className="mx-auto w-full max-w-4xl pt-8">
        <PageHeader
          group="Consulta"
          current={displayName}
          actions={<YearSelect basePath={`/politico/${person.id}`} years={financeYears} value={year} />}
        />

        <header className="animate-in pb-8">
          <div className="flex items-center gap-4">
            {photoUrl ? (
              <SourceZone provenance={photoProvenance} inline>
                <ProfilePhoto url={photoUrl} size={72} />
              </SourceZone>
            ) : null}
            <SourceZone provenance={header.provenance}>
              <h1 className="text-[34px] leading-tight font-light tracking-tight text-balance sm:text-[42px]">
                {displayName}
              </h1>
            </SourceZone>
          </div>
          {latestCandidacy ? (
            <SourceZone provenance={header.provenance}>
              <div className="mt-2 text-[14px] text-[var(--muted)]">
                {latestCandidacy.office ?? "cargo n/d"} · {latestCandidacy.partyAbbr ?? "s/partido"}/
                {latestCandidacy.state ?? "—"} · {latestCandidacy.year}
              </div>
            </SourceZone>
          ) : null}

          <div className="mt-5 flex flex-wrap gap-x-8 gap-y-3 font-mono text-[11px] text-[var(--muted)]">
            <SourceZone provenance={header.provenance} inline>
              <span>
                <span className="text-[var(--muted-2)]">CPF </span>
                {formatCpf(person.cpf)}
                {person.cpf && !person.cpfTrusted ? (
                  <span className="ml-1.5 text-elo-amber">reconciliado</span>
                ) : null}
              </span>
            </SourceZone>
            <SourceZone provenance={header.provenance} inline>
              <span>
                <span className="text-[var(--muted-2)]">título eleitoral </span>
                {person.voterId ?? "não disponível"}
              </span>
            </SourceZone>
          </div>

          {overviewFinance.donationsCount > 0 || overviewFinance.expensesCount > 0 ? (
            <div className="kpis mt-6">
              <div className="kpi">
                <div className="kpi__label">recebido em doações {year ? `em ${year}` : "(todas as eleições)"}</div>
                <div className="kpi__value kpi__value--green">{formatBRL(overviewFinance.donationsTotalCents)}</div>
                <div className="kpi__sub">{overviewFinance.donationsCount.toLocaleString("pt-BR")} doações</div>
              </div>
              <div className="kpi">
                <div className="kpi__label">despesas contratadas</div>
                <div className="kpi__value">{formatBRL(overviewFinance.expensesTotalCents)}</div>
                <div className="kpi__sub">{overviewFinance.expensesCount.toLocaleString("pt-BR")} despesas</div>
              </div>
              <div className="kpi">
                <div className="kpi__label">pago até agora</div>
                <div className="kpi__value">{formatBRL(overviewFinance.paymentsTotalCents)}</div>
                <div className="kpi__sub">regime de caixa</div>
              </div>
            </div>
          ) : null}
        </header>
  
        <Suspense fallback={<SectionSkeleton id="candidaturas" title="candidaturas por eleição" rows={3} />}>
          <CandidaciesSection personId={person.id} />
        </Suspense>
  
        <Suspense fallback={<SectionSkeleton id="bens-declarados" title="bens declarados" rows={2} />}>
          <AssetsSection personId={person.id} />
        </Suspense>

        <Suspense fallback={<SectionSkeleton id="emendas-parlamentares" title="emendas parlamentares" rows={2} />}>
          <EarmarksSection personId={person.id} />
        </Suspense>

        <Suspense fallback={<SectionSkeleton id="como-votou" title="como votou no plenário" rows={3} />}>
          <VotesSection personId={person.id} />
        </Suspense>

        <Suspense fallback={<NetworkSkeleton />}>
          <NetworkSection personId={person.id} displayName={displayName} />
        </Suspense>
  
        <Suspense fallback={<SignalsSkeleton />}>
          <SignalsSection personId={person.id} personCpf={person.cpf} />
        </Suspense>
  
        {financeYears.length > 0 ? (
          <Suspense fallback={<FinanceSkeleton />}>
            <FinanceSection personId={person.id} year={year} />
          </Suspense>
        ) : null}
  
        <Suspense fallback={<SectionSkeleton id="redes-sociais" title="redes sociais declaradas" rows={2} />}>
          <SocialMediaSection personId={person.id} />
        </Suspense>
  
        <Suspense fallback={<SectionSkeleton id="discurso" title="posts no X sinalizados pela IA" rows={3} />}>
          <DiscourseSection personId={person.id} displayName={displayName} />
        </Suspense>
      </main>
  );
}


async function NetworkSection({ personId, displayName }: { personId: number; displayName: string }) {
  const network = getPoliticianDonationNetwork(personId);
  if (network.donatedTo.length === 0 && network.receivedFrom.length === 0) return null;
  return (
    <Section id="rede-de-doacao" title="rede de doação">
      <PoliticianNetwork network={network} centerLabel={displayName} centerPhotoUrl={getPersonPhotoUrl(personId)} />
    </Section>
  );
}

async function SignalsSection({ personId, personCpf }: { personId: number; personCpf: string | null }) {
  const { signals, signalsCount } = getPersonSignals(personId);
  if (signals.length === 0) return null;
  return (
    <section id="sinais" data-toc-title="sinais de alerta" className="animate-in py-7">
      <div className="mb-2 flex items-baseline gap-3">
        <div className="section-title !text-elo-red">sinais de alerta</div>
        <span className="font-mono text-[9px] text-[var(--muted-2)]">
          {signalsCount} {signalsCount === 1 ? "sinal" : "sinais"}
          {signalsCount > signals.length ? ` · mostrando os ${signals.length} maiores` : ""}
        </span>
      </div>
      <p className="mb-4 text-[11.5px] text-[var(--muted-2)]">
        Gerado por regras sobre dados já coletados — indício, não prova.
      </p>
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2" style={{ alignItems: "start" }}>
        {signals.map((s) => {
          const isHigh = s.severity === "high";
          const roleLabel = s.role === "candidate" ? "como candidato" : s.role === "cycle_member" ? "no ciclo" : "como fornecedor";
          const graphHref = s.graphIds && s.graphIds.length > 0
            ? `/grafo?add=${encodeURIComponent(s.graphIds.join(","))}`
            : null;

          if (s.cycleNodes && s.cycleNodes.length > 0) {
            return (
              <CycleGraph
                key={s.id}
                nodes={s.cycleNodes}
                selfCpfCnpj={personCpf}
                edgeAmounts={getCycleEdgeAmounts(s.cycleNodes)}
                severity={s.severity}
                roleLabel={roleLabel}
                aiReview={s.aiReview}
                amountCents={s.cycleAmountCents}
                pathLength={s.cyclePathLength}
                graphHref={graphHref}
              />
            );
          }

          return (
            <article key={s.id} className={`signal signal--${isHigh ? "high" : "medium"}`}>
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                {isHigh ? <span className="badge badge--red">{s.severity}</span> : null}
                <span className="mono" style={{ fontSize: 10, color: "var(--muted-2)" }}>
                  {roleLabel} · {s.rule}
                </span>
                {s.aiReview ? (
                  <span
                    className={`badge ${
                      s.aiReview.verdict === "bizarro"
                        ? "badge--red"
                        : s.aiReview.verdict === "inconclusivo"
                          ? "badge--accent"
                          : ""
                    }`}
                  >
                    IA: {s.aiReview.verdict === "plausivel" ? "plausível" : s.aiReview.verdict}
                  </span>
                ) : null}
                {s.expense ? (
                  <span className="num" style={{ fontSize: 13, marginLeft: "auto" }}>
                    {formatBRL(s.expense.amountCents)}
                  </span>
                ) : null}
                {graphHref ? (
                  <Link href={graphHref} className="btn" style={{ fontSize: 11 }}>
                    grafo completo
                  </Link>
                ) : null}
              </div>
              <p className="mt-1.5 text-[13px] leading-snug" style={{ color: "var(--fg-2)" }}>
                {s.explanation}
              </p>
              {s.aiReview ? (
                <p className="mt-1.5 max-w-2xl border-l-2 pl-2.5 text-[11.5px] leading-snug" style={{ borderColor: "var(--border-1)", color: "var(--muted)" }}>
                  <span className="mono-label">IA</span> {s.aiReview.explanation}
                </p>
              ) : null}
              {s.expense ? (
                <div className="mono mt-1" style={{ fontSize: 10, color: "var(--muted-2)" }}>
                  {s.expense.year} · fornecedor: {s.expense.supplierName ?? "n/d"}
                </div>
              ) : null}
            </article>
          );
        })}
      </div>
    </section>
  );
}

async function CandidaciesSection({ personId }: { personId: number }) {
  const candidacies = getPersonCandidacies(personId);
  if (candidacies.length === 0) return null;
  const campaignOrgs = getPersonCampaignOrgs(personId);
  const cnpjByYear = new Map(campaignOrgs.map((o) => [o.year, o.cnpj]));

  return (
    <Section id="candidaturas" title="candidaturas por eleição">
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {candidacies.map((c) => {
          const tone = resultTone(c.result);
          const toneColor =
            tone === "green" ? "text-elo-green" : tone === "red" ? "text-elo-red" : "text-[var(--muted)]";
          const cnpj = cnpjByYear.get(c.year);
          return (
            <SourceZone key={c.id} provenance={c.provenance}>
              <div className="card flex flex-col gap-1.5" style={{ padding: "12px 14px" }}>
                <div className="flex items-center justify-between">
                  <span className="font-mono text-[13px] text-[var(--fg-2)]">{c.year}</span>
                  <span className={`font-mono text-[10.5px] ${toneColor}`}>{c.result ?? "sem resultado"}</span>
                </div>
                <div className="truncate text-[13.5px]">{c.office ?? "cargo não informado"}</div>
                <div className="truncate font-mono text-[10px] text-[var(--muted-2)]">
                  {c.partyAbbr ?? "s/partido"} · {c.state ?? "—"}
                  {c.round ? ` · ${c.round}º turno` : ""}
                </div>
                {cnpj ? (
                  <Link
                    href={`/cnpj/${cnpj}`}
                    className="truncate font-mono text-[10px] text-[var(--accent-2)] hover:underline"
                  >
                    {formatCnpj(cnpj)}
                  </Link>
                ) : null}
              </div>
            </SourceZone>
          );
        })}
      </div>
    </Section>
  );
}

async function AssetsSection({ personId }: { personId: number }) {
  const { declaredAssets, declaredAssetsByYear } = getPersonAssets(personId);
  if (declaredAssets.length === 0) return null;
  return (
    <Section id="bens-declarados" title="bens declarados">
      {declaredAssetsByYear.length > 1 ? (
        <div className="mb-6">
          <div className="label mb-2">patrimônio declarado por eleição</div>
          <AssetsCurveChart data={declaredAssetsByYear.map((y) => ({ year: y.year, totalCents: y.totalCents }))} />
        </div>
      ) : null}

      <AssetsYearCards byYear={declaredAssetsByYear} assets={declaredAssets} />
    </Section>
  );
}

async function EarmarksSection({ personId }: { personId: number }) {
  const { earmarks, totalCommittedCents, totalPaidCents } = getPersonEarmarks(personId);
  if (earmarks.length === 0) return null;
  return (
    <Section id="emendas-parlamentares" title="emendas parlamentares">
      <p className="mb-4 text-[11.5px] text-[var(--muted-2)]">
        Emendas ao orçamento federal autoradas por esta pessoa (Portal da Transparência) — o
        autor é identificado só por nome, não por CPF, então é um cruzamento provável, não uma
        identidade confirmada.
      </p>
      <div className="kpis mb-6">
        <div className="kpi">
          <div className="kpi__label">valor empenhado</div>
          <div className="kpi__value">{formatBRL(totalCommittedCents)}</div>
          <div className="kpi__sub">{earmarks.length.toLocaleString("pt-BR")} emendas</div>
        </div>
        <div className="kpi">
          <div className="kpi__label">valor pago</div>
          <div className="kpi__value">{formatBRL(totalPaidCents)}</div>
        </div>
      </div>
      <div className="table-wrap">
        <div className="overflow-x-auto">
          <table className="table min-w-[640px]">
            <thead>
              <tr>
                <th>ano</th>
                <th>localidade</th>
                <th>ação</th>
                <th className="text-right">empenhado</th>
                <th className="text-right">pago</th>
              </tr>
            </thead>
            <tbody>
              {earmarks.map((e) => (
                <SourceZone key={e.id} as="tr" provenance={e.provenance}>
                  <td className="num">{e.year}</td>
                  <td className="max-w-[220px]">
                    {e.locality ?? (e.municipality && e.state ? `${e.municipality}/${e.state}` : "—")}
                  </td>
                  <td className="max-w-[260px]" style={{ fontSize: 12, color: "var(--muted)" }}>
                    {e.actionName ?? e.functionName ?? "—"}
                  </td>
                  <td className="num">{e.committedCents != null ? formatBRL(e.committedCents) : "—"}</td>
                  <td className="num" style={{ color: "var(--muted)" }}>
                    {e.paidCents != null ? formatBRL(e.paidCents) : "—"}
                  </td>
                </SourceZone>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </Section>
  );
}

const RECENT_VOTES = 10;

async function VotesSection({ personId }: { personId: number }) {
  const { camara, senado } = getPersonVotesSummary(personId, { includeVotes: true });
  const blocks = [...camara, ...senado].filter((s) => s.votes.length > 0);
  if (blocks.length === 0) return null;
  return (
    <Section id="como-votou" title="como votou no plenário">
      <p className="mb-4 text-[11.5px] text-[var(--muted-2)]">
        Votações nominais de mérito no Plenário (2019–2026), sem os requerimentos de procedimento.
        Ligação pela ficha da Câmara/Senado (CPF ou nome + nascimento) com esta pessoa do TSE.
      </p>
      <div className="flex flex-col gap-10">
        {blocks.map((s) => {
          const { house, id, name } = s.identity;
          return (
            <div key={`${house}-${id}`} className="flex flex-col gap-4">
              <SourceZone provenance={s.sourcesBySection.identity[0] ?? s.identity.provenance}>
                <div className="label">
                  {HOUSE_LABEL[house]} · {name ?? `parlamentar ${id}`}
                  {s.identity.matchBasis ? ` · vínculo: ${s.identity.matchBasis}` : ""}
                </div>
              </SourceZone>
              {/* Multi-year aggregates: every file is listed; each table row cites its own. */}
              <ParliamentarianKpis house={house} summary={s} />
              <SourcesList sources={s.sources} label="fontes deste resumo" />
              <div>
                <div className="label mb-2">
                  {Math.min(RECENT_VOTES, s.votes.length)} votações de mérito mais recentes
                </div>
                <ParliamentarianVotesTable house={house} rows={s.votes.slice(0, RECENT_VOTES)} compact />
              </div>
              <Link href={parliamentarianHref(house, id)} className="mono-label hover:text-[var(--accent-2)]">
                ver todas as {s.votes.length.toLocaleString("pt-BR")} votações, com filtros{house === "camara" ? " por ano e tema" : " por ano"} →
              </Link>
            </div>
          );
        })}
      </div>
    </Section>
  );
}

async function FinanceSection({
  personId, year,
}: { personId: number; year: number | undefined }) {
  const finance = year != null ? getPersonFinance(personId, year) : getPersonFinance(personId);
  return (
    <div id="financas" data-toc-title="finanças">
      {finance.donationsCount > 0 ? (
        <FinanceTable
          title="doações recebidas"
          scope="candidate"
          id={String(personId)}
          dir="received"
          counterpartyLabel="doador"
          tone="green"
          year={year}
        />
      ) : null}

      {finance.expensesCount > 0 ? (
        <FinanceTable
          title="despesas — pra onde foi o dinheiro"
          scope="candidate"
          id={String(personId)}
          dir="spent"
          counterpartyLabel="fornecedor"
          tone="amber"
          year={year}
        />
      ) : null}

      {finance.donationsCount === 0 && finance.expensesCount === 0 ? (
        <div className="py-7">
          <EmptyState icon="◌" title={`Nenhuma doação ou despesa registrada ${year != null ? `em ${year}` : ""}.`} compact />
        </div>
      ) : null}
    </div>
  );
}

async function SocialMediaSection({ personId }: { personId: number }) {
  const socialMedia = getPersonSocialMedia(personId);
  if (socialMedia.length === 0) return null;
  return (
    <Section id="redes-sociais" title="redes sociais declaradas">
      <div className="flex flex-wrap gap-2">
        {socialMedia.map((s) => (
          <SourceZone key={s.id} provenance={s.provenance} inline>
            <SocialCard social={s} />
          </SourceZone>
        ))}
      </div>
    </Section>
  );
}

async function DiscourseSection({ personId, displayName }: { personId: number; displayName: string }) {
  const discourseCount = getDiscourseCount({ personId });
  if (discourseCount === 0) return null;
  const discourse = getDiscourseSignals({ personId, limit: DISCOURSE_SHOWN });
  return (
    <Section id="discurso" tocLabel="discurso" title="posts no X sinalizados pela IA">
      <p className="mb-4 text-[11.5px] text-[var(--muted-2)]">
        Classificação automática por IA — pode errar.
        {discourseCount > discourse.length ? ` Mostrando os ${discourse.length} de ${discourseCount}.` : ""}
      </p>
      <div className="flex flex-col gap-2">
        {discourse.map((t) => (
          <TweetCard key={t.postId} post={t} fallbackName={displayName} />
        ))}
      </div>
      {discourseCount > discourse.length ? (
        <div className="mt-3">
          <Link href={`/sinais/discurso?q=${encodeURIComponent(discourse[0]?.handle ?? "")}`} className="mono-label hover:text-[var(--accent-2)]">
            ver todos os {discourseCount} posts →
          </Link>
        </div>
      ) : null}
    </Section>
  );
}

function Section({
  id, title, tocLabel, children,
}: { id?: string; title: string; tocLabel?: string; children: React.ReactNode }) {
  return (
    <section id={id} data-toc-title={tocLabel ?? title} className="animate-in py-7">
      <div className="section-title mb-4">{title}</div>
      {children}
    </section>
  );
}


function SectionSkeleton({ id, title, rows = 3 }: { id?: string; title: string; rows?: number }) {
  return (
    <section id={id} data-toc-title={title} className="py-7">
      <div className="section-title mb-4">{title}</div>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: rows }).map((_, i) => (
          <Skeleton key={i} className="h-[68px] w-full" style={{ borderRadius: "var(--r-md)" }} />
        ))}
      </div>
    </section>
  );
}

function NetworkSkeleton() {
  return (
    <section id="rede-de-doacao" data-toc-title="rede de doação" className="py-7">
      <div className="section-title mb-4">rede de doação</div>
      <Skeleton className="h-[260px] w-full" style={{ borderRadius: "var(--r-md)" }} />
    </section>
  );
}

function SignalsSkeleton() {
  return (
    <section id="sinais" data-toc-title="sinais de alerta" className="py-7">
      <div className="section-title mb-4 !text-elo-red">sinais de alerta</div>
      <div className="flex flex-col gap-2">
        {Array.from({ length: 2 }).map((_, i) => (
          <div key={i} className="signal signal--medium">
            <div className="flex items-center gap-3">
              <Skeleton className="h-4 w-14" />
              <Skeleton className="h-3 w-24" />
            </div>
            <Skeleton className="mt-2 h-3 w-3/4" />
          </div>
        ))}
      </div>
    </section>
  );
}

function FinanceSkeleton() {
  return (
    <div id="financas" data-toc-title="finanças" className="flex flex-col gap-6 py-6">
      {Array.from({ length: 2 }).map((_, i) => (
        <div key={i}>
          <Skeleton className="mb-4 h-3 w-40" />
          <div className="table-wrap">
            <div className="p-4">
              {Array.from({ length: 5 }).map((_, j) => (
                <Skeleton key={j} className="mb-3 h-3.5 w-full last:mb-0" />
              ))}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
