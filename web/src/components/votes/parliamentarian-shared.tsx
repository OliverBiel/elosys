// Display helpers shared by the parliamentarian pages and the "Como votou" section of /politico.
// Pure presentation: every number comes from votes-queries.ts; nothing here queries the DB.

import Link from "next/link";
import type { GovernmentRatios, ParliamentarianIdentity, Ratio } from "@/lib/votes-queries";
import { GOVERNMENT_PERIODS, type GovernmentId, type House } from "@/lib/vote-meaning";
import { formatPct } from "@/lib/format";
import { MIN_N } from "./party-ui";

export function pctText(r: Ratio): string {
  return r.pct == null ? "—" : formatPct(r.pct);
}

/** True when a percentage rests on fewer than MIN_N votes ("poucos dados"). */
export const lowN = (r: Ratio | null | undefined): boolean => !!r && r.n > 0 && r.n < MIN_N;

export function ratioSub(r: Ratio, unit: string): string {
  if (r.n === 0) return "nada contável neste recorte";
  const base = `${r.agree.toLocaleString("pt-BR")} de ${r.n.toLocaleString("pt-BR")} ${unit}`;
  return lowN(r) ? `${base} · poucos dados` : base;
}

export const HOUSE_MEMBER: Record<House, string> = { camara: "deputado(a)", senado: "senador(a)" };
export const HOUSE_SHORT: Record<House, string> = { camara: "Câmara", senado: "Senado" };

export function isHouse(v: unknown): v is House {
  return v === "camara" || v === "senado";
}

export type GovernmentRow = { id: GovernmentId; label: string; ratio: Ratio | null };

const GOV_YEARS: Record<GovernmentId, string> = { bolsonaro: "2019–2022", lula: "2023–" };

/** Both governments, always separate (never blended); ratio null = no vote of that government. */
export function governmentRows(w: GovernmentRatios): GovernmentRow[] {
  return GOVERNMENT_PERIODS.map((g) => ({
    id: g.id,
    label: `${g.label} (${GOV_YEARS[g.id]})`,
    ratio: w[g.id],
  }));
}

// unmatched_reason codes written by elosys/camara/deputies.py match_person and
// elosys/senado/votes.py (senator index); the raw code is always shown next to the text.
const UNMATCHED_TEXT: Record<string, string> = {
  no_cpf: "a Câmara não informa o CPF",
  cpf_invalid: "o CPF informado pela Câmara é inválido",
  cpf_untrusted: "o CPF só aparece em registro não confiável",
  cpf_not_in_people: "o CPF não aparece nas candidaturas do TSE",
  cpf_ambiguous: "o CPF aparece em mais de uma pessoa",
  cpf_match_but_birth_and_name_differ: "o CPF bate, mas nascimento e nome civil diferem",
  no_full_name: "o cadastro do Senado não tem nome completo",
  no_birth_date: "o cadastro do Senado não tem data de nascimento",
  homonyms: "há mais de uma pessoa com o mesmo nome e nascimento",
  name_found_birth_differs: "o nome foi encontrado, mas a data de nascimento é diferente",
  history_without_birth: "o histórico do TSE não tem data de nascimento",
  not_in_history: "não encontrado nas candidaturas do TSE",
  no_detail: "o cadastro do senador não pôde ser lido",
};

export function unmatchedText(identity: ParliamentarianIdentity): string {
  const code = identity.unmatchedReason;
  if (!code) {
    return identity.provenance
      ? "motivo não registrado"
      : "cadastro do parlamentar não coletado (só aparece nas listas de votação)";
  }
  return `${UNMATCHED_TEXT[code] ?? "motivo sem descrição"} (${code})`;
}

/** Link to /politico when the parliamentarian is tied to a TSE person, else the reason why not. */
export function PersonLinkOrReason({ identity }: { identity: ParliamentarianIdentity }) {
  if (identity.personId != null) {
    return (
      <Link href={`/politico/${identity.personId}`} className="mono-label hover:text-[var(--accent-2)]">
        ver ficha completa no TSE (candidaturas, bens, doações) →
      </Link>
    );
  }
  return (
    <span className="text-[12px] text-[var(--muted)]">
      Não vinculado à ficha do TSE: {unmatchedText(identity)}.
    </span>
  );
}

/** Collapsed plain-language definition shown next to a metric. */
export function Definition({ text, label = "como calculamos" }: { text: string; label?: string }) {
  return (
    <details className="mt-2 text-[11px] leading-snug text-[var(--muted)]">
      <summary className="cursor-pointer select-none text-[var(--muted-2)] hover:text-[var(--fg-2)]">
        {label}
      </summary>
      <p className="mt-1.5 max-w-prose">{text}</p>
    </details>
  );
}

export function voteHref(house: House, voteId: string): string {
  return `/votacoes/${house}/${encodeURIComponent(voteId)}`;
}

export function parliamentarianHref(house: House, id: number): string {
  return `/votacoes/parlamentares/${house}/${id}`;
}
