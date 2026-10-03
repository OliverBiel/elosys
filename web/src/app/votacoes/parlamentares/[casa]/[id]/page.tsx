import Link from "next/link";
import { notFound } from "next/navigation";
import {
  getDeputySummary,
  getParliamentarian,
  listThemes,
  listYears,
  type VoteScope,
} from "@/lib/votes-queries";
import { DEFINITIONS, HOUSE_LABEL } from "@/lib/vote-meaning";
import { getPersonPhotoProvenance, getPersonPhotoUrl } from "@/lib/queries";
import { PageHeader } from "@/components/shell/shell-context";
import { SourceZone } from "@/components/source-zone";
import { ProfilePhoto } from "@/components/profile-photo";
import { EmptyState } from "@/components/ui/empty-state";
import { PaginationLinks } from "@/components/ui/pagination-links";
import { ParliamentarianKpis } from "@/components/votes/parliamentarian-kpis";
import { ParliamentarianPartyHistory } from "@/components/votes/parliamentarian-party-history";
import { ParliamentarianVotesTable } from "@/components/votes/parliamentarian-votes-table";
import {
  Definition,
  HOUSE_MEMBER,
  PersonLinkOrReason,
  isHouse,
  parliamentarianHref,
} from "@/components/votes/parliamentarian-shared";
import { SELECT_CLASS } from "@/components/votes/party-ui";
import { SourcesList } from "@/components/votes/sources-list";
import { formatVoteDate } from "@/components/votes/vote-result";

export const dynamic = "force-dynamic";

const VOTES_PAGE_SIZE = 100;

export default async function ParlamentarPage({
  params, searchParams,
}: PageProps<"/votacoes/parlamentares/[casa]/[id]">) {
  const { casa, id: idParam } = await params;
  if (!isHouse(casa)) notFound();
  const id = Number(idParam);
  if (!Number.isInteger(id)) notFound();
  const identity = getParliamentarian(casa, id);
  if (!identity) notFound();

  const sp = await searchParams;
  const one = (k: string) => (typeof sp[k] === "string" ? (sp[k] as string) : "");
  const years = listYears(casa);
  // proc=1 as on /votacoes, /partidos and /comparar; procedimentais=1 kept for old links.
  const includeProcedural = one("proc") === "1" || one("procedimentais") === "1";
  const themes = casa === "camara" ? listThemes(includeProcedural) : [];
  const yearParam = Number(one("ano"));
  const year = years.includes(yearParam) ? yearParam : undefined;
  const themeParam = Number(one("tema"));
  const theme = one("tema") && themes.some((t) => t.code === themeParam) ? themeParam : undefined;
  const page = Math.max(1, Math.floor(Number(one("page")) || 1));

  const scope: VoteScope = { year, theme, includeProcedural };
  const summary = getDeputySummary(casa, id, { ...scope, includeVotes: true });
  if (!summary) notFound();
  // Party history always covers every roll call, whatever the filters of the vote list.
  const isFull = includeProcedural && year == null && theme == null;
  const history = isFull
    ? summary.votes
    : (getDeputySummary(casa, id, { includeProcedural: true, includeVotes: true })?.votes ?? []);

  const name = identity.name ?? `parlamentar ${id}`;
  const photoUrl = identity.personId != null ? getPersonPhotoUrl(identity.personId) : null;
  const photoProvenance = photoUrl && identity.personId != null ? getPersonPhotoProvenance(identity.personId) : null;
  const totalPages = Math.max(1, Math.ceil(summary.votes.length / VOTES_PAGE_SIZE));
  const pageRows = summary.votes.slice((page - 1) * VOTES_PAGE_SIZE, page * VOTES_PAGE_SIZE);
  const themeName = theme != null ? themes.find((t) => t.code === theme)?.name : null;
  const src = summary.sourcesBySection;
  const identityProvenance = src.identity[0] ?? identity.provenance;

  const back = new URLSearchParams();
  if (casa === "senado") back.set("casa", "senado");
  back.set("uf", identity.lastState ?? "DF");
  back.set("candidatos", "0");
  const backHref = `/votacoes/parlamentares?${back.toString()}`;

  // Query contract of /votacoes/comparar (T10): casa (default camara), modo, id (repeatable).
  const compareHref = (modo: string) =>
    `/votacoes/comparar?${casa === "senado" ? "casa=senado&" : ""}modo=${modo}&id=${id}`;

  const hrefFor = (p: number) => {
    const usp = new URLSearchParams();
    if (year != null) usp.set("ano", String(year));
    if (theme != null) usp.set("tema", String(theme));
    if (includeProcedural) usp.set("proc", "1");
    if (p > 1) usp.set("page", String(p));
    const s = usp.toString();
    return `${parliamentarianHref(casa, id)}${s ? `?${s}` : ""}#votacoes`;
  };

  return (
    <main className="mx-auto w-full max-w-5xl pt-8">
      <PageHeader group="Votações" current={name} />

      <header className="animate-in pb-8">
        <div className="mono-label mb-3">
          <Link href={backHref} className="hover:text-[var(--accent-2)]">
            ← parlamentares {identity.lastState ? `de ${identity.lastState}` : ""}
          </Link>
        </div>
        <div className="flex items-center gap-4">
          {photoUrl ? (
            <SourceZone provenance={photoProvenance} inline>
              <ProfilePhoto url={photoUrl} size={72} />
            </SourceZone>
          ) : null}
          <SourceZone provenance={identityProvenance}>
            <h1 className="text-[34px] leading-tight font-light tracking-tight text-balance sm:text-[40px]">
              {name}
            </h1>
          </SourceZone>
        </div>
        <SourceZone provenance={identityProvenance}>
          <div className="mt-2 text-[14px] text-[var(--muted)]">
            {HOUSE_MEMBER[casa]} · {HOUSE_LABEL[casa]}
            {identity.lastParty ? ` · ${identity.lastParty}` : ""}
            {identity.lastState ? `/${identity.lastState}` : ""}
            {identity.lastVoteDate ? ` · último voto em ${formatVoteDate(identity.lastVoteDate)}` : ""}
          </div>
          {identity.fullName ? (
            <div className="mono mt-1 text-[11px] text-[var(--muted-2)]">nome civil: {identity.fullName}</div>
          ) : null}
        </SourceZone>
        <div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-2">
          <PersonLinkOrReason identity={identity} />
          <Link href={compareHref("parlamentares")} className="btn" style={{ fontSize: 11 }}>
            comparar com outro parlamentar
          </Link>
          <Link href={compareHref("parlamentar-partido")} className="btn" style={{ fontSize: 11 }}>
            comparar com um partido
          </Link>
        </div>
      </header>

      <section className="animate-in py-4">
        <form className="flex flex-wrap items-center gap-2" action={parliamentarianHref(casa, id)}>
          <select name="ano" defaultValue={year ?? ""} className={SELECT_CLASS} aria-label="ano">
            <option value="">2019–2026 (todos os anos)</option>
            {years.map((y) => (
              <option key={y} value={y}>{y}</option>
            ))}
          </select>
          {casa === "camara" ? (
            <select name="tema" defaultValue={theme ?? ""} className={SELECT_CLASS} aria-label="tema">
              <option value="">todos os temas</option>
              {themes.map((t) => (
                <option key={t.code} value={t.code}>{t.name}</option>
              ))}
            </select>
          ) : null}
          <label className="flex items-center gap-1.5 text-[12px] text-[var(--muted)]">
            <input type="checkbox" name="proc" value="1" defaultChecked={includeProcedural} />
            incluir votações procedimentais (requerimentos)
          </label>
          <button type="submit" className="btn">aplicar</button>
        </form>
        <p className="mt-2 text-[11.5px] text-[var(--muted-2)]">
          Recorte: {includeProcedural ? "todas as votações nominais do Plenário" : "votações de mérito (sem procedimentais)"}
          {year != null ? `, ${year}` : ", 2019–2026"}
          {themeName ? `, tema "${themeName}"` : ""}.
          {casa === "senado" ? " O Senado não classifica votações por tema." : ""}
        </p>
      </section>

      <section id="resumo" data-toc-title="resumo" className="animate-in py-6">
        <div className="section-title mb-4">resumo</div>
        {summary.votes.length === 0 ? (
          <EmptyState compact title="nenhuma votação nesse recorte." />
        ) : (
          // Aggregates over 2019–2026: every file behind them is listed (one file would be false).
          <div className="flex flex-col gap-3">
            <ParliamentarianKpis house={casa} summary={summary} />
            <SourcesList sources={summary.sources} label="fontes deste resumo (votos, mandatos, presença)" />
          </div>
        )}
      </section>

      {history.length > 0 ? (
        <section id="partidos" data-toc-title="partidos ao longo do tempo" className="animate-in py-6">
          <div className="section-title mb-4">partidos ao longo do tempo</div>
          <ParliamentarianPartyHistory house={casa} rows={history} />
        </section>
      ) : null}

      {summary.votes.length > 0 ? (
        <section id="votacoes" data-toc-title="votação por votação" className="animate-in py-6">
          <div className="section-title mb-2">votação por votação</div>
          <p className="mb-4 max-w-2xl text-[11.5px] text-[var(--muted-2)]">
            Mais recentes primeiro. Clique na votação para ver a descrição oficial completa, como
            cada partido orientou e a fonte. &quot;Seguiu o partido?&quot; só vale quando o partido
            orientou Sim, Não ou Obstrução e o parlamentar votou.
          </p>
          <ParliamentarianVotesTable house={casa} rows={pageRows} />
          {totalPages > 1 ? (
            <div className="mt-3 flex items-center justify-between">
              <span className="mono text-[11px] text-[var(--muted-2)]">
                {((page - 1) * VOTES_PAGE_SIZE + 1).toLocaleString("pt-BR")}–
                {Math.min(page * VOTES_PAGE_SIZE, summary.votes.length).toLocaleString("pt-BR")} de{" "}
                {summary.votes.length.toLocaleString("pt-BR")}
              </span>
              <PaginationLinks page={page} totalPages={totalPages} makeHref={hrefFor} />
            </div>
          ) : null}
          <div className="mt-4 max-w-3xl">
            <Definition label="como lemos o que o Sim significa" text={DEFINITIONS.meaning} />
          </div>
        </section>
      ) : null}
    </main>
  );
}
