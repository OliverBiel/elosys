// Plain-language copy for roll-call votes. Pure TS, no SQL: safe in client components.
// Codes come from elosys/camara/vote_kind.py (CLASSIFIER_VERSION 1.2) and elosys/schema.sql.

export type House = "camara" | "senado";

export const HOUSE_LABEL: Record<House, string> = {
  camara: "Câmara dos Deputados",
  senado: "Senado Federal",
};

/** "na Câmara dos Deputados" / "no Senado Federal" (gender of the house name). */
export const IN_HOUSE: Record<House, string> = {
  camara: "na Câmara dos Deputados",
  senado: "no Senado Federal",
};

/** "da Câmara dos Deputados" / "do Senado Federal". */
export const OF_HOUSE: Record<House, string> = {
  camara: "da Câmara dos Deputados",
  senado: "do Senado Federal",
};

/** How each source names parties on old votes (the Senado rewrites renamed parties). */
export const PARTY_NAME_NOTE: Record<House, string> = {
  camara: "Na Câmara, cada voto traz a sigla da época da votação, sem fusões.",
  senado:
    "No Senado, a fonte mostra a sigla atual do partido mesmo em votos antigos (ex.: PRB " +
    "aparece como Republicanos, PR como PL, PPS como Cidadania).",
};

// ---------------------------------------------------------------- what "Sim" means

export const MEANING_CODES = [
  "approve_text",
  "approve_committee_substitute",
  "approve_senate_changes",
  "approve_chamber_changes",
  "pec_first_turn",
  "pec_second_turn",
  "keep_highlighted_text",
  "approve_highlight",
  "approve_amendment",
  "approve_wording_amendment",
  "approve_final_wording",
  "approve_request",
  "approve_urgency",
  "approve_preference",
  "admit_highlights",
  "approve_appeal",
  "mp_meets_requirements",
  "approve_nomination",
  "approve_opinion",
] as const;

export type MeaningCode = (typeof MEANING_CODES)[number];

export const MEANING_UNAVAILABLE = "Interpretação indisponível — leia a descrição oficial da votação.";

const MEANING_TEXT: Record<MeaningCode, string> = {
  approve_text: "Votar Sim = aprovar o texto principal da proposta; Não = rejeitá-lo.",
  approve_committee_substitute:
    "Votar Sim = aprovar a proposta na versão da comissão (substitutivo), no lugar do texto " +
    "original; Não = rejeitar essa versão.",
  approve_senate_changes:
    "Votar Sim = aceitar as mudanças feitas pelo Senado; Não = rejeitá-las e ficar com o texto " +
    "da Câmara.",
  approve_chamber_changes:
    "Votar Sim = aceitar as mudanças feitas pela Câmara (substitutivo da Câmara); Não = " +
    "rejeitá-las e ficar com o texto do Senado.",
  pec_first_turn:
    "Votar Sim = aprovar a PEC (mudança na Constituição) em primeiro turno. Para passar, " +
    "precisa de 3/5 da Casa (308 deputados ou 49 senadores), não só de mais Sim que Não.",
  pec_second_turn:
    "Votar Sim = aprovar a PEC (mudança na Constituição) em segundo turno. Para passar, " +
    "precisa de 3/5 da Casa (308 deputados ou 49 senadores), não só de mais Sim que Não.",
  keep_highlighted_text: "Votar Sim = manter o trecho destacado no texto; Não = retirá-lo.",
  approve_highlight:
    "Votar Sim = apoiar o destaque, isto é, a mudança pedida pelo partido autor; Não = manter " +
    "o texto como está.",
  approve_amendment: "Votar Sim = aprovar a emenda (mudança no texto); Não = rejeitá-la.",
  approve_wording_amendment:
    "Votar Sim = aprovar a emenda de redação (ajuste na redação do texto); Não = rejeitá-la.",
  approve_final_wording:
    "Votar Sim = aprovar a redação final do texto já aprovado; Não = rejeitá-la.",
  approve_request:
    "Votar Sim = aprovar o requerimento (um pedido sobre o andamento da votação, como adiar " +
    "ou retirar de pauta; o pedido exato está na descrição oficial); Não = rejeitá-lo.",
  approve_urgency:
    "Votar Sim = aprovar a urgência (a proposta passa a tramitar mais rápido); Não = rejeitá-la.",
  approve_preference:
    "Votar Sim = votar primeiro o texto ou a emenda indicada no pedido de preferência; Não = " +
    "manter a ordem normal de votação.",
  admit_highlights:
    "Votar Sim = admitir os destaques simples para votação em separado; Não = não votá-los " +
    "em separado.",
  approve_appeal:
    "Votar Sim = aprovar o recurso (o que se contesta está na descrição oficial); Não = " +
    "rejeitá-lo.",
  mp_meets_requirements:
    "Votar Sim = reconhecer que a medida provisória atende aos requisitos de relevância e " +
    "urgência, e ela segue para a votação do conteúdo; Não = considerar que não atende.",
  approve_nomination:
    "Votar Sim = aprovar a indicação da autoridade (o nome está na descrição oficial); Não = " +
    "rejeitá-la.",
  approve_opinion:
    "Votar Sim = aprovar o parecer da comissão; a conclusão do parecer está na descrição oficial.",
};

export function isMeaningCode(code: string | null | undefined): code is MeaningCode {
  return code != null && (MEANING_CODES as readonly string[]).includes(code);
}

/** Sentence for a meaning_code; unknown or NULL code -> MEANING_UNAVAILABLE. */
export function meaningSentence(code: string | null | undefined): string {
  return isMeaningCode(code) ? MEANING_TEXT[code] : MEANING_UNAVAILABLE;
}

// ---------------------------------------------------------------- kind

export const VOTE_KINDS = [
  "merito",
  "pec_turn",
  "highlight",
  "amendment",
  "final_wording",
  "procedural",
  "nomination",
  "other",
] as const;

export type VoteKind = (typeof VOTE_KINDS)[number];

const KIND_LABEL: Record<VoteKind, string> = {
  merito: "Texto principal",
  pec_turn: "PEC",
  highlight: "Destaque",
  amendment: "Emenda",
  final_wording: "Redação final",
  procedural: "Procedimental",
  nomination: "Indicação/eleição",
  other: "Outro",
};

/** Kind label; PEC votes carry the turn when meaning_code says it ("PEC — 1º turno"). */
export function kindLabel(kind: string | null | undefined, meaningCode?: string | null): string {
  if (kind === "pec_turn") {
    if (meaningCode === "pec_first_turn") return "PEC — 1º turno";
    if (meaningCode === "pec_second_turn") return "PEC — 2º turno";
    return KIND_LABEL.pec_turn;
  }
  return (VOTE_KINDS as readonly string[]).includes(kind ?? "")
    ? KIND_LABEL[kind as VoteKind]
    : KIND_LABEL.other;
}

/** Requerimento/urgência/preferência/recurso are procedural; NULL kind counts as "other". */
export const PROCEDURAL_KINDS: readonly VoteKind[] = ["procedural"];
export const MERIT_KINDS: readonly VoteKind[] = VOTE_KINDS.filter((k) => k !== "procedural");

export function isProcedural(kind: string | null | undefined): boolean {
  return kind === "procedural";
}

// ---------------------------------------------------------------- vote values

// Stored values (schema CHECKs) plus Câmara states derived at query time. present_not_voted is
// only the stored Senado P-NRV; the Câmara presence-list state is session_present_not_voted.
export type VoteValue =
  | "yes"
  | "no"
  | "abstention"
  | "obstruction"
  | "presiding"
  | "secret"
  | "present_not_voted"
  | "session_present_not_voted"
  | "absent"
  | "absent_justified"
  | "other"
  | "indeterminate"
  | "unknown";

/** Values that state a position: the only ones used by majority, fidelity and agreement. */
export const POSITION_VALUES = ["yes", "no", "obstruction", "abstention"] as const;
export type PositionValue = (typeof POSITION_VALUES)[number];

export function isPositionValue(v: string | null | undefined): v is PositionValue {
  return v === "yes" || v === "no" || v === "obstruction" || v === "abstention";
}

/** Label for a vote value. Senado P-NRV is never called "presente" (official page may say NCom). */
export function voteValueLabel(value: string, house: House, raw?: string | null): string {
  switch (value) {
    case "yes":
      return "Sim";
    case "no":
      return "Não";
    case "abstention":
      return "Abstenção";
    case "obstruction":
      return "Obstrução";
    case "presiding":
      return house === "camara" ? "Presidindo a sessão (Art. 17)" : "Presidindo a sessão (art. 51 RISF)";
    case "secret":
      return "Voto secreto";
    case "present_not_voted":
      return "Não registrou voto";
    case "session_present_not_voted":
      return "Registrou presença na sessão, mas não votou nesta votação";
    case "absent":
      return "Ausente";
    case "absent_justified":
      return raw ? `Ausência justificada (${raw})` : "Ausência justificada";
    case "indeterminate":
      return "Indeterminado (não dá para afirmar ausência)";
    case "unknown":
      return "Desconhecido";
    case "other":
      if (house === "senado" && raw === "NA") return "Ausência — dispositivo não citado (NA)";
      return `Outro (código: ${raw ?? "?"})`;
    default:
      return `Outro (código: ${raw ?? value})`;
  }
}

/** Why a Câmara non-voter is "indeterminate" (DEFINITIONS.presenceCamara). */
export type IndeterminateReason = "status_change" | "no_presence_list";

export function indeterminateLabel(reason: IndeterminateReason | null | undefined): string {
  if (reason === "status_change") return "Indeterminado (situação do mandato mudou nesse dia)";
  if (reason === "no_presence_list") return "Indeterminado (a sessão não tem lista de presença)";
  return voteValueLabel("indeterminate", "camara");
}

// ---------------------------------------------------------------- orientation

export type OrientationValue = "yes" | "no" | "obstruction" | "free" | "none" | "other";

/** Orientations that count for fidelity and agreement (§3: Liberado/vazio fora). */
export const ORIENTED_VALUES = ["yes", "no", "obstruction"] as const;
export type OrientedValue = (typeof ORIENTED_VALUES)[number];

export function isOrientedValue(v: string | null | undefined): v is OrientedValue {
  return v === "yes" || v === "no" || v === "obstruction";
}

/**
 * value null = no orientation for the party (Câmara: unmatched bench; Senado: unpublished).
 * voteHasOrientations false = the Câmara vote has no orientation row at all (not a mapping gap).
 */
export function orientationLabel(
  value: string | null | undefined,
  house: House,
  raw?: string | null,
  voteHasOrientations = true
): string {
  switch (value) {
    case "yes":
      return "Sim";
    case "no":
      return "Não";
    case "obstruction":
      return "Obstrução";
    case "free":
      return "Liberado (a bancada liberou o voto)";
    case "none":
      return "Não orientou";
    case "other":
      return `Outro (orientação: ${raw ?? "?"})`;
    default:
      if (house === "senado") return "Sem orientação publicada";
      return voteHasOrientations ? "Sem orientação identificada" : "Sem orientação registrada";
  }
}

// ---------------------------------------------------------------- party position

/** Majority position (§3); "split" = tie between the most frequent values ("dividido"). */
export type MajorityPosition = PositionValue | "split";

/** secretVotes: the party's 'secret' casts; with them a null majority is unknown, not empty. */
export function majorityLabel(m: MajorityPosition | null, secretVotes = 0): string {
  if (m === null) return secretVotes > 0 ? "Posição desconhecida (voto secreto)" : "Nenhum voto registrado";
  if (m === "split") return "Dividido";
  return voteValueLabel(m, "camara");
}

// ---------------------------------------------------------------- government

export type GovernmentId = "bolsonaro" | "lula";

export type GovernmentPeriod = {
  id: GovernmentId;
  label: string;
  president: string;
  start: string;
  end: string | null;
};

export const GOVERNMENT_PERIODS: readonly GovernmentPeriod[] = [
  { id: "bolsonaro", label: "Governo Bolsonaro", president: "Jair Bolsonaro", start: "2019-01-01", end: "2022-12-31" },
  { id: "lula", label: "Governo Lula", president: "Luiz Inácio Lula da Silva", start: "2023-01-01", end: null },
];

/** Government in office on a YYYY-MM-DD date; null before 2019-01-01. */
export function governmentAt(date: string): GovernmentPeriod | null {
  const day = date.slice(0, 10);
  return GOVERNMENT_PERIODS.find((g) => day >= g.start && (g.end === null || day <= g.end)) ?? null;
}

// ---------------------------------------------------------------- legislatures

export const LEGISLATURES = [
  { id: 56, start: "2019-02-01", end: "2023-01-31", label: "56ª legislatura (2019–2023)" },
  { id: 57, start: "2023-02-01", end: "2027-01-31", label: "57ª legislatura (2023–2027)" },
] as const;

// ---------------------------------------------------------------- definitions (UI text)

// Each metric in votes-queries.ts cites the key it implements; change both together.
export const DEFINITIONS = {
  majorityPosition:
    "Posição majoritária do partido: o voto mais frequente (Sim, Não, Obstrução ou Abstenção) " +
    "entre os parlamentares do partido que votaram. Empate entre os mais frequentes = " +
    "\"dividido\". Voto secreto, presidência da sessão e ausências não entram. No Senado, " +
    "quando a liderança do partido orientou obstrução, a posição majoritária fica fora das " +
    "comparações (concordância e Governo): quem obstrui aparece como \"não registrou voto\", " +
    "e a maioria sairia só dos poucos que votaram.",
  fidelity:
    "Fidelidade: votos iguais à orientação do partido ÷ votos dados (Sim, Não, Obstrução ou " +
    "Abstenção) nas votações em que o partido orientou Sim, Não ou Obstrução. Ficam de fora " +
    "as votações em que o partido liberou o voto, não orientou ou não teve orientação " +
    "identificada, e votos secretos e a presidência da sessão (Art. 17 na Câmara; art. 51 do " +
    "Regimento no Senado). No Senado, orientação de obstrução fica fora: quem obstrui aparece " +
    "como \"não registrou voto\".",
  agreement:
    "Concordância entre partidos: % das votações em que os dois partidos tiveram a mesma " +
    "posição, contando só as votações em que ambos tiveram posição Sim, Não ou Obstrução " +
    "(pela orientação ou pela posição majoritária, conforme escolhido). Obstrução e Não " +
    "contam como posições diferentes. N = número de votações comparadas. Pela orientação, " +
    "partidos da mesma federação ou bloco recebem a mesma orientação; a célula mostra em " +
    "quantas votações foi assim.",
  agreementParliamentarians:
    "Concordância entre parlamentares: % das votações em que os dois votaram igual, contando " +
    "só as votações em que ambos votaram Sim, Não, Obstrução ou Abstenção.",
  government:
    "Governo: a orientação da liderança do Governo na votação, rotulada com o governo da data " +
    "(Bolsonaro até 31/12/2022, Lula a partir de 01/01/2023). Concordância com o Governo = " +
    "% das votações em que a posição (orientação do partido, posição majoritária ou voto do " +
    "parlamentar) foi igual à orientação do Governo, contando só as votações em que o " +
    "Governo orientou Sim, Não ou Obstrução e o lado comparado tinha posição (orientação Sim, " +
    "Não ou Obstrução; maioria Sim, Não ou Obstrução; voto Sim, Não, Obstrução ou Abstenção). " +
    "A concordância é calculada separadamente para cada governo (Bolsonaro e Lula); nunca " +
    "somamos os dois. No Senado, quando o Governo orienta obstrução, a votação fica fora das " +
    "comparações com votos e com a maioria (quem obstrui aparece como \"não registrou voto\").",
  withPartyMajority:
    "Com a maioria do partido: % das votações em que o voto do parlamentar (Sim, Não, " +
    "Obstrução ou Abstenção) foi igual à posição majoritária dos demais parlamentares do " +
    "partido, sem contar o próprio voto. Votações em que nenhum outro parlamentar do partido " +
    "votou ficam de fora.",
  voted:
    "Votou: votações em que o parlamentar registrou voto (Sim, Não, Abstenção, Obstrução ou " +
    "voto secreto) ÷ votações em que estava em exercício do mandato (Câmara) ou em que aparece " +
    "na lista oficial da votação (Senado). É a mesma conta nas duas Casas, então dá para " +
    "comparar deputados e senadores. Votações em que presidiu a sessão ficam fora da conta: " +
    "quem preside não vota. Pelo regimento, o presidente da Casa em regra só vota em votação " +
    "secreta ou para desempatar, mesmo quando outro parlamentar está na cadeira: para quem " +
    "presidiu a Câmara ou o Senado no período, o % fica baixo por regra, não por falta. No " +
    "Senado, votação que lista só quem votou e em que o senador não aparece fica fora " +
    "(situação desconhecida).",
  presenceCamara:
    "Presença na sessão (Câmara, dado complementar): votou, presidiu ou estava na lista de " +
    "presença da sessão ÷ essas votações + ausências. A lista de presença vale para a sessão " +
    "inteira, não para cada votação: estar nela não quer dizer que votou. Ausente = sem voto " +
    "registrado, em exercício do mandato na hora da votação, fora da lista de presença e sem " +
    "mudança de situação do mandato naquele dia. Em dia de mudança de situação (posse, " +
    "licença, saída) sem voto, não afirmamos nada: \"indeterminado\". O mesmo vale quando a " +
    "sessão não tem lista de presença. Fora do exercício do mandato não conta. O Senado não " +
    "publica essa lista; para comparar as Casas, use \"votou\".",
  presenceSenado:
    "Lista oficial do Senado (dado complementar): \"não registrou voto\" (P-NRV) = o senador " +
    "aparece na lista da votação, mas sem voto; a página oficial às vezes mostra essa pessoa " +
    "como ausente. A lista também traz ausências justificadas (licença, missão) e ausências " +
    "sem justificativa (NCom). Quando a votação lista só quem votou, quem não aparece fica " +
    "como \"desconhecido\", nunca como ausente, e fica fora das contas.",
  senadoDestaque:
    "Destaques no Senado: o código de resultado que o Senado publica num destaque (A ou R) " +
    "diz o que aconteceu com o pedido de destaque, não se o texto ficou. Por isso o resultado " +
    "mostrado sai do placar: mais Sim que Não mantém o trecho destacado ou aprova a emenda " +
    "(em PEC, são precisos pelo menos 49 votos Sim; em projeto de lei complementar, 41). " +
    "Quando não dá para ter certeza, mostramos \"resultado do destaque\" e o registro oficial.",
  senadoOrientation:
    "No Senado, a orientação dos partidos só é publicada em parte das votações, e a parte " +
    "coberta muda muito de um ano para outro (veja a cobertura por ano). Sem orientação publicada para " +
    "o partido, mostramos \"sem orientação publicada\" e usamos só os votos reais (posição " +
    "majoritária). Fidelidade e concordância por orientação no Senado valem só para as " +
    "votações com orientação publicada.",
  camaraBench:
    "Na Câmara, a orientação vem por bancada (partido, federação ou bloco). Quando não há " +
    "documento oficial que ligue o partido a uma bancada naquela data, mostramos \"sem " +
    "orientação identificada\" e a votação não entra na fidelidade.",
  meaning:
    "\"O que o Sim significa\" é lido da descrição oficial da votação por regras fixas. Quando " +
    "a regra não tem certeza, mostramos \"interpretação indisponível\". A descrição oficial " +
    "aparece sempre junto.",
} as const;

export type DefinitionKey = keyof typeof DEFINITIONS;
