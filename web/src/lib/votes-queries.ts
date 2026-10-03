// Roll-call votes (Câmara + Senado). Every number here is computed at query time from the
// per-row tables of elosys/schema.sql; nothing aggregated is stored (ADs/votacoes.md §8).
// Metric rules are the DEFINITIONS strings in vote-meaning.ts; each implementation cites its key.

import { db, foldText, hasColumn, hasTable } from "./db";
import { PROVENANCE_COLUMNS, PROVENANCE_JOIN, pickProvenance, type Provenance } from "./queries";
import {
  DEFINITIONS,
  GOVERNMENT_PERIODS,
  LEGISLATURES,
  POSITION_VALUES,
  governmentAt,
  indeterminateLabel,
  isOrientedValue,
  isPositionValue,
  kindLabel,
  majorityLabel,
  meaningSentence,
  orientationLabel,
  voteValueLabel,
  type GovernmentId,
  type GovernmentPeriod,
  type House,
  type IndeterminateReason,
  type MajorityPosition,
  type OrientationValue,
  type OrientedValue,
  type PositionValue,
  type VoteValue,
} from "./vote-meaning";

export { DEFINITIONS };

type Row = Record<string, unknown>;

const str = (v: unknown): string | null => (v == null ? null : String(v));
const num = (v: unknown): number | null => (v == null ? null : Number(v));

// ---------------------------------------------------------------- house wiring

const H = {
  camara: {
    vote: "camara_vote",
    id: "camara_vote_id",
    cast: "camara_vote_cast",
    member: "camara_deputy_id",
    orient: "camara_vote_orientation",
  },
  senado: {
    vote: "senate_vote",
    id: "senate_vote_code",
    cast: "senate_vote_cast",
    member: "senator_code",
    orient: "senate_vote_orientation",
  },
} as const;

const HOUSE_TABLES: Record<House, string[]> = {
  camara: [
    "camara_vote", "camara_vote_cast", "camara_vote_orientation", "camara_vote_party_bench",
    "camara_vote_proposition", "camara_proposition_theme", "camara_session_presence",
    "camara_deputy", "camara_deputy_term",
  ],
  senado: ["senate_vote", "senate_vote_cast", "senate_vote_orientation", "senate_senator"],
};

/** schema.sql creates each house's tables together; an older .db has none of them. */
export function votesAvailable(house: House): boolean {
  return HOUSE_TABLES[house].every((t) => hasTable(t));
}

// Cast-list party values that are not a party: never a bench, never in party aggregates.
// Câmara 'S.PART.' (sem partido); Senado 'S/Partido', 'Sem registro' (mid party switch).
export const NO_PARTY_ABBRS = ["S.PART.", "S/Partido", "Sem registro"] as const;
const NO_PARTY_SQL = `(${NO_PARTY_ABBRS.map((p) => `'${p}'`).join(", ")})`;

export function isRealParty(p: string | null | undefined): p is string {
  return p != null && p !== "" && !(NO_PARTY_ABBRS as readonly string[]).includes(p);
}

// ---------------------------------------------------------------- period / scope

export type PeriodFilter =
  | { kind: "all" }
  | { kind: "legislature"; legislature: number }
  | { kind: "government"; government: GovernmentId }
  | { kind: "year"; year: number };

/** Inclusive YYYY-MM-DD bounds of a period (legislature dates in vote-meaning LEGISLATURES). */
export function periodRange(p: PeriodFilter | undefined): { from: string; to: string } {
  if (!p || p.kind === "all") return { from: "0000-01-01", to: "9999-12-31" };
  if (p.kind === "year") return { from: `${p.year}-01-01`, to: `${p.year}-12-31` };
  if (p.kind === "legislature") {
    const l = LEGISLATURES.find((x) => x.id === p.legislature);
    return l ? { from: l.start, to: l.end } : { from: "9999-12-31", to: "0000-01-01" };
  }
  const g = GOVERNMENT_PERIODS.find((x) => x.id === p.government);
  return g ? { from: g.start, to: g.end ?? "9999-12-31" } : { from: "9999-12-31", to: "0000-01-01" };
}

export type VoteScope = {
  period?: PeriodFilter;
  /** Default false: the default view is "mérito" (every kind except 'procedural'). */
  includeProcedural?: boolean;
  year?: number;
  /** Câmara theme code (camara_proposition_theme.theme_code); the Senado has no themes. */
  theme?: number;
};

type Sql = { sql: string; args: unknown[] };

function scopeWhere(house: House, s: VoteScope): Sql {
  let { from, to } = periodRange(s.period);
  if (s.year != null) {
    from = from > `${s.year}-01-01` ? from : `${s.year}-01-01`;
    to = to < `${s.year}-12-31` ? to : `${s.year}-12-31`;
  }
  const parts = ["v.date BETWEEN ? AND ?"];
  const args: unknown[] = [from, to];
  if (!s.includeProcedural) parts.push("coalesce(v.kind, 'other') <> 'procedural'");
  if (s.theme != null) {
    if (house === "senado") {
      parts.push("0");
    } else {
      parts.push(
        `EXISTS (SELECT 1 FROM camara_vote_proposition vp
                 JOIN camara_proposition_theme th ON th.proposition_id = vp.proposition_id
                 WHERE vp.camara_vote_id = v.camara_vote_id AND th.theme_code = ?)`
      );
      args.push(s.theme);
    }
  }
  return { sql: parts.join(" AND "), args };
}

// ---------------------------------------------------------------- shared value types

export type Ratio = { agree: number; n: number; pct: number | null };

function ratio(agree: number, n: number): Ratio {
  return { agree, n, pct: n > 0 ? Math.round((1000 * agree) / n) / 10 : null };
}

/** DEFINITIONS.government per government; null = no vote of that government in scope. */
export type GovernmentRatios = { bolsonaro: Ratio | null; lula: Ratio | null };

type GovTally = Record<GovernmentId, { agree: number; n: number; seen: boolean }>;

const govTally = (): GovTally => ({
  bolsonaro: { agree: 0, n: 0, seen: false },
  lula: { agree: 0, n: 0, seen: false },
});

const noGovRatios = (): GovernmentRatios => ({ bolsonaro: null, lula: null });

function govRatios(t: GovTally): GovernmentRatios {
  const one = (x: GovTally[GovernmentId]) => (x.seen ? ratio(x.agree, x.n) : null);
  return { bolsonaro: one(t.bolsonaro), lula: one(t.lula) };
}

/** Marks a vote of government `gid` as seen; counts it when `same` is not null. */
function tally(t: GovTally, gid: GovernmentId | undefined, same: boolean | null): void {
  if (!gid) return;
  t[gid].seen = true;
  if (same === null) return;
  t[gid].n += 1;
  if (same) t[gid].agree += 1;
}

/** DEFINITIONS.fidelity: Senado obstruction orientation is out (obstructors show as P-NRV). */
function fidelityOrientation(house: House, o: OrientationValue | null | undefined): o is OrientedValue {
  return isOrientedValue(o) && !(house === "senado" && o === "obstruction");
}

/** DEFINITIONS.majorityPosition: a Senado party that oriented obstruction has no countable majority. */
function majorityCounts(house: House, partyOrientation: OrientationValue | null | undefined): boolean {
  return !(house === "senado" && partyOrientation === "obstruction");
}

export type PositionCounts = Record<PositionValue, number>;

const emptyCounts = (): PositionCounts => ({ yes: 0, no: 0, obstruction: 0, abstention: 0 });

/** DEFINITIONS.majorityPosition: most frequent position among members who voted; tie = split. */
export function majorityOf(c: PositionCounts): MajorityPosition | null {
  let best = 0;
  let winners: PositionValue[] = [];
  for (const v of POSITION_VALUES) {
    const n = c[v];
    if (n > best) {
      best = n;
      winners = [v];
    } else if (n === best && n > 0) {
      winners.push(v);
    }
  }
  if (best === 0) return null;
  return winners.length > 1 ? "split" : winners[0];
}

export type VoteResult = {
  approved: boolean | null;
  label: string;
  raw: string | null;
  note: string | null;
  /** Senado destaque: the label comes from the tally, not from resultadoVotacao. */
  destaque: boolean;
  /** Senado: the paragraph of senate_vote.informe_text that carries this vote's tally. */
  officialText: string | null;
};

// Columns read by camaraResultOf(). rTypes only where the PEC/PLP quorum note can apply.
const CAMARA_RESULT_COLS = `v.approved AS approved, v.description AS rDesc, v.yes_count AS rYes,
  v.no_count AS rNo,
  CASE WHEN v.approved IS NULL AND v.description LIKE 'Suprimid%' AND v.yes_count > v.no_count
    THEN (SELECT group_concat(DISTINCT vp.type_abbr) FROM camara_vote_proposition vp
          WHERE vp.camara_vote_id = v.camara_vote_id) END AS rTypes`;

// '|'-joined proposition labels of a Câmara vote (alias v); split with splitProps().
const CAMARA_PROPS_SQL = `(SELECT group_concat(coalesce(vp.title,
    vp.type_abbr || ' ' || vp.number || '/' || vp.year), '|')
  FROM camara_vote_proposition vp WHERE vp.camara_vote_id = v.camara_vote_id)`;

const splitProps = (v: unknown): string[] => (v ? String(v).split("|") : []);

const camaraResultOf = (r: Row): VoteResult =>
  camaraResult(r.approved, str(r.rDesc), num(r.rYes), num(r.rNo), str(r.rTypes)?.split(",") ?? []);

// DVS votes ("Mantido/Suprimido o texto") come with aprovacao NULL: the outcome is in the
// description. Keeping a PEC/PLP passage needs 308/257 Sim, so Sim > Não can still drop it.
function camaraResult(
  approved: unknown,
  description: string | null,
  yes: number | null,
  no: number | null,
  propositionTypes: string[]
): VoteResult {
  const a = num(approved);
  if (a == null && description) {
    if (/^mantid/i.test(description)) {
      return { approved: null, label: "Trecho mantido", raw: null, note: null, destaque: false, officialText: null };
    }
    if (/^suprimid/i.test(description)) {
      let note: string | null = null;
      if (yes != null && no != null && yes > no) {
        if (propositionTypes.includes("PEC")) {
          note = "Em PEC, manter o trecho exige 308 votos Sim, não só mais Sim que Não.";
        } else if (propositionTypes.includes("PLP")) {
          note =
            "Em projeto de lei complementar, manter o trecho exige 257 votos Sim, não só mais " +
            "Sim que Não.";
        }
      }
      return { approved: null, label: "Trecho retirado", raw: null, note, destaque: false, officialText: null };
    }
  }
  // 2613731-65: aprovacao=1 on a 182 x 182 tie; the source is kept, the reader is warned.
  const tie = a === 1 && yes != null && yes === no;
  return {
    approved: a == null ? null : a === 1,
    label: a == null ? "Resultado não informado" : a === 1 ? "Aprovado" : "Rejeitado",
    raw: a == null ? null : String(a),
    note: tie ? "Empate no placar; a fonte marca como aprovado. Confira a descrição oficial." : null,
    destaque: false,
    officialText: null,
  };
}

// SQL twin of senadoCamaraSubstitute(): fold() is the db.ts function, GLOB is case-sensitive.
const SENADO_CAMARA_SUBSTITUTE_SQL = `v.description LIKE '%destacad%' AND (
    fold(v.identification) GLOB '*substitutivo-cd*' OR fold(v.identification) || ' ' GLOB 'scd[^a-z0-9_]*'
    OR fold(v.description) || ' ' GLOB '*substitutivo da camara[^a-z0-9_]*'
    OR fold(v.description) || ' ' GLOB '*substitutivo da cd[^a-z0-9_]*')`;

/**
 * meaning_code as shown (alias v). vote_kind.py 1.4 stores NULL for a Senado destaque on a Câmara
 * substitute; this also covers .db files classified before it (6174 had "Sim = manter").
 */
function meaningCol(house: House): string {
  if (house === "camara") return "v.meaning_code";
  return `CASE WHEN ${SENADO_CAMARA_SUBSTITUTE_SQL} THEN NULL ELSE v.meaning_code END`;
}

// Columns read by senadoResultOf() (alias v). Sim/Não and informe only for destaques.
// informe_text arrives with the senado-votes re-collection; older .db files lack it.
function senadoResultCols(): string {
  const outcome = hasColumn("senate_vote", "informe_text")
    ? "CASE WHEN v.description LIKE '%destacad%' THEN v.informe_text END"
    : "NULL";
  const count = (value: string, col: string) =>
    `CASE WHEN v.description LIKE '%destacad%' THEN coalesce(v.${col}, (SELECT count(*)
       FROM senate_vote_cast sc WHERE sc.senate_vote_code = v.senate_vote_code
         AND sc.vote = '${value}')) END`;
  return `v.result_raw AS resultRaw, ${meaningCol("senado")} AS rMeaning, v.description AS rDesc,
    v.identification AS rIdent, ${outcome} AS rOutcome,
    ${count("yes", "yes_count")} AS rYes, ${count("no", "no_count")} AS rNo`;
}

const senadoResultOf = (r: Row): VoteResult =>
  senadoResult({
    raw: str(r.resultRaw), meaning: str(r.rMeaning), description: str(r.rDesc),
    identification: str(r.rIdent), yes: num(r.rYes), no: num(r.rNo), informeText: str(r.rOutcome),
  });

/**
 * informe_text narrates the whole session for the matter (every vote of it): keep only the
 * paragraph with this vote's tally ("Rejeitada a Emenda nº 12 ... Sim - 12; Não - 50").
 */
export function informeExcerpt(text: string | null, yes: number | null, no: number | null): string | null {
  if (!text || yes == null || no == null) return null;
  const re = new RegExp(String.raw`sim\D{0,12}${yes}\b[\s\S]{0,40}?n[aã]o\D{0,12}${no}\b`, "i");
  return text.split(/[\r\n]+/).map((x) => x.trim()).find((x) => re.test(x)) ?? null;
}

// Senado quorum for keeping a passage / approving an amendment (3/5 and absolute majority).
const SENADO_QUORUM: Record<string, { n: number; what: string }> = {
  PEC: { n: 49, what: "PEC" },
  PLP: { n: 41, what: "projeto de lei complementar" },
};

// Amendment wording that vote_kind.py <= 1.2 read as "keep the text" (6166, 6335, 6881).
const AMENDMENT_DESC = /^(votacao( nominal)?( conjunta)? d[ae]s? )?((primeira )?parte d[ae] )?(sub)?emendas? /;

const RAW_RESULT: Record<string, string> = { A: "aprovado", R: "rejeitado" };

// A destaque on a Câmara substitute picks between the Senado and Câmara versions, so what Sim
// does is unsure (6174: Sim 52 x 22 removed the art. 17-E). `desc` is foldText()ed.
function senadoCamaraSubstitute(desc: string, identification: string | null): boolean {
  return /substitutivo-cd|^scd\b/i.test(identification ?? "") || /substitutivo da (camara|cd)\b/.test(desc);
}

// ADs/votacoes.md §5: on a destaque, resultadoVotacao is the fate of the destaque request,
// not of the text (6321: R, yet "Aprovado o texto" 36 x 31). The outcome comes from the tally.
function senadoResult(x: {
  raw: string | null;
  meaning: string | null;
  description: string | null;
  identification: string | null;
  yes: number | null;
  no: number | null;
  informeText: string | null;
}): VoteResult {
  const r = x.raw;
  const officialText = informeExcerpt(x.informeText, x.yes, x.no);
  const base = { raw: r, destaque: false, officialText };
  if (!/destacad[oa]s?\b/i.test(x.description ?? "")) {
    if (r === "A") return { ...base, approved: true, label: "Aprovado", note: null };
    if (r === "R") return { ...base, approved: false, label: "Rejeitado", note: null };
    return { ...base, approved: null, label: r ? `Resultado: ${r}` : "Resultado não informado", note: null };
  }
  const desc = foldText(x.description ?? "");
  const camaraSubstitute = senadoCamaraSubstitute(desc, x.identification);
  const keep = x.meaning === "keep_highlighted_text" && !AMENDMENT_DESC.test(desc) && !camaraSubstitute;
  const amendment = x.meaning === "approve_amendment" && !camaraSubstitute;
  const quorum = SENADO_QUORUM[(x.identification ?? "").trim().split(/[\s/]/)[0].toUpperCase()];
  // A tie or a missing tally (or an unsure meaning) gives no certain outcome.
  if ((keep || amendment) && x.yes != null && x.no != null && x.yes !== x.no) {
    const passes = x.yes > x.no && (!quorum || x.yes >= quorum.n);
    const what = keep ? "manter o trecho" : "aprovar a emenda";
    const note = !passes && x.yes > x.no && quorum
      ? `Em ${quorum.what}, ${what} exige ${quorum.n} votos Sim, não só mais Sim que Não.`
      : null;
    if (keep) {
      return { ...base, destaque: true, approved: null, label: passes ? "Trecho mantido" : "Trecho retirado", note };
    }
    return { ...base, destaque: true, approved: passes, label: passes ? "Emenda aprovada" : "Emenda rejeitada", note };
  }
  const rawText = r ? `"${r}"${RAW_RESULT[r] ? ` (${RAW_RESULT[r]})` : ""}` : "nenhum código";
  return {
    ...base,
    destaque: true,
    approved: null,
    label: "Resultado do destaque: ver registro oficial",
    note: officialText
      ? null
      : `O Senado registra ${rawText}, mas num destaque isso fala do pedido de destaque, não do texto.`,
  };
}

export type GovernmentOrientation = {
  government: GovernmentPeriod | null;
  value: OrientationValue | null;
  raw: string | null;
  label: string;
};

function governmentOrientation(
  house: House,
  date: string,
  rows: Array<{ value: string; raw: string }> | undefined
): GovernmentOrientation {
  const government = governmentAt(date);
  if (!rows || rows.length === 0) {
    const label = house === "senado" ? "Sem orientação publicada" : "Sem orientação registrada";
    return { government, value: null, raw: null, label };
  }
  const values = new Set(rows.map((r) => r.value));
  const raw = rows.map((r) => r.raw).join(" / ");
  // Two Governo rows that disagree: no orientation is asserted.
  if (values.size > 1) {
    return { government, value: null, raw, label: `Orientações conflitantes (${raw})` };
  }
  const value = rows[0].value as OrientationValue;
  return { government, value, raw: rows[0].raw, label: orientationLabel(value, house, rows[0].raw) };
}

export type PartyOrientation = {
  value: OrientationValue | null;
  raw: string | null;
  label: string;
  /** Câmara: bench that oriented the party; Senado: the panel label. */
  benchLabel: string | null;
  /** Câmara only: party | federation | bloc_api | bloc_curated | bloc_label. */
  matchBasis: string | null;
  /** Câmara: ambiguous | bloc_unresolved | no_bench_row | no_party; Senado: ambiguous. */
  unmatchedReason: string | null;
};

function noOrientation(house: House, voteHasOrientations = true): PartyOrientation {
  return {
    value: null, raw: null, label: orientationLabel(null, house, null, voteHasOrientations),
    benchLabel: null, matchBasis: null, unmatchedReason: null,
  };
}

// ---------------------------------------------------------------- shared loaders

type VoteFilter = { from: string; where: string; args: unknown[] };

/** `FROM <vote> v WHERE <scope>` for a house + scope. */
function scopeFilter(house: House, s: VoteScope): VoteFilter {
  const w = scopeWhere(house, s);
  return { from: `${H[house].vote} v`, where: w.sql, args: w.args };
}

function oneVoteFilter(house: House, voteId: string): VoteFilter {
  return {
    from: `${H[house].vote} v`,
    where: `v.${H[house].id} = ?`,
    args: [house === "senado" ? Number(voteId) : voteId],
  };
}

/** (vote, party) -> position counts; members without a real party are left out. */
function loadPartyCounts(
  house: House,
  f: VoteFilter,
  extra?: Sql
): Map<string, Map<string, PositionCounts>> {
  const h = H[house];
  const rows = db()
    .prepare(
      `SELECT c.${h.id} AS voteId, c.party_abbr AS party, c.vote AS value, count(*) AS n
       FROM ${f.from} JOIN ${h.cast} c ON c.${h.id} = v.${h.id}
       WHERE ${f.where} AND c.vote IN ('yes', 'no', 'obstruction', 'abstention')
         AND c.party_abbr IS NOT NULL AND c.party_abbr NOT IN ${NO_PARTY_SQL}
         ${extra ? `AND ${extra.sql}` : ""}
       GROUP BY 1, 2, 3`
    )
    .all(...f.args, ...(extra?.args ?? [])) as Row[];
  const out = new Map<string, Map<string, PositionCounts>>();
  for (const r of rows) {
    const vid = String(r.voteId);
    let byParty = out.get(vid);
    if (!byParty) out.set(vid, (byParty = new Map()));
    const party = r.party as string;
    let c = byParty.get(party);
    if (!c) byParty.set(party, (c = emptyCounts()));
    c[r.value as PositionValue] = r.n as number;
  }
  return out;
}

/** (vote, party) -> the party's own orientation. Câmara via camara_vote_party_bench. */
function loadPartyOrientations(
  house: House,
  f: VoteFilter,
  party?: string
): Map<string, Map<string, PartyOrientation>> {
  const out = new Map<string, Map<string, PartyOrientation>>();
  const put = (vid: string, p: string, o: PartyOrientation) => {
    let m = out.get(vid);
    if (!m) out.set(vid, (m = new Map()));
    const prev = m.get(p);
    // Senado: two panel labels normalized to one party ('PRB' + 'Republicanos') must agree.
    if (prev && prev.value !== o.value) {
      m.set(p, { ...noOrientation(house), unmatchedReason: "ambiguous" });
    } else if (!prev) {
      m.set(p, o);
    }
  };
  if (house === "camara") {
    const rows = db()
      .prepare(
        `SELECT pb.camara_vote_id AS voteId, pb.party_abbr AS party, pb.bench_label AS benchLabel,
                pb.match_basis AS matchBasis, pb.unmatched_reason AS unmatchedReason,
                o.orientation AS value, o.orientation_raw AS raw,
                EXISTS (SELECT 1 FROM camara_vote_orientation ao
                        WHERE ao.camara_vote_id = v.camara_vote_id) AS anyOrientation
         FROM ${f.from}
         JOIN camara_vote_party_bench pb ON pb.camara_vote_id = v.camara_vote_id
         LEFT JOIN camara_vote_orientation o
           ON o.camara_vote_id = pb.camara_vote_id AND o.bench_label = pb.bench_label
         WHERE ${f.where} ${party ? "AND pb.party_abbr = ?" : ""}`
      )
      .all(...f.args, ...(party ? [party] : [])) as Row[];
    for (const r of rows) {
      const value = (str(r.value) as OrientationValue | null) ?? null;
      put(String(r.voteId), r.party as string, {
        value,
        raw: str(r.raw),
        label: orientationLabel(value, "camara", str(r.raw), !!r.anyOrientation),
        benchLabel: str(r.benchLabel),
        matchBasis: str(r.matchBasis),
        unmatchedReason: str(r.unmatchedReason),
      });
    }
    return out;
  }
  const rows = db()
    .prepare(
      `SELECT o.senate_vote_code AS voteId, o.bench_norm AS party, o.bench_label AS benchLabel,
              o.orientation AS value, o.orientation_raw AS raw
       FROM ${f.from} JOIN senate_vote_orientation o ON o.senate_vote_code = v.senate_vote_code
       WHERE ${f.where} AND o.bench_kind = 'party' AND o.bench_norm IS NOT NULL
         ${party ? "AND o.bench_norm = ?" : ""}`
    )
    .all(...f.args, ...(party ? [party] : [])) as Row[];
  for (const r of rows) {
    const value = r.value as OrientationValue;
    put(String(r.voteId), r.party as string, {
      value,
      raw: str(r.raw),
      label: orientationLabel(value, "senado", str(r.raw)),
      benchLabel: str(r.benchLabel),
      matchBasis: null,
      unmatchedReason: null,
    });
  }
  return out;
}

/** vote -> Governo orientation (DEFINITIONS.government), labelled by the vote date. */
function loadGovernment(house: House, f: VoteFilter): Map<string, GovernmentOrientation> {
  const h = H[house];
  const rows = db()
    .prepare(
      `SELECT v.${h.id} AS voteId, v.date, o.orientation AS value, o.orientation_raw AS raw
       FROM ${f.from} LEFT JOIN ${h.orient} o
         ON o.${h.id} = v.${h.id} AND o.bench_kind = 'government'
       WHERE ${f.where}`
    )
    .all(...f.args) as Row[];
  const grouped = new Map<string, { date: string; rows: Array<{ value: string; raw: string }> }>();
  for (const r of rows) {
    const vid = String(r.voteId);
    let g = grouped.get(vid);
    if (!g) grouped.set(vid, (g = { date: r.date as string, rows: [] }));
    if (r.value != null) g.rows.push({ value: r.value as string, raw: (r.raw as string) ?? "" });
  }
  const out = new Map<string, GovernmentOrientation>();
  for (const [vid, g] of grouped) out.set(vid, governmentOrientation(house, g.date, g.rows));
  return out;
}

/** Distinct provenance of the rows of `table` (alias t) matching `where`. */
function provenanceOf(table: string, where: string, args: unknown[]): Provenance[] {
  const rows = db()
    .prepare(
      `SELECT ${PROVENANCE_COLUMNS}
       FROM (SELECT DISTINCT provenance_id FROM ${table} WHERE ${where}) t
       ${PROVENANCE_JOIN}`
    )
    .all(...args) as Row[];
  return rows.map(pickProvenance);
}

// ---------------------------------------------------------------- vote lists

export type VoteListItem = {
  house: House;
  id: string;
  date: string;
  description: string | null;
  kind: string | null;
  kindLabel: string;
  meaningCode: string | null;
  meaning: string;
  isSecret: boolean;
  /** Câmara: proposition titles ("PL 2215/2024"); Senado: identificacao ("PLP 192/2023"). */
  propositions: string[];
  ementa: string | null;
  result: VoteResult;
  /** Câmara: votosSim/votosNao; Senado: official totals, else counted from the cast list. */
  yes: number | null;
  no: number | null;
  provenance: Provenance;
};

export type VoteListPage = { rows: VoteListItem[]; total: number; page: number; pageSize: number };

export const VOTES_PAGE_SIZE = 50;

export type ListVotesOptions = VoteScope & {
  house: House;
  q?: string;
  page?: number;
  /** Only these vote ids (at most VOTES_PAGE_SIZE: one page, no paging). */
  ids?: string[];
};

export function listVotes(opts: ListVotesOptions): VoteListPage {
  // ?page=abc reaches here as NaN: LIMIT/OFFSET NaN would throw.
  const asked = Number(opts.page);
  const page = Number.isFinite(asked) && asked >= 1 ? Math.floor(asked) : 1;
  const empty: VoteListPage = { rows: [], total: 0, page, pageSize: VOTES_PAGE_SIZE };
  if (!votesAvailable(opts.house)) return empty;
  const house = opts.house;
  const w = scopeWhere(house, opts);
  const q = opts.q?.trim() ?? "";
  let qSql = "";
  const qArgs: unknown[] = [];
  if (q) {
    // fold() (db.ts): "tributario" finds "Tributário"; LIKE alone folds ASCII case only.
    const like = `%${foldText(q)}%`;
    if (house === "camara") {
      qSql = `AND (v.camara_vote_id = ? OR fold(v.description) LIKE ? OR fold(v.opening_description) LIKE ?
               OR EXISTS (SELECT 1 FROM camara_vote_proposition vp
                          WHERE vp.camara_vote_id = v.camara_vote_id
                            AND (fold(vp.title) LIKE ? OR fold(vp.ementa) LIKE ?)))`;
      qArgs.push(q, like, like, like, like);
    } else {
      qSql = `AND (CAST(v.senate_vote_code AS TEXT) = ? OR fold(v.identification) LIKE ?
               OR fold(v.description) LIKE ? OR fold(v.ementa) LIKE ?)`;
      qArgs.push(q, like, like, like);
    }
  }
  if (opts.ids) {
    qSql += ` AND v.${H[house].id} IN (SELECT value FROM json_each(?))`;
    qArgs.push(JSON.stringify(house === "senado" ? opts.ids.map(Number) : opts.ids));
  }
  const h = H[house];
  const base = `FROM ${h.vote} v WHERE ${w.sql} ${qSql}`;
  const total = (db().prepare(`SELECT count(*) AS n ${base}`).get(...w.args, ...qArgs) as { n: number }).n;
  const extraCols =
    house === "camara"
      ? `${CAMARA_RESULT_COLS}, v.yes_count AS yes, v.no_count AS no, NULL AS ementa,
         ${CAMARA_PROPS_SQL} AS props,
         (SELECT vp.ementa FROM camara_vote_proposition vp
          WHERE vp.camara_vote_id = v.camara_vote_id ORDER BY vp.id LIMIT 1) AS firstEmenta`
      : `${senadoResultCols()}, v.ementa AS ementa, v.identification AS props,
         coalesce(v.yes_count, (SELECT count(*) FROM senate_vote_cast c
           WHERE c.senate_vote_code = v.senate_vote_code AND c.vote = 'yes')) AS yes,
         coalesce(v.no_count, (SELECT count(*) FROM senate_vote_cast c
           WHERE c.senate_vote_code = v.senate_vote_code AND c.vote = 'no')) AS no`;
  const order =
    house === "camara" ? "v.date DESC, v.registered_at DESC, v.id DESC" : "v.date DESC, v.senate_vote_code DESC";
  const rows = db()
    .prepare(
      `SELECT v.${h.id} AS id, v.date, v.description, v.kind, ${meaningCol(house)} AS meaningCode,
              v.is_secret AS isSecret, ${extraCols},
              ${PROVENANCE_COLUMNS}
       FROM ${h.vote} v
       JOIN parse pa ON pa.id = v.provenance_id
       JOIN collection col ON col.id = pa.collection_id
       JOIN source src ON src.id = col.source_id
       WHERE ${w.sql} ${qSql}
       ORDER BY ${order}
       LIMIT ? OFFSET ?`
    )
    .all(...w.args, ...qArgs, VOTES_PAGE_SIZE, opts.ids ? 0 : (page - 1) * VOTES_PAGE_SIZE) as Row[];
  return {
    total,
    page,
    pageSize: VOTES_PAGE_SIZE,
    rows: rows.map((r) => ({
      house,
      id: String(r.id),
      date: r.date as string,
      description: str(r.description),
      kind: str(r.kind),
      kindLabel: kindLabel(str(r.kind), str(r.meaningCode)),
      meaningCode: str(r.meaningCode),
      meaning: meaningSentence(str(r.meaningCode)),
      isSecret: !!r.isSecret,
      propositions: splitProps(r.props),
      ementa: str(r.ementa) ?? str(r.firstEmenta),
      result: house === "camara" ? camaraResultOf(r) : senadoResultOf(r),
      yes: num(r.yes),
      no: num(r.no),
      provenance: pickProvenance(r),
    })),
  };
}

export type ThemeItem = { code: number; name: string; votes: number };

/** Câmara themes with vote counts; same procedural filter as listVotes (default: mérito). */
export function listThemes(includeProcedural = false): ThemeItem[] {
  if (!votesAvailable("camara")) return [];
  const rows = db()
    .prepare(
      `SELECT th.theme_code AS code, max(th.theme) AS name, count(DISTINCT vp.camara_vote_id) AS votes
       FROM camara_proposition_theme th
       JOIN camara_vote_proposition vp ON vp.proposition_id = th.proposition_id
       JOIN camara_vote v ON v.camara_vote_id = vp.camara_vote_id
       ${includeProcedural ? "" : "WHERE coalesce(v.kind, 'other') <> 'procedural'"}
       GROUP BY th.theme_code
       ORDER BY name`
    )
    .all() as Row[];
  return rows.map((r) => ({ code: r.code as number, name: (r.name as string) ?? String(r.code), votes: r.votes as number }));
}

export function listYears(house: House): number[] {
  if (!votesAvailable(house)) return [];
  const rows = db()
    .prepare(`SELECT DISTINCT CAST(substr(date, 1, 4) AS INTEGER) AS y FROM ${H[house].vote} ORDER BY y DESC`)
    .all() as Array<{ y: number }>;
  return rows.map((r) => r.y);
}

export type PartyListItem = { party: string; votes: number; members: number };

/** Parties (as named at vote time) with at least one cast in scope. */
export function listParties(house: House, scope: VoteScope = {}): PartyListItem[] {
  if (!votesAvailable(house)) return [];
  const h = H[house];
  const f = scopeFilter(house, scope);
  const rows = db()
    .prepare(
      `SELECT c.party_abbr AS party, count(DISTINCT c.${h.id}) AS votes,
              count(DISTINCT c.${h.member}) AS members
       FROM ${f.from} JOIN ${h.cast} c ON c.${h.id} = v.${h.id}
       WHERE ${f.where} AND c.party_abbr IS NOT NULL AND c.party_abbr NOT IN ${NO_PARTY_SQL}
       GROUP BY c.party_abbr
       ORDER BY members DESC, party`
    )
    .all(...f.args) as Row[];
  return rows.map((r) => ({ party: r.party as string, votes: r.votes as number, members: r.members as number }));
}

export type OrientationCoverageYear = {
  year: number;
  votes: number;
  /** Votes with a party orientation Sim/Não/Obstrução/Liberado ("none" excluded). */
  votesWithPartyOrientation: number;
  pct: number | null;
};

/** Senado party-orientation coverage per year (DEFINITIONS.senadoOrientation). */
export function getSenadoOrientationCoverage(): OrientationCoverageYear[] {
  if (!votesAvailable("senado")) return [];
  const rows = db()
    .prepare(
      `SELECT CAST(substr(v.date, 1, 4) AS INTEGER) AS year, count(*) AS votes,
              sum(EXISTS (SELECT 1 FROM senate_vote_orientation o
                          WHERE o.senate_vote_code = v.senate_vote_code AND o.bench_kind = 'party'
                            AND o.orientation IN ('yes', 'no', 'obstruction', 'free'))) AS oriented
       FROM senate_vote v GROUP BY 1 ORDER BY 1`
    )
    .all() as Row[];
  return rows.map((r) => ({
    year: r.year as number,
    votes: r.votes as number,
    votesWithPartyOrientation: r.oriented as number,
    pct: ratio(r.oriented as number, r.votes as number).pct,
  }));
}

// ---------------------------------------------------------------- one vote

export type VoteProposition = {
  propositionId: number;
  typeAbbr: string | null;
  number: number | null;
  year: number | null;
  title: string | null;
  ementa: string | null;
  themes: Array<{ code: number; name: string | null }>;
  provenance: Provenance;
};

export type VoteOrientationRow = {
  benchLabel: string;
  benchKind: string;
  benchNorm: string | null;
  value: OrientationValue;
  raw: string;
  label: string;
  provenance: Provenance;
};

export type VoteCastRow = {
  memberId: number;
  name: string | null;
  party: string | null;
  state: string | null;
  value: VoteValue;
  raw: string;
  label: string;
  personId: number | null;
};

export type PartyBreakdown = {
  party: string;
  /** Every stored value of the party's cast rows (presiding, secret, Senado absences...). */
  counts: Partial<Record<VoteValue, number>>;
  listed: number;
  positions: PositionCounts;
  majority: MajorityPosition | null;
  /** majorityLabel() with the party's secret casts: "Posição desconhecida (voto secreto)". */
  majorityLabel: string;
  orientation: PartyOrientation;
  /** DEFINITIONS.fidelity on this vote; null when the orientation does not count for it. */
  followed: Ratio | null;
};

export type MemberRef = {
  memberId: number;
  name: string | null;
  /** Party of the deputy's latest cast in the same legislature up to this date; null if none. */
  lastParty: string | null;
  state: string | null;
  personId: number | null;
};

export type CamaraVotePresence = {
  /** false when camara_deputy_term is empty (camara-deputies not run): absence not assessed. */
  termsAvailable: boolean;
  eventId: number | null;
  /** Deputies in the session presence list (any, voted or not). */
  presenceListed: number;
  /** Deputies with an in_office term of the vote legislature covering the vote instant. */
  inOffice: number;
  /** In the session presence list, no cast: "Registrou presença na sessão, mas não votou". */
  sessionPresentNotVoted: MemberRef[];
  absent: MemberRef[];
  indeterminate: MemberRef[];
  /** One reason per vote: no presence list makes every in-office non-voter indeterminate. */
  indeterminateReason: IndeterminateReason | null;
  /** indeterminateLabel(indeterminateReason); null when nobody is indeterminate. */
  indeterminateLabel: string | null;
  /** /deputados/{id}/historico of the absent and indeterminate deputies (their in-office proof). */
  termSources: Provenance[];
};

export type VoteSourcesBySection = {
  casts: Provenance[];
  benches: Provenance[];
  presence: Provenance[];
  terms: Provenance[];
  propositions: Provenance[];
  themes: Provenance[];
};

export type VoteDetail = {
  house: House;
  id: string;
  date: string;
  registeredAt: string | null;
  legislature: number | null;
  description: string | null;
  openingDescription: string | null;
  identification: string | null;
  ementa: string | null;
  kind: string | null;
  kindLabel: string;
  kindBasis: string | null;
  meaningCode: string | null;
  meaning: string;
  isSecret: boolean;
  result: VoteResult;
  /** Câmara fills other (votosOutros), abstention null; Senado fills abstention, other null. */
  officialTotals: {
    yes: number | null;
    no: number | null;
    other: number | null;
    abstention: number | null;
  };
  castTotals: Partial<Record<VoteValue, number>>;
  /** Senado ids for senado.leg.br links (senate_vote.matter_code / session_code); Câmara null. */
  senate?: { matterCode: number | null; sessionCode: number | null } | null;
  government: GovernmentOrientation;
  propositions: VoteProposition[];
  orientations: VoteOrientationRow[];
  parties: PartyBreakdown[];
  casts: VoteCastRow[];
  presence: CamaraVotePresence | null;
  /** Senado: the cast list has no absence code at all, so a missing senator is "desconhecido". */
  listsOnlyVoters: boolean;
  /** Senado informeLegislativo.texto: the matter's record for the whole session; Câmara null. */
  informeText: string | null;
  provenance: Provenance;
  /** Provenance of casts, party benches and presence used for this vote. */
  sources: Provenance[];
  /** Keyed by page section, plus terms (absence rules); empty where the house has no such data. */
  sourcesBySection: VoteSourcesBySection;
};

export function getVote(house: House, id: string): VoteDetail | null {
  if (!votesAvailable(house)) return null;
  const h = H[house];
  const f = oneVoteFilter(house, id);
  const v = db()
    .prepare(
      `SELECT v.*, ${meaningCol(house)} AS shown_meaning, ${PROVENANCE_COLUMNS} FROM ${h.vote} t
       JOIN ${h.vote} v ON v.id = t.id
       ${PROVENANCE_JOIN}
       WHERE t.${h.id} = ?`
    )
    .get(...f.args) as Row | undefined;
  if (!v) return null;
  const voteKey = f.args[0];
  const date = v.date as string;

  const casts = loadCasts(house, voteKey);
  const castTotals: Partial<Record<VoteValue, number>> = {};
  for (const c of casts) castTotals[c.value] = (castTotals[c.value] ?? 0) + 1;

  const orientations = (
    db()
      .prepare(
        `SELECT t.bench_label AS benchLabel, t.bench_kind AS benchKind, t.bench_norm AS benchNorm,
                t.orientation AS value, t.orientation_raw AS raw, ${PROVENANCE_COLUMNS}
         FROM ${h.orient} t ${PROVENANCE_JOIN}
         WHERE t.${h.id} = ? ORDER BY t.id`
      )
      .all(voteKey) as Row[]
  ).map((r) => ({
    benchLabel: r.benchLabel as string,
    benchKind: r.benchKind as string,
    benchNorm: str(r.benchNorm),
    value: r.value as OrientationValue,
    raw: r.raw as string,
    label:
      r.benchKind === "government"
        ? `${governmentAt(date)?.label ?? "Governo"}: ${orientationLabel(r.value as string, house, str(r.raw))}`
        : orientationLabel(r.value as string, house, str(r.raw)),
    provenance: pickProvenance(r),
  }));

  const isCamara = house === "camara";
  const presence = isCamara ? camaraVotePresence(voteKey as string) : null;
  const partyOrient = loadPartyOrientations(house, f).get(String(voteKey)) ?? new Map();
  const parties = partyBreakdown(house, casts, partyOrient, orientations.length > 0);
  const government = loadGovernment(house, f).get(String(voteKey)) ?? governmentOrientation(house, date, []);
  const propositions = isCamara ? loadPropositions(voteKey as string) : [];

  const bySection: VoteSourcesBySection = {
    casts: provenanceOf(h.cast, `${h.id} = ?`, [voteKey]),
    benches: isCamara ? provenanceOf("camara_vote_party_bench", "camara_vote_id = ?", [voteKey]) : [],
    presence:
      isCamara && v.event_id != null
        ? provenanceOf("camara_session_presence", "event_id = ?", [v.event_id])
        : [],
    terms: presence?.termSources ?? [],
    propositions: dedupeProvenance(propositions.map((p) => p.provenance)),
    themes: isCamara
      ? provenanceOf(
          "camara_proposition_theme",
          `proposition_id IN (SELECT proposition_id FROM camara_vote_proposition
                              WHERE camara_vote_id = ?)`,
          [voteKey]
        )
      : [],
  };
  // terms (one /historico file per absent deputy) are listed under the absence block instead.
  const sources = [...bySection.casts, ...bySection.benches, ...bySection.presence];

  return {
    house,
    id: String(v[h.id]),
    date,
    registeredAt: isCamara ? str(v.registered_at) : null,
    legislature: isCamara ? num(v.legislature) : legislatureOf(date),
    description: str(v.description),
    openingDescription: isCamara ? str(v.opening_description) : null,
    identification: isCamara ? null : str(v.identification),
    ementa: isCamara ? null : str(v.ementa),
    kind: str(v.kind),
    kindLabel: kindLabel(str(v.kind), str(v.shown_meaning)),
    kindBasis: str(v.kind_basis),
    meaningCode: str(v.shown_meaning),
    meaning: meaningSentence(str(v.shown_meaning)),
    isSecret: !!v.is_secret,
    result: isCamara
      ? camaraResult(
          v.approved, str(v.description), num(v.yes_count), num(v.no_count),
          propositions.map((p) => p.typeAbbr ?? "")
        )
      : senadoResult({
          raw: str(v.result_raw), meaning: str(v.shown_meaning), description: str(v.description),
          identification: str(v.identification),
          yes: num(v.yes_count) ?? castTotals.yes ?? 0, no: num(v.no_count) ?? castTotals.no ?? 0,
          informeText: str(v.informe_text),
        }),
    officialTotals: {
      yes: num(v.yes_count),
      no: num(v.no_count),
      other: isCamara ? num(v.other_count) : null,
      abstention: isCamara ? null : num(v.abstention_count),
    },
    castTotals,
    senate: isCamara ? null : { matterCode: num(v.matter_code), sessionCode: num(v.session_code) },
    government,
    propositions,
    orientations,
    parties,
    casts,
    presence,
    // Same test as the crawler's votes_listing_only_voters (elosys/senado/votes.py ABSENCE_VOTES).
    listsOnlyVoters:
      !isCamara && casts.length > 0 && !casts.some((c) => ["absent", "absent_justified", "other"].includes(c.value)),
    informeText: isCamara ? null : str(v.informe_text),
    provenance: pickProvenance(v),
    sources: dedupeProvenance(sources),
    sourcesBySection: bySection,
  };
}

function legislatureOf(date: string): number | null {
  return LEGISLATURES.find((l) => date >= l.start && date <= l.end)?.id ?? null;
}

function dedupeProvenance(list: Provenance[]): Provenance[] {
  const seen = new Set<string>();
  return list.filter((p) => {
    const k = `${p.url}|${p.sha256}|${p.parserName}`;
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
}

function loadCasts(house: House, voteKey: unknown): VoteCastRow[] {
  const h = H[house];
  const person =
    house === "camara"
      ? "LEFT JOIN camara_deputy d ON d.camara_deputy_id = c.camara_deputy_id"
      : "LEFT JOIN senate_senator d ON d.senator_code = c.senator_code";
  const rows = db()
    .prepare(
      `SELECT c.${h.member} AS memberId, c.name, c.party_abbr AS party, c.state, c.vote AS value,
              c.vote_raw AS raw, d.person_id AS personId
       FROM ${h.cast} c ${person}
       WHERE c.${h.id} = ?
       ORDER BY c.party_abbr, c.name`
    )
    .all(voteKey) as Row[];
  return rows.map((r) => ({
    memberId: r.memberId as number,
    name: str(r.name),
    party: str(r.party),
    state: str(r.state),
    value: r.value as VoteValue,
    raw: r.raw as string,
    label: voteValueLabel(r.value as string, house, str(r.raw)),
    personId: num(r.personId),
  }));
}

function partyBreakdown(
  house: House,
  casts: VoteCastRow[],
  orient: Map<string, PartyOrientation>,
  voteHasOrientations: boolean
): PartyBreakdown[] {
  const by = new Map<string, VoteCastRow[]>();
  for (const c of casts) {
    if (!isRealParty(c.party)) continue;
    const list = by.get(c.party) ?? [];
    list.push(c);
    by.set(c.party, list);
  }
  const out: PartyBreakdown[] = [];
  for (const [party, list] of by) {
    const counts: Partial<Record<VoteValue, number>> = {};
    const positions = emptyCounts();
    for (const c of list) {
      counts[c.value] = (counts[c.value] ?? 0) + 1;
      if (isPositionValue(c.value)) positions[c.value] += 1;
    }
    const orientation = orient.get(party) ?? noOrientation(house, voteHasOrientations);
    const majority = majorityOf(positions);
    out.push({
      party,
      counts,
      listed: list.length,
      positions,
      majority,
      majorityLabel: majorityLabel(majority, counts.secret ?? 0),
      orientation,
      followed: followedRatio(house, positions, orientation.value),
    });
  }
  return out.sort((a, b) => b.listed - a.listed || a.party.localeCompare(b.party));
}

/** DEFINITIONS.fidelity on one vote: positions equal to an oriented Sim/Não/Obstrução. */
function followedRatio(house: House, p: PositionCounts, orientation: OrientationValue | null): Ratio | null {
  if (!fidelityOrientation(house, orientation)) return null;
  const n = p.yes + p.no + p.obstruction + p.abstention;
  return ratio(p[orientation], n);
}

function loadPropositions(voteId: string): VoteProposition[] {
  const rows = db()
    .prepare(
      `SELECT t.proposition_id AS propositionId, t.type_abbr AS typeAbbr, t.number, t.year, t.title,
              t.ementa, ${PROVENANCE_COLUMNS}
       FROM camara_vote_proposition t ${PROVENANCE_JOIN}
       WHERE t.camara_vote_id = ? ORDER BY t.id`
    )
    .all(voteId) as Row[];
  const themeStmt = db().prepare(
    `SELECT theme_code AS code, theme AS name FROM camara_proposition_theme
     WHERE proposition_id = ? ORDER BY theme`
  );
  return rows.map((r) => ({
    propositionId: r.propositionId as number,
    typeAbbr: str(r.typeAbbr),
    number: num(r.number),
    year: num(r.year),
    title: str(r.title),
    ementa: str(r.ementa),
    themes: (themeStmt.all(r.propositionId) as Row[]).map((t) => ({ code: t.code as number, name: str(t.name) })),
    provenance: pickProvenance(r),
  }));
}

function termsAvailable(): boolean {
  return db().prepare("SELECT 1 FROM camara_deputy_term LIMIT 1").get() !== undefined;
}

/** Vote instant: dataHoraRegistro (Brasília local, as the term stamps), else noon. */
const VOTE_INSTANT = "coalesce(v.registered_at, v.date || 'T12:00')";

type CamaraStatus = "cast" | "session_present_not_voted" | "absent" | IndeterminateReason | "out_of_office";

const isIndeterminate = (s: CamaraStatus): s is IndeterminateReason =>
  s === "status_change" || s === "no_presence_list";

// ABSENCE RULES (task 8; DEFINITIONS.presenceCamara). A cast always wins over term status.
function camaraStatus(x: {
  hasCast: boolean;
  present: boolean;
  inOffice: boolean;
  changedThatDay: boolean;
  presenceChecked: boolean;
}): CamaraStatus {
  if (x.hasCast) return "cast";
  if (x.present) return "session_present_not_voted";
  if (!x.inOffice) return "out_of_office";
  if (!x.presenceChecked) return "no_presence_list";
  if (x.changedThatDay) return "status_change";
  return "absent";
}

// Rule (d) "status change" = a term row starting that day whose in_office differs from the
// previous row of the same deputy + legislature (first row = posse). Rows that only change
// party or description keep in_office and do not count. Columns: id, start_date.
const inOfficeChanges = (where: string) => `
  SELECT id, start_date FROM (
    SELECT camara_deputy_id AS id, start_date, in_office,
           lag(in_office) OVER (PARTITION BY camara_deputy_id, legislature ORDER BY started_at) AS prev
    FROM camara_deputy_term tm WHERE ${where}
  ) WHERE prev IS NULL OR prev <> in_office`;

function camaraVotePresence(voteId: string): CamaraVotePresence {
  const meta = db()
    .prepare(`SELECT event_id AS eventId, date, legislature FROM camara_vote WHERE camara_vote_id = ?`)
    .get(voteId) as { eventId: number | null; date: string; legislature: number };
  const rows = db()
    .prepare(
      `WITH v AS (
         SELECT legislature, date, event_id, ${VOTE_INSTANT} AS t
         FROM camara_vote v WHERE camara_vote_id = ?
       ),
       office AS (
         SELECT DISTINCT tm.camara_deputy_id AS id FROM camara_deputy_term tm JOIN v
           ON tm.legislature = v.legislature AND tm.in_office = 1 AND tm.started_at <= v.t
          AND (tm.ended_at IS NULL OR v.t < tm.ended_at)
       ),
       present AS (
         SELECT p.camara_deputy_id AS id FROM camara_session_presence p JOIN v ON p.event_id = v.event_id
       ),
       changed AS (
         SELECT DISTINCT ch.id FROM (${inOfficeChanges("1")}) ch JOIN v ON ch.start_date = v.date
       ),
       casted AS (SELECT camara_deputy_id AS id FROM camara_vote_cast WHERE camara_vote_id = ?)
       SELECT u.id AS memberId,
              u.id IN (SELECT id FROM office) AS inOffice,
              u.id IN (SELECT id FROM present) AS present,
              u.id IN (SELECT id FROM changed) AS changed,
              u.id IN (SELECT id FROM casted) AS hasCast
       FROM (SELECT id FROM office UNION SELECT id FROM present) u`
    )
    .all(voteId, voteId) as Row[];
  const presenceListed = rows.filter((r) => r.present).length;
  const presenceChecked = meta.eventId != null && presenceListed > 0;
  // Latest cast per deputy: the 40 votes up to this date first. CROSS JOIN pins the vote-first
  // plan (else SQLite drives from the deputy index: 0.4 s vs 2 ms); the rest scan all casts.
  const lastCasts = (ids: number[], window: number | null): Row[] => {
    const json = JSON.stringify(ids);
    const votes = window
      ? `(SELECT camara_vote_id, date, registered_at FROM camara_vote
          WHERE legislature = ? AND date <= ? ORDER BY date DESC, registered_at DESC LIMIT ${window})`
      : `(SELECT camara_vote_id, date, registered_at FROM camara_vote WHERE legislature = ? AND date <= ?)`;
    return db()
      .prepare(
        `SELECT c.camara_deputy_id AS id, max(r.date || coalesce(r.registered_at, '')) AS k,
                c.name AS cName, c.party_abbr AS cParty, c.state AS cState
         FROM ${votes} r
         ${window ? "CROSS JOIN" : "JOIN"} camara_vote_cast c ON c.camara_vote_id = r.camara_vote_id
         WHERE c.camara_deputy_id IN (SELECT value FROM json_each(?))
         GROUP BY c.camara_deputy_id`
      )
      .all(meta.legislature, meta.date, json) as Row[];
  };
  const refs = (ids: number[]): Map<number, MemberRef> => {
    const out = new Map<number, MemberRef>();
    if (ids.length === 0) return out;
    const last = new Map<number, Row>();
    for (const r of lastCasts(ids, 40)) last.set(r.id as number, r);
    const missing = ids.filter((id) => !last.has(id));
    if (missing.length) for (const r of lastCasts(missing, null)) last.set(r.id as number, r);
    const dStmt = db().prepare(
      `SELECT camara_deputy_id AS id, parliamentary_name AS dName, person_id AS personId
       FROM camara_deputy WHERE camara_deputy_id IN (SELECT value FROM json_each(?))`
    );
    const deps = new Map((dStmt.all(JSON.stringify(ids)) as Row[]).map((r) => [r.id as number, r]));
    for (const id of ids) {
      const c = last.get(id);
      const d = deps.get(id);
      out.set(id, {
        memberId: id,
        name: str(d?.dName) ?? str(c?.cName),
        lastParty: str(c?.cParty),
        state: str(c?.cState),
        personId: num(d?.personId),
      });
    }
    return out;
  };
  const out: CamaraVotePresence = {
    termsAvailable: termsAvailable(),
    eventId: meta.eventId,
    presenceListed,
    inOffice: rows.filter((r) => r.inOffice).length,
    sessionPresentNotVoted: [],
    absent: [],
    indeterminate: [],
    indeterminateReason: null,
    indeterminateLabel: null,
    termSources: [],
  };
  const pending: Array<[MemberRef[], number]> = [];
  for (const r of rows) {
    const status = camaraStatus({
      hasCast: !!r.hasCast,
      present: !!r.present,
      inOffice: !!r.inOffice,
      changedThatDay: !!r.changed,
      presenceChecked,
    });
    const id = r.memberId as number;
    if (status === "session_present_not_voted") pending.push([out.sessionPresentNotVoted, id]);
    else if (status === "absent") pending.push([out.absent, id]);
    else if (isIndeterminate(status)) {
      pending.push([out.indeterminate, id]);
      out.indeterminateReason = status;
      out.indeterminateLabel = indeterminateLabel(status);
    }
  }
  const byId = refs(pending.map(([, id]) => id));
  for (const [list, id] of pending) {
    list.push(byId.get(id) ?? { memberId: id, name: null, lastParty: null, state: null, personId: null });
  }
  const byName = (a: MemberRef, b: MemberRef) => (a.name ?? "").localeCompare(b.name ?? "");
  out.sessionPresentNotVoted.sort(byName);
  out.absent.sort(byName);
  out.indeterminate.sort(byName);
  const flagged = [...out.absent, ...out.indeterminate].map((m) => m.memberId);
  if (flagged.length > 0) {
    out.termSources = provenanceOf(
      "camara_deputy_term",
      "legislature = ? AND camara_deputy_id IN (SELECT value FROM json_each(?))",
      [meta.legislature, JSON.stringify(flagged)]
    );
  }
  return out;
}

// ---------------------------------------------------------------- party summary

export type PartyVoteRow = {
  voteId: string;
  date: string;
  description: string | null;
  kind: string | null;
  kindLabel: string;
  meaningCode: string | null;
  meaning: string;
  result: VoteResult;
  orientation: PartyOrientation;
  majority: MajorityPosition | null;
  /** majorityLabel() with the party's secret casts: "Posição desconhecida (voto secreto)". */
  majorityLabel: string;
  positions: PositionCounts;
  /** Members listed in the vote (any value, incl. secret/presiding/Senado absences). */
  listed: number;
  /** Senado P-NRV ("não registrou voto") of the party; 0 for the Câmara. */
  notVoted: number;
  /** false = Senado party oriented obstruction: majority left out of comparisons. */
  majorityCounted: boolean;
  followed: Ratio | null;
  government: GovernmentOrientation;
};

export type PartySummary = {
  house: House;
  party: string;
  scope: VoteScope;
  votes: number;
  /** Votes where the party oriented Sim/Não/Obstrução. */
  votesOriented: number;
  /** DEFINITIONS.fidelity summed over the party's members in oriented votes. */
  fidelity: Ratio;
  /** DEFINITIONS.government: party orientation vs Governo orientation, per government. */
  withGovernmentByOrientation: GovernmentRatios;
  /** DEFINITIONS.government: party majority position vs Governo orientation, per government. */
  withGovernmentByMajority: GovernmentRatios;
  rows: PartyVoteRow[];
};

export function getPartySummary(house: House, party: string, scope: VoteScope = {}): PartySummary {
  const summary: PartySummary = {
    house, party, scope, votes: 0, votesOriented: 0, fidelity: ratio(0, 0),
    withGovernmentByOrientation: noGovRatios(), withGovernmentByMajority: noGovRatios(),
    rows: [],
  };
  if (!votesAvailable(house)) return summary;
  const h = H[house];
  const base = scopeFilter(house, scope);
  const f: VoteFilter = {
    from: base.from,
    where: `${base.where} AND EXISTS (SELECT 1 FROM ${h.cast} pc WHERE pc.${h.id} = v.${h.id} AND pc.party_abbr = ?)`,
    args: [...base.args, party],
  };
  const resultCols = house === "camara" ? CAMARA_RESULT_COLS : senadoResultCols();
  const votes = db()
    .prepare(
      `SELECT v.${h.id} AS voteId, v.date, v.description, v.kind, ${meaningCol(house)} AS meaningCode, ${resultCols},
              (SELECT count(*) FROM ${h.cast} pc WHERE pc.${h.id} = v.${h.id} AND pc.party_abbr = ?) AS listed,
              (SELECT count(*) FROM ${h.cast} pc
               WHERE pc.${h.id} = v.${h.id} AND pc.party_abbr = ? AND pc.vote = 'secret') AS secret,
              ${house === "senado"
                ? `(SELECT count(*) FROM ${h.cast} pc WHERE pc.${h.id} = v.${h.id}
                     AND pc.party_abbr = ? AND pc.vote = 'present_not_voted')`
                : "0"} AS notVoted
       FROM ${f.from} WHERE ${f.where}
       ORDER BY v.date DESC, v.${h.id} DESC`
    )
    .all(party, party, ...(house === "senado" ? [party] : []), ...f.args) as Row[];
  const counts = loadPartyCounts(house, f, { sql: "c.party_abbr = ?", args: [party] });
  const orient = loadPartyOrientations(house, f, party);
  const gov = loadGovernment(house, f);

  let fAgree = 0, fN = 0;
  const byOrientation = govTally();
  const byMajority = govTally();
  for (const r of votes) {
    const vid = String(r.voteId);
    const positions = counts.get(vid)?.get(party) ?? emptyCounts();
    const orientation = orient.get(vid)?.get(party) ?? noOrientation(house);
    const majority = majorityOf(positions);
    const government = gov.get(vid) ?? governmentOrientation(house, r.date as string, []);
    const followed = followedRatio(house, positions, orientation.value);
    if (followed) {
      summary.votesOriented += 1;
      fAgree += followed.agree;
      fN += followed.n;
    }
    const gid = government.government?.id;
    const govOriented = isOrientedValue(government.value);
    const majorityCounted = majorityCounts(house, orientation.value);
    // Abstenção or "dividido" is not a Sim/Não/Obstrução position: left out. Votes vs a Senado
    // Governo obstruction are left out like fidelity (DEFINITIONS.government).
    tally(byOrientation, gid,
      govOriented && isOrientedValue(orientation.value) ? orientation.value === government.value : null);
    tally(byMajority, gid,
      fidelityOrientation(house, government.value) && majorityCounted && isOrientedValue(majority)
        ? majority === government.value
        : null);
    summary.rows.push({
      voteId: vid,
      date: r.date as string,
      description: str(r.description),
      kind: str(r.kind),
      kindLabel: kindLabel(str(r.kind), str(r.meaningCode)),
      meaningCode: str(r.meaningCode),
      meaning: meaningSentence(str(r.meaningCode)),
      result: house === "camara" ? camaraResultOf(r) : senadoResultOf(r),
      orientation,
      majority,
      majorityLabel: majorityLabel(majority, r.secret as number),
      positions,
      listed: r.listed as number,
      notVoted: r.notVoted as number,
      majorityCounted,
      followed,
      government,
    });
  }
  summary.votes = votes.length;
  summary.fidelity = ratio(fAgree, fN);
  summary.withGovernmentByOrientation = govRatios(byOrientation);
  summary.withGovernmentByMajority = govRatios(byMajority);
  return summary;
}

// ---------------------------------------------------------------- party x party

export type AgreementBasis = "orientation" | "majority";

/**
 * sharedBench: of the n compared votes, those where both parties read the same Câmara bench
 * row (federation/bloc), so agreement is by construction. Always 0 on the majority basis.
 */
export type AgreementCell = { a: string; b: string; sharedBench: number } & Ratio;

export type AgreementMatrix = {
  house: House;
  basis: AgreementBasis;
  scope: VoteScope;
  /** Votes in scope. */
  votes: number;
  /** Parties with at least one Sim/Não/Obstrução position, most positions first. */
  parties: Array<{ party: string; positions: number }>;
  /** One cell per unordered pair, a < b (localeCompare); read with matrixCell(). */
  cells: AgreementCell[];
};

export function matrixCell(m: AgreementMatrix, a: string, b: string): AgreementCell | null {
  const [x, y] = a.localeCompare(b) <= 0 ? [a, b] : [b, a];
  return m.cells.find((c) => c.a === x && c.b === y) ?? null;
}

/** A party's Sim/Não/Obstrução position on a vote; bench = Câmara bench row it read, if any. */
type PartyPosition = { pos: OrientedValue; bench: string | null };

/** (vote -> party -> position) under a basis. */
function partyPositions(
  house: House,
  f: VoteFilter,
  basis: AgreementBasis
): Map<string, Map<string, PartyPosition>> {
  const out = new Map<string, Map<string, PartyPosition>>();
  if (basis === "orientation") {
    for (const [vid, byParty] of loadPartyOrientations(house, f)) {
      const m = new Map<string, PartyPosition>();
      for (const [party, o] of byParty) {
        if (!isRealParty(party) || !isOrientedValue(o.value)) continue;
        m.set(party, { pos: o.value, bench: house === "camara" ? o.benchLabel : null });
      }
      if (m.size) out.set(vid, m);
    }
    return out;
  }
  // DEFINITIONS.majorityPosition: Senado parties that oriented obstruction are left out.
  const orient = house === "senado" ? loadPartyOrientations(house, f) : null;
  for (const [vid, byParty] of loadPartyCounts(house, f)) {
    const m = new Map<string, PartyPosition>();
    for (const [party, c] of byParty) {
      if (!majorityCounts(house, orient?.get(vid)?.get(party)?.value)) continue;
      const maj = majorityOf(c);
      if (isOrientedValue(maj)) m.set(party, { pos: maj, bench: null });
    }
    if (m.size) out.set(vid, m);
  }
  return out;
}

const matrixCache = new Map<string, AgreementMatrix>();

// Module caches outlive crawler reloads in a long-running server. PRAGMA data_version changes
// whenever another connection commits, so any write to the .db drops them.
let cachedDataVersion: number | null = null;

function freshCaches(): void {
  const version = db().pragma("data_version", { simple: true }) as number;
  if (version === cachedDataVersion) return;
  cachedDataVersion = version;
  matrixCache.clear();
  rosterCache.clear();
  govCache.clear();
  sourcesCache.clear();
}

// DEFINITIONS.agreement: same position over votes where both have Sim/Não/Obstrução.
export function getAgreementMatrix(
  house: House,
  opts: VoteScope & { basis: AgreementBasis }
): AgreementMatrix {
  const scope: VoteScope = { period: opts.period, includeProcedural: opts.includeProcedural, year: opts.year, theme: opts.theme };
  const key = JSON.stringify([house, opts.basis, scope]);
  if (votesAvailable(house)) freshCaches();
  const hit = matrixCache.get(key);
  if (hit) return hit;
  const empty: AgreementMatrix = { house, basis: opts.basis, scope, votes: 0, parties: [], cells: [] };
  if (!votesAvailable(house)) return empty;
  const f = scopeFilter(house, scope);
  const votes = (db().prepare(`SELECT count(*) AS n FROM ${f.from} WHERE ${f.where}`).get(...f.args) as { n: number }).n;
  const positions = partyPositions(house, f, opts.basis);
  const posCount = new Map<string, number>();
  const pairs = new Map<string, { agree: number; n: number; shared: number }>();
  for (const byParty of positions.values()) {
    const list = [...byParty.entries()].sort((x, y) => x[0].localeCompare(y[0]));
    for (let i = 0; i < list.length; i++) {
      posCount.set(list[i][0], (posCount.get(list[i][0]) ?? 0) + 1);
      const x = list[i][1];
      for (let j = i + 1; j < list.length; j++) {
        const y = list[j][1];
        const k = `${list[i][0]}\u001f${list[j][0]}`;
        const p = pairs.get(k) ?? { agree: 0, n: 0, shared: 0 };
        p.n += 1;
        if (x.pos === y.pos) p.agree += 1;
        if (x.bench !== null && x.bench === y.bench) p.shared += 1;
        pairs.set(k, p);
      }
    }
  }
  const result: AgreementMatrix = {
    house,
    basis: opts.basis,
    scope,
    votes,
    parties: [...posCount.entries()]
      .map(([party, n]) => ({ party, positions: n }))
      .sort((a, b) => b.positions - a.positions || a.party.localeCompare(b.party)),
    cells: [...pairs.entries()].map(([k, p]) => {
      const [a, b] = k.split("\u001f");
      return { a, b, sharedBench: p.shared, ...ratio(p.agree, p.n) };
    }),
  };
  // The .db is rewrite-only; an empty result may mean a crawler is still running, so not cached.
  if (votes > 0) matrixCache.set(key, result);
  return result;
}

// ---------------------------------------------------------------- party x Governo

export type PartyGovernmentRow = {
  party: string;
  /** Party orientation vs Governo orientation, per government. */
  byOrientation: GovernmentRatios;
  /** Party majority position vs Governo orientation, per government. */
  byMajority: GovernmentRatios;
};

export type PartyVsGovernment = {
  house: House;
  scope: VoteScope;
  votes: number;
  /** Votes where the Governo oriented Sim/Não/Obstrução. */
  votesWithGovernmentOrientation: number;
  governments: GovernmentPeriod[];
  rows: PartyGovernmentRow[];
};

const govCache = new Map<string, PartyVsGovernment>();

// DEFINITIONS.government, per party, for both bases. Cached like the matrix (~0.8 s for "tudo").
export function getPartyVsGovernment(house: House, scope: VoteScope = {}): PartyVsGovernment {
  const out: PartyVsGovernment = { house, scope, votes: 0, votesWithGovernmentOrientation: 0, governments: [], rows: [] };
  if (!votesAvailable(house)) return out;
  const key = JSON.stringify([house, scope.period ?? null, !!scope.includeProcedural, scope.year ?? null, scope.theme ?? null]);
  freshCaches();
  const hit = govCache.get(key);
  if (hit) return hit;
  const f = scopeFilter(house, scope);
  const gov = loadGovernment(house, f);
  out.votes = gov.size;
  const orient = partyPositions(house, f, "orientation");
  const major = partyPositions(house, f, "majority");
  const acc = new Map<string, { o: GovTally; m: GovTally }>();
  const get = (p: string) => {
    let a = acc.get(p);
    if (!a) acc.set(p, (a = { o: govTally(), m: govTally() }));
    return a;
  };
  const govs = new Set<GovernmentId>();
  for (const [vid, g] of gov) {
    const gid = g.government?.id;
    if (gid) govs.add(gid);
    const oriented = isOrientedValue(g.value);
    if (oriented) out.votesWithGovernmentOrientation += 1;
    // A party with a position on a vote marks that government as seen on both bases.
    for (const [party, p] of orient.get(vid) ?? []) {
      const a = get(party);
      tally(a.o, gid, oriented ? p.pos === g.value : null);
      tally(a.m, gid, null);
    }
    for (const [party, p] of major.get(vid) ?? []) {
      const a = get(party);
      tally(a.m, gid, fidelityOrientation(house, g.value) ? p.pos === g.value : null);
      tally(a.o, gid, null);
    }
  }
  out.governments = GOVERNMENT_PERIODS.filter((g) => govs.has(g.id));
  const majorityN = (r: PartyGovernmentRow) => (r.byMajority.bolsonaro?.n ?? 0) + (r.byMajority.lula?.n ?? 0);
  out.rows = [...acc.entries()]
    .map(([party, a]) => ({ party, byOrientation: govRatios(a.o), byMajority: govRatios(a.m) }))
    .sort((x, y) => majorityN(y) - majorityN(x) || x.party.localeCompare(y.party));
  // Empty may mean a crawler is still writing: not cached.
  if (out.votes > 0) govCache.set(key, out);
  return out;
}

const sourcesCache = new Map<string, Provenance[]>();

/** Files behind the matrix of a scope: casts, orientations and (Câmara) party benches. */
export function getScopeSources(house: House, scope: VoteScope = {}): Provenance[] {
  if (!votesAvailable(house)) return [];
  const key = JSON.stringify([house, scope.period ?? null, !!scope.includeProcedural, scope.year ?? null, scope.theme ?? null]);
  freshCaches();
  const hit = sourcesCache.get(key);
  if (hit) return hit;
  const h = H[house];
  const f = scopeFilter(house, scope);
  const inScope = `${h.id} IN (SELECT v.${h.id} FROM ${f.from} WHERE ${f.where})`;
  const out = dedupeProvenance([
    ...provenanceOf(h.cast, inScope, f.args),
    ...provenanceOf(h.orient, inScope, f.args),
    ...(house === "camara" ? provenanceOf("camara_vote_party_bench", inScope, f.args) : []),
  ]).sort((a, b) => a.url.localeCompare(b.url));
  if (out.length > 0) sourcesCache.set(key, out);
  return out;
}

// ---------------------------------------------------------------- one parliamentarian

export type ParliamentarianIdentity = {
  house: House;
  id: number;
  /** Parliamentary name (camara_deputy / senate_senator), else the latest cast name. */
  name: string | null;
  /** Câmara nomeCivil / Senado NomeCompletoParlamentar. */
  fullName: string | null;
  personId: number | null;
  matchBasis: string | null;
  unmatchedReason: string | null;
  lastParty: string | null;
  lastState: string | null;
  lastVoteDate: string | null;
  provenance: Provenance | null;
};

export function getParliamentarian(house: House, id: number): ParliamentarianIdentity | null {
  if (!votesAvailable(house)) return null;
  const h = H[house];
  const tbl = house === "camara" ? "camara_deputy" : "senate_senator";
  const full = house === "camara" ? "civil_name" : "full_name";
  const d = db()
    .prepare(
      `SELECT t.parliamentary_name AS name, t.${full} AS fullName, t.person_id AS personId,
              t.match_basis AS matchBasis, t.unmatched_reason AS unmatchedReason, ${PROVENANCE_COLUMNS}
       FROM ${tbl} t ${PROVENANCE_JOIN} WHERE t.${h.member} = ?`
    )
    .get(id) as Row | undefined;
  const order = house === "camara" ? "v.date DESC, v.registered_at DESC" : "v.date DESC, v.senate_vote_code DESC";
  const last = db()
    .prepare(
      `SELECT c.name, c.party_abbr AS party, c.state, v.date
       FROM ${h.cast} c JOIN ${h.vote} v ON v.${h.id} = c.${h.id}
       WHERE c.${h.member} = ? ORDER BY ${order} LIMIT 1`
    )
    .get(id) as Row | undefined;
  if (!d && !last) return null;
  return {
    house,
    id,
    name: str(d?.name) ?? str(last?.name),
    fullName: str(d?.fullName),
    personId: num(d?.personId),
    matchBasis: str(d?.matchBasis),
    unmatchedReason: str(d?.unmatchedReason),
    lastParty: str(last?.party),
    lastState: str(last?.state),
    lastVoteDate: str(last?.date),
    provenance: d ? pickProvenance(d) : null,
  };
}

export type ParliamentarianVoteRow = {
  voteId: string;
  date: string;
  description: string | null;
  kind: string | null;
  kindLabel: string;
  meaningCode: string | null;
  meaning: string;
  /** Same as VoteListItem.propositions: Câmara titles ("PL 2215/2024"); Senado identificacao. */
  propositions: string[];
  result: VoteResult;
  /** Cast value, or Câmara-derived session_present_not_voted / absent / indeterminate. */
  value: VoteValue;
  /** Set when value is 'indeterminate'; label then says which reason. */
  indeterminateReason: IndeterminateReason | null;
  raw: string | null;
  label: string;
  /** Party at vote time (from the cast row); null when there is no cast. */
  party: string | null;
  partyOrientation: PartyOrientation | null;
  /** DEFINITIONS.fidelity on this vote; null when not counted. */
  followedParty: boolean | null;
  government: GovernmentOrientation;
  /** DEFINITIONS.government on this vote; null when not counted. */
  withGovernment: boolean | null;
  /** File behind this row: the cast's votacoesVotos-YYYY / Senado month; for a Câmara row
   *  without cast, the session presence file of that year, else the vote's own file. */
  provenance: Provenance | null;
};

export type PresenceSummary = {
  /** Câmara: false when camara_deputy_term is empty, so absences were not assessed. */
  available: boolean;
  /**
   * Cast values plus, Câmara, session_present_not_voted / absent / indeterminate; Senado,
   * unknown = left out of a voters-only list (DEFINITIONS.presenceSenado, not in the rate).
   */
  counts: Partial<Record<VoteValue, number>>;
  /** counts.indeterminate split by reason (Câmara; zeros for the Senado). */
  indeterminateByReason: Record<IndeterminateReason, number>;
  /** DEFINITIONS.voted: the main attendance figure, same formula in both houses. */
  voted: Ratio;
  /** Votes where the member presided the session (outside `voted`). */
  presiding: number;
  /** Secondary. Câmara: DEFINITIONS.presenceCamara; Senado: the former "participação". */
  rate: Ratio;
};

export type ParliamentarianSourcesBySection = {
  identity: Provenance[];
  casts: Provenance[];
  /** Câmara camara_deputy_term (absence rules); empty for the Senado. */
  terms: Provenance[];
  /** Câmara camara_session_presence; empty for the Senado. */
  presence: Provenance[];
};

export type ParliamentarianSummary = {
  identity: ParliamentarianIdentity;
  scope: VoteScope;
  presence: PresenceSummary;
  fidelity: Ratio;
  /** DEFINITIONS.government per government; null = the member has no vote of that government. */
  withGovernment: GovernmentRatios;
  /** Empty unless includeVotes. Newest first. */
  votes: ParliamentarianVoteRow[];
  /** Provenance of everything above, deduplicated; per section in sourcesBySection. */
  sources: Provenance[];
  sourcesBySection: ParliamentarianSourcesBySection;
};

type MemberVote = {
  voteId: string;
  date: string;
  description: string | null;
  kind: string | null;
  meaningCode: string | null;
  propositions: string[];
  result: VoteResult;
  value: VoteValue;
  indeterminateReason: IndeterminateReason | null;
  raw: string | null;
  party: string | null;
  orientation: PartyOrientation | null;
  provenanceId: number | null;
};

/** Every vote in scope that concerns the member (cast, or Câmara derived status), newest first. */
function memberVotes(house: House, id: number, f: VoteFilter): MemberVote[] {
  if (house === "senado") {
    const rows = db()
      .prepare(
        `SELECT v.senate_vote_code AS voteId, v.date, v.description, v.kind, ${meaningCol("senado")} AS meaningCode,
                ${senadoResultCols()}, c.vote AS value, c.vote_raw AS raw, c.party_abbr AS party,
                v.identification AS props, c.provenance_id AS provenanceId
         FROM ${f.from} JOIN senate_vote_cast c ON c.senate_vote_code = v.senate_vote_code
         WHERE ${f.where} AND c.senator_code = ?
         ORDER BY v.date DESC, v.senate_vote_code DESC`
      )
      .all(...f.args, id) as Row[];
    const orient = loadPartyOrientations("senado", {
      ...f,
      where: `${f.where} AND EXISTS (SELECT 1 FROM senate_vote_cast mc
               WHERE mc.senate_vote_code = v.senate_vote_code AND mc.senator_code = ?)`,
      args: [...f.args, id],
    });
    return rows.map((r) => {
      const party = str(r.party);
      const vid = String(r.voteId);
      return {
        voteId: vid,
        date: r.date as string,
        description: str(r.description),
        kind: str(r.kind),
        meaningCode: str(r.meaningCode),
        propositions: splitProps(r.props),
        result: senadoResultOf(r),
        value: r.value as VoteValue,
        indeterminateReason: null,
        raw: str(r.raw),
        party,
        orientation: isRealParty(party) ? (orient.get(vid)?.get(party) ?? noOrientation("senado")) : null,
        provenanceId: num(r.provenanceId),
      };
    });
  }
  // Câmara: ABSENCE RULES (a)-(d) per vote, same order of precedence as camaraStatus().
  const rows = db()
    .prepare(
      `WITH changes AS (${inOfficeChanges("tm.camara_deputy_id = ?")})
       SELECT v.camara_vote_id AS voteId, v.date, v.description, v.kind, v.meaning_code AS meaningCode,
              ${CAMARA_RESULT_COLS}, ${CAMARA_PROPS_SQL} AS props,
              v.event_id AS eventId, c.vote AS value, c.vote_raw AS raw,
              coalesce(c.provenance_id, CASE WHEN c.id IS NULL THEN (SELECT p.provenance_id
                FROM camara_session_presence p WHERE p.event_id = v.event_id LIMIT 1) END,
                v.provenance_id) AS provenanceId,
              c.party_abbr AS party, pb.bench_label AS benchLabel, pb.match_basis AS matchBasis,
              pb.unmatched_reason AS unmatchedReason, o.orientation AS oValue, o.orientation_raw AS oRaw,
              (pb.id IS NOT NULL) AS hasBench,
              EXISTS (SELECT 1 FROM camara_vote_orientation ao
                      WHERE ao.camara_vote_id = v.camara_vote_id) AS anyOrientation,
              EXISTS (SELECT 1 FROM camara_session_presence p
                      WHERE p.event_id = v.event_id AND p.camara_deputy_id = ?) AS present,
              EXISTS (SELECT 1 FROM camara_session_presence p WHERE p.event_id = v.event_id) AS presenceChecked,
              EXISTS (SELECT 1 FROM camara_deputy_term tm
                      WHERE tm.camara_deputy_id = ? AND tm.legislature = v.legislature AND tm.in_office = 1
                        AND tm.started_at <= ${VOTE_INSTANT}
                        AND (tm.ended_at IS NULL OR ${VOTE_INSTANT} < tm.ended_at)) AS inOffice,
              v.date IN (SELECT start_date FROM changes) AS changed
       FROM ${f.from}
       LEFT JOIN camara_vote_cast c ON c.camara_vote_id = v.camara_vote_id AND c.camara_deputy_id = ?
       LEFT JOIN camara_vote_party_bench pb
         ON pb.camara_vote_id = v.camara_vote_id AND pb.party_abbr = c.party_abbr
       LEFT JOIN camara_vote_orientation o
         ON o.camara_vote_id = pb.camara_vote_id AND o.bench_label = pb.bench_label
       WHERE ${f.where}
       ORDER BY v.date DESC, v.registered_at DESC, v.id DESC`
    )
    .all(id, id, id, id, ...f.args) as Row[];
  const out: MemberVote[] = [];
  for (const r of rows) {
    const status = camaraStatus({
      hasCast: r.value != null,
      present: !!r.present,
      inOffice: !!r.inOffice,
      changedThatDay: !!r.changed,
      presenceChecked: r.eventId != null && !!r.presenceChecked,
    });
    if (status === "out_of_office") continue;
    const party = str(r.party);
    let orientation: PartyOrientation | null = null;
    if (status === "cast" && isRealParty(party)) {
      const value = (str(r.oValue) as OrientationValue | null) ?? null;
      orientation = r.hasBench
        ? {
            value,
            raw: str(r.oRaw),
            label: orientationLabel(value, "camara", str(r.oRaw), !!r.anyOrientation),
            benchLabel: str(r.benchLabel),
            matchBasis: str(r.matchBasis),
            unmatchedReason: str(r.unmatchedReason),
          }
        : noOrientation("camara", !!r.anyOrientation);
    }
    const indeterminate = isIndeterminate(status);
    out.push({
      voteId: String(r.voteId),
      date: r.date as string,
      description: str(r.description),
      kind: str(r.kind),
      meaningCode: str(r.meaningCode),
      propositions: splitProps(r.props),
      result: camaraResultOf(r),
      value: status === "cast" ? (r.value as VoteValue) : indeterminate ? "indeterminate" : status,
      indeterminateReason: indeterminate ? status : null,
      raw: str(r.raw),
      party,
      orientation,
      provenanceId: num(r.provenanceId),
    });
  }
  return out;
}

/** provenance_id -> Provenance, one query. */
function provenanceByIds(ids: Array<number | null>): Map<number, Provenance> {
  const uniq = [...new Set(ids.filter((x): x is number => x != null))];
  if (uniq.length === 0) return new Map();
  const rows = db()
    .prepare(
      `SELECT t.provenance_id AS pid, ${PROVENANCE_COLUMNS}
       FROM (SELECT value AS provenance_id FROM json_each(?)) t ${PROVENANCE_JOIN}`
    )
    .all(JSON.stringify(uniq)) as Row[];
  return new Map(rows.map((r) => [r.pid as number, pickProvenance(r)]));
}

export type SummaryOptions = VoteScope & { includeVotes?: boolean };

export function getDeputySummary(house: House, id: number, opts: SummaryOptions = {}): ParliamentarianSummary | null {
  const identity = getParliamentarian(house, id);
  if (!identity) return null;
  const scope: VoteScope = { period: opts.period, includeProcedural: opts.includeProcedural, year: opts.year, theme: opts.theme };
  const f = scopeFilter(house, scope);
  const mv = memberVotes(house, id, f);
  const gov = loadGovernment(house, f);

  const counts: Partial<Record<VoteValue, number>> = {};
  const indeterminateByReason: Record<IndeterminateReason, number> = { status_change: 0, no_presence_list: 0 };
  let attended = 0, expected = 0, fA = 0, fN = 0, voted = 0, inOffice = 0, presiding = 0;
  const withGov = govTally();
  const rows: ParliamentarianVoteRow[] = [];
  const prov = opts.includeVotes ? provenanceByIds(mv.map((m) => m.provenanceId)) : new Map<number, Provenance>();
  for (const m of mv) {
    counts[m.value] = (counts[m.value] ?? 0) + 1;
    // DEFINITIONS.voted: presiding is neither a vote nor an absence, so it is left out.
    if (m.value === "presiding") presiding += 1;
    else inOffice += 1;
    if (isPositionValue(m.value) || m.value === "secret") voted += 1;
    if (m.indeterminateReason) indeterminateByReason[m.indeterminateReason] += 1;
    if (house === "camara") {
      // DEFINITIONS.presenceCamara: indeterminate is outside the denominator.
      if (m.value !== "indeterminate") expected += 1;
      if (m.value !== "indeterminate" && m.value !== "absent") attended += 1;
    } else {
      // DEFINITIONS.presenceSenado: P-NRV and absence codes are listed but not attendance.
      expected += 1;
      if (isPositionValue(m.value) || m.value === "secret" || m.value === "presiding") attended += 1;
    }
    let followedParty: boolean | null = null;
    if (m.orientation && fidelityOrientation(house, m.orientation.value) && isPositionValue(m.value)) {
      followedParty = m.value === m.orientation.value;
      fN += 1;
      if (followedParty) fA += 1;
    }
    const government = gov.get(m.voteId) ?? governmentOrientation(house, m.date, []);
    let withGovernment: boolean | null = null;
    if (fidelityOrientation(house, government.value) && isPositionValue(m.value)) {
      withGovernment = m.value === government.value;
    }
    tally(withGov, government.government?.id, withGovernment);
    if (opts.includeVotes) {
      rows.push({
        voteId: m.voteId,
        date: m.date,
        description: m.description,
        kind: m.kind,
        kindLabel: kindLabel(m.kind, m.meaningCode),
        meaningCode: m.meaningCode,
        meaning: meaningSentence(m.meaningCode),
        propositions: m.propositions,
        result: m.result,
        value: m.value,
        indeterminateReason: m.indeterminateReason,
        raw: m.raw,
        label: m.indeterminateReason
          ? indeterminateLabel(m.indeterminateReason)
          : voteValueLabel(m.value, house, m.raw),
        party: m.party,
        partyOrientation: m.orientation,
        followedParty,
        government,
        withGovernment,
        provenance: m.provenanceId != null ? (prov.get(m.provenanceId) ?? null) : null,
      });
    }
  }
  if (house === "senado") {
    const unknown = senadoUnknownVotes(id, f);
    if (unknown > 0) counts.unknown = unknown;
  }
  const isCamara = house === "camara";
  const sourcesBySection: ParliamentarianSourcesBySection = {
    identity: identity.provenance ? [identity.provenance] : [],
    casts: provenanceOf(H[house].cast, `${H[house].member} = ?`, [id]),
    terms: isCamara ? provenanceOf("camara_deputy_term", "camara_deputy_id = ?", [id]) : [],
    presence: isCamara ? provenanceOf("camara_session_presence", "camara_deputy_id = ?", [id]) : [],
  };
  return {
    identity,
    scope,
    presence: {
      available: house === "senado" || termsAvailable(),
      counts,
      indeterminateByReason,
      voted: ratio(voted, inOffice),
      presiding,
      rate: ratio(attended, expected),
    },
    fidelity: ratio(fA, fN),
    withGovernment: govRatios(withGov),
    votes: rows,
    sources: dedupeProvenance(Object.values(sourcesBySection).flat()),
    sourcesBySection,
  };
}

const LEGISLATURE_OF_DATE_SQL =
  `CASE ${LEGISLATURES.map((l) => `WHEN v.date BETWEEN '${l.start}' AND '${l.end}' THEN ${l.id}`).join(" ")} END`;

// DEFINITIONS.presenceSenado "desconhecido": votes that list only voters (no absence code
// at all, as getVote listsOnlyVoters) where the senator is missing, counted only between
// their first and last listed vote of that legislature (no Senado mandate table to bound it).
function senadoUnknownVotes(id: number, f: VoteFilter): number {
  const r = db()
    .prepare(
      `WITH span AS (
         SELECT ${LEGISLATURE_OF_DATE_SQL} AS leg, min(v.date) AS a, max(v.date) AS b
         FROM senate_vote v JOIN senate_vote_cast c ON c.senate_vote_code = v.senate_vote_code
         WHERE c.senator_code = ? GROUP BY 1
       )
       SELECT count(*) AS n
       FROM ${f.from} JOIN span ON v.date BETWEEN span.a AND span.b
       WHERE ${f.where}
         AND NOT EXISTS (SELECT 1 FROM senate_vote_cast c
                         WHERE c.senate_vote_code = v.senate_vote_code AND c.senator_code = ?)
         AND EXISTS (SELECT 1 FROM senate_vote_cast c WHERE c.senate_vote_code = v.senate_vote_code)
         AND NOT EXISTS (SELECT 1 FROM senate_vote_cast c
                         WHERE c.senate_vote_code = v.senate_vote_code
                           AND c.vote IN ('absent', 'absent_justified', 'other'))`
    )
    .get(id, ...f.args, id) as { n: number };
  return r.n;
}

// ---------------------------------------------------------------- comparisons

export type PairAgreement = { a: number; b: number } & Ratio;

export type ComparisonVoteRow = {
  voteId: string;
  date: string;
  description: string | null;
  kindLabel: string;
  meaning: string;
  /** memberId -> { value, label }; absent key = not in the cast list. */
  values: Record<number, { value: VoteValue; label: string; party: string | null }>;
};

export type ParliamentarianComparison = {
  house: House;
  scope: VoteScope;
  members: ParliamentarianIdentity[];
  /** DEFINITIONS.agreementParliamentarians, per unordered pair. */
  pairs: PairAgreement[];
  /** Votes where at least two of the members are in the cast list, newest first. */
  votes: ComparisonVoteRow[];
};

export function compareParliamentarians(house: House, ids: number[], scope: VoteScope = {}): ParliamentarianComparison {
  const unique = [...new Set(ids.filter((x) => Number.isInteger(x)))].slice(0, 10);
  const out: ParliamentarianComparison = { house, scope, members: [], pairs: [], votes: [] };
  if (!votesAvailable(house) || unique.length === 0) return out;
  out.members = unique.map((id) => getParliamentarian(house, id)).filter((m): m is ParliamentarianIdentity => !!m);
  const h = H[house];
  const f = scopeFilter(house, scope);
  const marks = unique.map(() => "?").join(", ");
  const rows = db()
    .prepare(
      `SELECT v.${h.id} AS voteId, v.date, v.description, v.kind, ${meaningCol(house)} AS meaningCode,
              c.${h.member} AS memberId, c.vote AS value, c.vote_raw AS raw, c.party_abbr AS party
       FROM ${f.from} JOIN ${h.cast} c ON c.${h.id} = v.${h.id}
       WHERE ${f.where} AND c.${h.member} IN (${marks})
       ORDER BY v.date DESC, v.${h.id} DESC`
    )
    .all(...f.args, ...unique) as Row[];
  const byVote = new Map<string, ComparisonVoteRow>();
  for (const r of rows) {
    const vid = String(r.voteId);
    let row = byVote.get(vid);
    if (!row) {
      row = {
        voteId: vid,
        date: r.date as string,
        description: str(r.description),
        kindLabel: kindLabel(str(r.kind), str(r.meaningCode)),
        meaning: meaningSentence(str(r.meaningCode)),
        values: {},
      };
      byVote.set(vid, row);
    }
    row.values[r.memberId as number] = {
      value: r.value as VoteValue,
      label: voteValueLabel(r.value as string, house, str(r.raw)),
      party: str(r.party),
    };
  }
  out.votes = [...byVote.values()].filter((v) => Object.keys(v.values).length >= 2);
  for (let i = 0; i < unique.length; i++) {
    for (let j = i + 1; j < unique.length; j++) {
      let agree = 0, n = 0;
      for (const v of out.votes) {
        const x = v.values[unique[i]]?.value;
        const y = v.values[unique[j]]?.value;
        if (isPositionValue(x) && isPositionValue(y)) {
          n += 1;
          if (x === y) agree += 1;
        }
      }
      out.pairs.push({ a: unique[i], b: unique[j], ...ratio(agree, n) });
    }
  }
  return out;
}

export type DeputyPartyVoteRow = {
  voteId: string;
  date: string;
  description: string | null;
  kindLabel: string;
  meaning: string;
  value: VoteValue;
  label: string;
  /** Party compared against on this vote (given party, or own party at vote time). */
  party: string | null;
  orientation: PartyOrientation;
  /** Majority position of the OTHER members of the party (the member is left out). */
  majority: MajorityPosition | null;
  majorityLabel: string;
  followedOrientation: boolean | null;
  withMajority: boolean | null;
};

export type DeputyPartyComparison = {
  identity: ParliamentarianIdentity;
  /** null = own party at each vote. */
  party: string | null;
  scope: VoteScope;
  /** DEFINITIONS.fidelity against the compared party's orientation. */
  fidelity: Ratio;
  /**
   * DEFINITIONS.withPartyMajority: same position as the majority of the other members of the
   * party. A vote where nobody else in the party voted drops out.
   */
  withMajority: Ratio;
  rows: DeputyPartyVoteRow[];
};

export function compareDeputyWithParty(
  house: House,
  id: number,
  opts: VoteScope & { party?: string } = {}
): DeputyPartyComparison | null {
  const identity = getParliamentarian(house, id);
  if (!identity) return null;
  const scope: VoteScope = { period: opts.period, includeProcedural: opts.includeProcedural, year: opts.year, theme: opts.theme };
  const h = H[house];
  const base = scopeFilter(house, scope);
  const f: VoteFilter = {
    from: base.from,
    where: `${base.where} AND EXISTS (SELECT 1 FROM ${h.cast} mc
             WHERE mc.${h.id} = v.${h.id} AND mc.${h.member} = ?)`,
    args: [...base.args, id],
  };
  const mine = db()
    .prepare(
      `SELECT v.${h.id} AS voteId, v.date, v.description, v.kind, ${meaningCol(house)} AS meaningCode,
              c.vote AS value, c.vote_raw AS raw, c.party_abbr AS party
       FROM ${f.from} JOIN ${h.cast} c ON c.${h.id} = v.${h.id} AND c.${h.member} = ?
       WHERE ${f.where}
       ORDER BY v.date DESC, v.${h.id} DESC`
    )
    .all(id, ...f.args) as Row[];
  // Counts only for the compared party of each vote (the given one, or the member's own),
  // without the member: their own vote would inflate the agreement with the majority.
  const partyFilter: Sql = opts.party
    ? { sql: `c.party_abbr = ? AND c.${h.member} <> ?`, args: [opts.party, id] }
    : {
        sql: `c.party_abbr = (SELECT me.party_abbr FROM ${h.cast} me
               WHERE me.${h.id} = c.${h.id} AND me.${h.member} = ?) AND c.${h.member} <> ?`,
        args: [id, id],
      };
  const counts = loadPartyCounts(house, f, partyFilter);
  const orient = loadPartyOrientations(house, f, opts.party);
  let fA = 0, fN = 0, mA = 0, mN = 0;
  const rows: DeputyPartyVoteRow[] = mine.map((r) => {
    const vid = String(r.voteId);
    const party = opts.party ?? str(r.party);
    const value = r.value as VoteValue;
    const real = isRealParty(party);
    const orientation = real ? (orient.get(vid)?.get(party) ?? noOrientation(house)) : noOrientation(house);
    const majority = real ? majorityOf(counts.get(vid)?.get(party) ?? emptyCounts()) : null;
    let followedOrientation: boolean | null = null;
    if (fidelityOrientation(house, orientation.value) && isPositionValue(value)) {
      followedOrientation = value === orientation.value;
      fN += 1;
      if (followedOrientation) fA += 1;
    }
    let withMajority: boolean | null = null;
    if (majorityCounts(house, orientation.value) && isPositionValue(majority) && isPositionValue(value)) {
      withMajority = value === majority;
      mN += 1;
      if (withMajority) mA += 1;
    }
    return {
      voteId: vid,
      date: r.date as string,
      description: str(r.description),
      kindLabel: kindLabel(str(r.kind), str(r.meaningCode)),
      meaning: meaningSentence(str(r.meaningCode)),
      value,
      label: voteValueLabel(value, house, str(r.raw)),
      party,
      orientation,
      majority,
      // In a secret vote every cast is 'secret', the member's included.
      majorityLabel: majorityLabel(majority, value === "secret" ? 1 : 0),
      followedOrientation,
      withMajority,
    };
  });
  return {
    identity,
    party: opts.party ?? null,
    scope,
    fidelity: ratio(fA, fN),
    withMajority: ratio(mA, mN),
    rows,
  };
}

// ---------------------------------------------------------------- lists of parliamentarians

/** politician_history year 2026: office as DS_CARGO ('SENADOR'), number, party, ballot name. */
export type Candidacy2026 = {
  office: string;
  number: string | null;
  party: string | null;
  /** NM_URNA_CANDIDATO: the name printed on the ballot ("LEILA DO VÔLEI"). */
  ballotName: string | null;
};

export type ParliamentarianListItem = {
  house: House;
  id: number;
  name: string | null;
  party: string | null;
  state: string | null;
  personId: number | null;
  votes: number;
  firstVoteDate: string;
  lastVoteDate: string;
  /** 2026 candidacy (the filtered office if any; a deputy may run for Senador); in `uf` when filtered. */
  candidacy2026: Candidacy2026 | null;
};

export type ListParliamentariansOptions = {
  house: House;
  uf?: string;
  /** Only people with a 2026 candidacy, in `uf` when given. */
  candidates2026?: boolean;
  /** With candidates2026: only this office (DS_CARGO, e.g. 'DEPUTADO FEDERAL'); none = any. */
  office?: string;
  q?: string;
  period?: PeriodFilter;
};

const rosterCache = new Map<string, Omit<ParliamentarianListItem, "candidacy2026">[]>();

function roster(house: House, period: PeriodFilter | undefined): Omit<ParliamentarianListItem, "candidacy2026">[] {
  const key = JSON.stringify([house, period ?? null]);
  freshCaches();
  const hit = rosterCache.get(key);
  if (hit) return hit;
  const h = H[house];
  const { from, to } = periodRange(period);
  const instant = house === "camara" ? "v.date || coalesce(v.registered_at, '')" : "v.date || printf('%012d', v.senate_vote_code)";
  const tbl = house === "camara" ? "camara_deputy" : "senate_senator";
  // SQLite bare columns take the max() row only when max() is the sole min/max aggregate:
  // name/party/state come from the latest cast; the first date needs its own GROUP BY.
  const rows = db()
    .prepare(
      `WITH latest AS (
         SELECT c.${h.member} AS id, max(${instant}) AS k, c.name, c.party_abbr AS party, c.state,
                v.date AS lastVoteDate, count(*) AS votes
         FROM ${h.cast} c JOIN ${h.vote} v ON v.${h.id} = c.${h.id}
         WHERE v.date BETWEEN ? AND ?
         GROUP BY c.${h.member}
       ),
       first AS (
         SELECT c.${h.member} AS id, min(v.date) AS firstVoteDate
         FROM ${h.cast} c JOIN ${h.vote} v ON v.${h.id} = c.${h.id}
         WHERE v.date BETWEEN ? AND ?
         GROUP BY c.${h.member}
       )
       SELECT latest.*, first.firstVoteDate, d.parliamentary_name AS dName, d.person_id AS personId
       FROM latest JOIN first ON first.id = latest.id
       LEFT JOIN ${tbl} d ON d.${h.member} = latest.id`
    )
    .all(from, to, from, to) as Row[];
  const list = rows.map((r) => ({
    house,
    id: r.id as number,
    name: str(r.dName) ?? str(r.name),
    party: str(r.party),
    state: str(r.state),
    personId: num(r.personId),
    votes: r.votes as number,
    firstVoteDate: r.firstVoteDate as string,
    lastVoteDate: r.lastVoteDate as string,
  }));
  // A roster built while the identity table is mid-reset has no personId; don't freeze it.
  const identities = db().prepare(`SELECT 1 FROM ${tbl} WHERE person_id IS NOT NULL LIMIT 1`).get();
  if (list.length > 0 && identities !== undefined) rosterCache.set(key, list);
  return list;
}

export function listParliamentarians(opts: ListParliamentariansOptions): ParliamentarianListItem[] {
  if (!votesAvailable(opts.house)) return [];
  const uf = opts.uf?.trim().toUpperCase() || null;
  const q = opts.q?.trim().toLocaleLowerCase("pt-BR") ?? "";
  const tbl = opts.house === "camara" ? "camara_deputy" : "senate_senator";
  const cands = new Map<number, Array<Candidacy2026 & { state: string | null }>>();
  for (const r of db()
    .prepare(
      `SELECT person_id AS personId, office, state, candidate_number AS number, party_abbr AS party,
              ballot_name AS ballotName
       FROM politician_history
       WHERE year = 2026 AND office IS NOT NULL
         AND person_id IN (SELECT person_id FROM ${tbl} WHERE person_id IS NOT NULL)
       ORDER BY round, id`
    )
    .all() as Row[]) {
    const list = cands.get(r.personId as number) ?? [];
    list.push({
      office: r.office as string, number: str(r.number), party: str(r.party),
      ballotName: str(r.ballotName), state: str(r.state),
    });
    cands.set(r.personId as number, list);
  }
  const qf = q ? foldText(q) : "";
  const out: ParliamentarianListItem[] = [];
  for (const m of roster(opts.house, opts.period)) {
    const all = m.personId != null ? (cands.get(m.personId) ?? []) : [];
    const mine = opts.candidates2026 && opts.office ? all.filter((c) => c.office === opts.office) : all;
    const inUf = uf ? mine.find((c) => c.state === uf) : undefined;
    if (opts.candidates2026 && !(uf ? inUf : mine.length > 0)) continue;
    if (uf && !opts.candidates2026 && m.state !== uf) continue;
    if (qf && !foldText(m.name ?? "").includes(qf)) continue;
    const c = inUf ?? mine.find((x) => x.state === m.state) ?? mine[0];
    out.push({
      ...m,
      candidacy2026: c ? { office: c.office, number: c.number, party: c.party, ballotName: c.ballotName } : null,
    });
  }
  return out.sort((a, b) => (a.name ?? "").localeCompare(b.name ?? "", "pt-BR"));
}

// ---------------------------------------------------------------- /politico/[id]

export type PersonVotesSummary = {
  camara: ParliamentarianSummary[];
  senado: ParliamentarianSummary[];
};

/** Both houses for a people.id, via camara_deputy.person_id / senate_senator.person_id. */
export function getPersonVotesSummary(personId: number, opts: SummaryOptions = {}): PersonVotesSummary {
  const out: PersonVotesSummary = { camara: [], senado: [] };
  for (const house of ["camara", "senado"] as const) {
    if (!votesAvailable(house)) continue;
    const tbl = house === "camara" ? "camara_deputy" : "senate_senator";
    const ids = db()
      .prepare(`SELECT ${H[house].member} AS id FROM ${tbl} WHERE person_id = ? ORDER BY 1`)
      .all(personId) as Array<{ id: number }>;
    for (const { id } of ids) {
      const s = getDeputySummary(house, id, opts);
      if (s) out[house].push(s);
    }
  }
  return out;
}
