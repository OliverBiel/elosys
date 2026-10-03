import Link from "next/link";
import { notFound } from "next/navigation";
import { getVote, type VoteDetail } from "@/lib/votes-queries";
import type { Provenance } from "@/lib/queries";
import { DEFINITIONS, HOUSE_LABEL, voteValueLabel, type House, type VoteValue } from "@/lib/vote-meaning";
import { PageHeader } from "@/components/shell/shell-context";
import { EmptyState } from "@/components/ui/empty-state";
import { SourceZone } from "@/components/source-zone";
import { VoteMeaningBox } from "@/components/votes/vote-meaning-box";
import { VoteOrientationsTable } from "@/components/votes/vote-orientations-table";
import { VotePartyTable } from "@/components/votes/vote-party-table";
import { VoteCastsTable } from "@/components/votes/vote-casts-table";
import { VotePresence } from "@/components/votes/vote-presence";
import { VoteResultBadge, formatCount, formatVoteDate } from "@/components/votes/vote-result";
import { SourcesList } from "@/components/votes/sources-list";

export const dynamic = "force-dynamic";

const ID_PATTERN: Record<House, RegExp> = { camara: /^\d+-\d+$/, senado: /^\d+$/ };

const VALUE_ORDER: VoteValue[] = [
  "yes", "no", "abstention", "obstruction", "presiding", "secret", "present_not_voted",
  "absent_justified", "absent", "other", "unknown",
];

function SectionTitle({ children, sub }: { children: React.ReactNode; sub?: React.ReactNode }) {
  return (
    <div className="mb-4">
      <h2 className="text-[18px] font-medium tracking-tight">{children}</h2>
      {sub ? (
        <p className="mt-1 max-w-3xl text-[12.5px] leading-relaxed" style={{ color: "var(--muted)" }}>{sub}</p>
      ) : null}
    </div>
  );
}

function voteTitle(v: VoteDetail): string {
  if (v.house === "senado") return v.identification ?? v.kindLabel;
  const titles = v.propositions.map(
    (p) => p.title ?? [p.typeAbbr, p.number != null ? `${p.number}/${p.year ?? "?"}` : null].filter(Boolean).join(" ")
  );
  return titles.length > 0 ? titles.join(", ") : v.kindLabel;
}

export default async function VotacaoPage({ params }: PageProps<"/votacoes/[casa]/[id]">) {
  const { casa, id: rawId } = await params;
  if (casa !== "camara" && casa !== "senado") notFound();
  const house: House = casa;
  const id = decodeURIComponent(rawId);
  if (!ID_PATTERN[house].test(id)) notFound();
  const v = getVote(house, id);
  if (!v) notFound();

  const title = voteTitle(v);
  const bySection = v.sourcesBySection;
  const castProvenance: Provenance | null = bySection.casts[0] ?? null;
  const presenceProvenance: Provenance | null = bySection.presence[0] ?? null;

  // Câmara publishes votosOutros (officialTotals.other); the Senado publishes abstention.
  const ot = v.officialTotals;
  const official = ot.yes != null || ot.no != null;
  const yes = official ? ot.yes : (v.castTotals.yes ?? 0);
  const no = official ? ot.no : (v.castTotals.no ?? 0);
  const third: { label: string; value: number | null } =
    house === "camara"
      ? { label: "outros (abstenção, obstrução…)", value: ot.other }
      : { label: "abstenção", value: official ? ot.abstention : (v.castTotals.abstention ?? 0) };
  // Generic note only off destaques: a Senado destaque explains its own outcome (senadoResult).
  const quorumNote = !v.result.destaque && v.result.approved === false && yes != null && no != null && yes > no;
  const castBreakdown = VALUE_ORDER.filter((k) => (v.castTotals[k] ?? 0) > 0);
  // 'other' keeps the source code next to it ("Outro (código: NA)"): one part per raw code.
  const otherByRaw = new Map<string, number>();
  for (const c of v.casts) if (c.value === "other") otherByRaw.set(c.raw, (otherByRaw.get(c.raw) ?? 0) + 1);
  const breakdownText = castBreakdown
    .flatMap((k) =>
      k === "other"
        ? [...otherByRaw].map(([raw, n]) => `${formatCount(n)} ${voteValueLabel("other", house, raw)}`)
        : [`${formatCount(v.castTotals[k])} ${voteValueLabel(k, house)}`]
    )
    .join(" · ");
  const time = v.registeredAt?.match(/T(\d{2}:\d{2})/)?.[1];

  const officialLinks: Array<{ href: string; label: string }> = [];
  if (house === "camara") {
    if (v.presence?.eventId != null) {
      officialLinks.push({
        href: `https://www.camara.leg.br/evento-legislativo/${v.presence.eventId}`,
        label: "Sessão no site da Câmara (camara.leg.br)",
      });
    }
    for (const p of v.propositions) {
      officialLinks.push({
        href: `https://www.camara.leg.br/propostas-legislativas/${p.propositionId}`,
        label: `Tramitação de ${p.title ?? `proposição ${p.propositionId}`} (camara.leg.br)`,
      });
    }
    officialLinks.push({
      href: `https://dadosabertos.camara.leg.br/api/v2/votacoes/${encodeURIComponent(v.id)}`,
      label: "Esta votação na API de dados abertos da Câmara",
    });
  } else {
    const matter = v.senate?.matterCode;
    if (matter != null) {
      officialLinks.push({
        href: `https://www25.senado.leg.br/web/atividade/materias/-/materia/${matter}`,
        label: `Tramitação de ${v.identification ?? `matéria ${matter}`} (senado.leg.br)`,
      });
    }
    officialLinks.push({
      href: v.provenance.url,
      label: "Votações do Senado no período (dados abertos do Senado, senado.leg.br)",
    });
  }

  return (
    <div className="flex flex-col gap-10">
      <PageHeader group="Votações" current={title} />

      <header>
        <Link
          href={`/votacoes${house === "senado" ? "?casa=senado" : ""}`}
          className="mono text-[11px] hover:underline"
          style={{ color: "var(--muted-2)" }}
        >
          ← todas as votações
        </Link>
        <SourceZone provenance={v.provenance}>
          <div className="mono mt-4 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[11.5px]" style={{ color: "var(--muted)" }}>
            <span>{HOUSE_LABEL[house]}</span>
            <span>·</span>
            <span>
              {formatVoteDate(v.date)}
              {time ? ` às ${time}` : ""}
            </span>
            {v.legislature ? (
              <>
                <span>·</span>
                <span>{v.legislature}ª legislatura</span>
              </>
            ) : null}
            <span className="badge">{v.kindLabel}</span>
            {v.isSecret ? <span className="badge badge--accent">votação secreta</span> : null}
          </div>
          <h1 className="mt-3 text-[28px] leading-tight font-medium tracking-tight">{title}</h1>
        </SourceZone>

        {house === "camara" ? (
          v.propositions.length > 0 ? (
            <div className="mt-4 flex flex-col gap-4">
              {v.propositions.map((p) => (
                <SourceZone key={p.propositionId} provenance={p.provenance}>
                  <div>
                    {v.propositions.length > 1 ? (
                      <div className="mono text-[11px]" style={{ color: "var(--muted-2)" }}>{p.title}</div>
                    ) : null}
                    {p.ementa ? (
                      <p className="max-w-3xl text-[14px] leading-relaxed" style={{ color: "var(--fg-2)" }}>
                        {p.ementa}
                      </p>
                    ) : null}
                    {p.themes.length > 0 ? (
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {p.themes.map((t) => (
                          <Link
                            key={t.code}
                            href={`/votacoes?tema=${t.code}${v.kind === "procedural" ? "&proc=1" : ""}`}
                            className="badge hover:underline"
                          >
                            {t.name ?? `tema ${t.code}`}
                          </Link>
                        ))}
                      </div>
                    ) : null}
                  </div>
                </SourceZone>
              ))}
            </div>
          ) : (
            <p className="mt-4 text-[13px]" style={{ color: "var(--muted)" }}>
              A fonte não associa nenhuma proposição a esta votação.
            </p>
          )
        ) : v.ementa ? (
          <SourceZone provenance={v.provenance}>
            <p className="mt-4 max-w-3xl text-[14px] leading-relaxed" style={{ color: "var(--fg-2)" }}>{v.ementa}</p>
          </SourceZone>
        ) : null}
      </header>

      <VoteMeaningBox
        meaning={v.meaning}
        meaningCode={v.meaningCode}
        kindBasis={v.kindBasis}
        description={v.description}
        openingDescription={v.openingDescription}
        provenance={v.provenance}
      />

      <section>
        <SectionTitle>Resultado e placar</SectionTitle>
        <SourceZone provenance={v.provenance}>
          <div className="kpis">
            <div className="kpi">
              <div className="kpi__label">resultado</div>
              <div className="mt-2">
                <VoteResultBadge result={v.result} />
              </div>
            </div>
            <div className="kpi">
              <div className="kpi__label">Sim</div>
              <div className="kpi__value kpi__value--green">{formatCount(yes)}</div>
            </div>
            <div className="kpi">
              <div className="kpi__label">Não</div>
              <div className="kpi__value kpi__value--red">{formatCount(no)}</div>
            </div>
            <div className="kpi">
              <div className="kpi__label">{third.label}</div>
              <div className="kpi__value">{formatCount(third.value)}</div>
            </div>
          </div>
        </SourceZone>
        <div className="mt-3 flex max-w-3xl flex-col gap-2 text-[12.5px] leading-relaxed" style={{ color: "var(--muted)" }}>
          <p>
            {official
              ? "Placar oficial publicado pela Casa."
              : "O Senado só publica o placar em votação secreta; aqui ele foi contado da lista de votos."}
          </p>
          {v.isSecret ? (
            <p className="signal__note signal__note--soft" style={{ marginTop: 0 }}>
              Votação secreta: a fonte diz quem votou e o placar total, mas não como cada um votou. Na
              lista abaixo, o voto de cada um aparece como &quot;Voto secreto&quot;.
            </p>
          ) : null}
          {v.result.note ? <p>{v.result.note}</p> : null}
          {v.result.officialText ? (
            <p>
              Registro oficial do Senado para esta votação:{" "}
              <q style={{ color: "var(--fg-2)" }}>{v.result.officialText}</q>
            </p>
          ) : null}
          {v.result.destaque ? (
            <p>
              {DEFINITIONS.senadoDestaque}
              {v.result.officialText ? null : " O registro oficial está na tramitação da matéria (Fontes oficiais, abaixo)."}
            </p>
          ) : null}
          {quorumNote ? (
            <p>
              Rejeitado mesmo com mais Sim que Não: algumas votações exigem um número mínimo de votos
              Sim (quórum qualificado — por exemplo, PEC precisa de 3/5 da Casa), não só mais Sim que
              Não.
            </p>
          ) : null}
          {v.informeText ? (
            <SourceZone provenance={v.provenance}>
              <details>
                <summary className="cursor-pointer select-none">
                  registro oficial do Senado sobre a matéria nesta sessão (texto integral, todas as votações dela)
                </summary>
                <p className="mt-2 whitespace-pre-line text-[12px]" style={{ color: "var(--fg-2)" }}>{v.informeText}</p>
              </details>
            </SourceZone>
          ) : null}
          {castBreakdown.length > 0 ? (
            <SourceZone provenance={castProvenance}>
              <p className="mono text-[11.5px]" style={{ color: "var(--muted-2)" }}>
                Na lista de votos: {breakdownText}
              </p>
            </SourceZone>
          ) : null}
        </div>
      </section>

      <section>
        <SectionTitle sub="Rotulada com o governo em exercício na data da votação.">
          Orientação do Governo
        </SectionTitle>
        <SourceZone provenance={v.orientations.find((o) => o.benchKind === "government")?.provenance ?? null}>
          <div className="card" style={{ padding: "14px 18px" }}>
            <span className="label">{v.government.government?.label ?? "Governo"}</span>
            {v.government.government ? (
              <span className="ml-2 text-[11px]" style={{ color: "var(--muted-2)" }}>
                ({v.government.government.president})
              </span>
            ) : null}
            <div className="mt-1.5 text-[16px]" style={{ color: v.government.value ? "var(--fg-1)" : "var(--muted)" }}>
              {v.government.label}
            </div>
          </div>
        </SourceZone>
      </section>

      <section>
        <SectionTitle
          sub={
            house === "camara"
              ? "Como cada bancada (partido, federação, bloco ou liderança) orientou seus membros, com o nome exatamente como a Câmara publicou."
              : "Como cada bancada orientou, com o nome exatamente como o painel do Senado publicou."
          }
        >
          Orientações das bancadas
        </SectionTitle>
        {v.orientations.length === 0 ? (
          house === "senado" ? (
            <EmptyState icon="◌" title="sem orientação publicada" hint={DEFINITIONS.senadoOrientation} compact />
          ) : (
            <EmptyState icon="◌" title="nenhuma orientação registrada para esta votação" compact />
          )
        ) : (
          <VoteOrientationsTable rows={v.orientations} />
        )}
      </section>

      <section>
        <SectionTitle sub="Votos de cada partido nesta votação, pela sigla que o parlamentar tinha no dia.">
          Como cada partido votou
        </SectionTitle>
        {v.parties.length === 0 ? (
          <EmptyState icon="◌" title="nenhum voto com partido nesta votação" compact />
        ) : (
          <div className="flex flex-col gap-3">
            {v.isSecret ? (
              <p className="max-w-3xl text-[12.5px] leading-relaxed" style={{ color: "var(--muted)" }}>
                Votação secreta: a tabela mostra quantos de cada partido votaram, mas a posição de cada
                partido é desconhecida.
              </p>
            ) : null}
            <VotePartyTable house={house} parties={v.parties} provenance={castProvenance} legislature={v.legislature} />
          </div>
        )}
      </section>

      <section>
        <SectionTitle
          sub={
            house === "senado"
              ? v.listsOnlyVoters
                ? "Esta votação lista só quem votou: senador que não aparece aqui fica como \"desconhecido\", nunca como ausente."
                : DEFINITIONS.presenceSenado
              : "Quem aparece na lista oficial de votos desta votação."
          }
        >
          Voto de cada {house === "camara" ? "deputado" : "senador"} ({formatCount(v.casts.length)})
        </SectionTitle>
        {v.casts.length === 0 ? (
          <EmptyState icon="◌" title="a fonte não publicou a lista de votos" compact />
        ) : (
          <VoteCastsTable house={house} casts={v.casts} provenance={castProvenance} />
        )}
      </section>

      {v.presence ? (
        <section>
          <SectionTitle>Presença na sessão</SectionTitle>
          <VotePresence presence={v.presence} provenance={presenceProvenance} />
        </section>
      ) : null}

      <section>
        <SectionTitle>Fontes oficiais</SectionTitle>
        <ul className="flex flex-col gap-1.5 text-[13px]">
          {officialLinks.map((l) => (
            <li key={l.href}>
              <a href={l.href} target="_blank" rel="noreferrer" className="link-primary">
                {l.label} ↗
              </a>
            </li>
          ))}
        </ul>
        <div className="mt-4">
          <SourcesList
            open
            label="arquivos usados nesta página"
            sources={[v.provenance, ...v.sources, ...bySection.propositions, ...bySection.themes, ...bySection.terms]
              .filter((p, i, all) => all.findIndex((x) => x.url === p.url && x.sha256 === p.sha256) === i)}
          />
        </div>
      </section>
    </div>
  );
}
