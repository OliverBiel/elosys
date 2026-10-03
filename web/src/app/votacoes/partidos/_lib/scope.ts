// URL <-> VoteScope for the party and comparison pages (searchParams: casa, periodo, base, proc).

import {
  listVotes,
  type AgreementBasis,
  type PeriodFilter,
  type VoteListItem,
  type VoteScope,
} from "@/lib/votes-queries";
import { LEGISLATURES, type House } from "@/lib/vote-meaning";

export type SearchParams = Record<string, string | string[] | undefined>;

export const one = (v: string | string[] | undefined): string | undefined =>
  typeof v === "string" ? v : Array.isArray(v) ? v[0] : undefined;

export const many = (v: string | string[] | undefined): string[] =>
  v == null ? [] : (Array.isArray(v) ? v : [v]).filter((x) => x !== "");

export type PeriodOption = { value: string; label: string; filter: PeriodFilter };

/** Legislatures first (each one maps to a single government: no vote in Jan 2019/Jan 2023). */
export function periodOptions(years: number[]): PeriodOption[] {
  const legs = [...LEGISLATURES].reverse().map((l) => ({
    value: `leg${l.id}`,
    label: l.label,
    filter: { kind: "legislature", legislature: l.id } as PeriodFilter,
  }));
  return [
    ...legs,
    { value: "tudo", label: "Todo o período (2019–hoje)", filter: { kind: "all" } },
    ...years.map((y) => ({ value: String(y), label: String(y), filter: { kind: "year", year: y } as PeriodFilter })),
  ];
}

export const DEFAULT_PERIOD = `leg${LEGISLATURES[LEGISLATURES.length - 1].id}`;

export type PartyPageState = {
  house: House;
  period: PeriodOption;
  basis: AgreementBasis;
  procedural: boolean;
};

// Senado default = votos reais: its party orientation is published only for part of the votes.
export function defaultBasis(house: House): AgreementBasis {
  return house === "senado" ? "majority" : "orientation";
}

export function parseState(sp: SearchParams, years: number[]): PartyPageState {
  const house: House = one(sp.casa) === "senado" ? "senado" : "camara";
  const options = periodOptions(years);
  const period =
    options.find((o) => o.value === one(sp.periodo)) ?? options.find((o) => o.value === DEFAULT_PERIOD)!;
  const b = one(sp.base);
  const basis: AgreementBasis =
    b === "orientacao" ? "orientation" : b === "maioria" ? "majority" : defaultBasis(house);
  return {
    house,
    period,
    basis,
    procedural: one(sp.proc) === "1",
  };
}

export function scopeOf(s: PartyPageState, theme?: number): VoteScope {
  return { period: s.period.filter, includeProcedural: s.procedural, theme };
}

/** Query string for a state; values equal to the defaults are left out. */
export function stateQuery(s: PartyPageState, extra: Record<string, string | string[] | undefined> = {}) {
  const usp = new URLSearchParams();
  if (s.house !== "camara") usp.set("casa", s.house);
  if (s.period.value !== DEFAULT_PERIOD) usp.set("periodo", s.period.value);
  if (s.basis !== defaultBasis(s.house)) usp.set("base", s.basis === "orientation" ? "orientacao" : "maioria");
  if (s.procedural) usp.set("proc", "1");
  for (const [k, v] of Object.entries(extra)) {
    for (const x of many(v)) usp.append(k, x);
  }
  const q = usp.toString();
  return q ? `?${q}` : "";
}

export const BASIS_LABEL: Record<AgreementBasis, string> = {
  orientation: "orientação oficial do partido",
  majority: "voto da maioria da bancada",
};

// Vote rows of the party/comparison queries carry no proposition, ementa or provenance;
// listVotes has them. One query by id (callers pass one page: <= VOTES_PAGE_SIZE ids).
export function voteIndex(house: House, scope: VoteScope, ids: string[]): Map<string, VoteListItem> {
  if (ids.length === 0) return new Map();
  return new Map(listVotes({ house, ...scope, ids }).rows.map((r) => [r.id, r]));
}

export const voteHref = (house: House, id: string) => `/votacoes/${house}/${encodeURIComponent(id)}`;
export const memberHref = (house: House, id: number) => `/votacoes/parlamentares/${house}/${id}`;
export const partyHref = (party: string, s: PartyPageState) =>
  `/votacoes/partidos/${encodeURIComponent(party)}${stateQuery(s)}`;
