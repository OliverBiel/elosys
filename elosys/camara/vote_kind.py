"""What a "Sim" vote means in a plenary roll call, read from the vote's free-text description.

Neither house publishes a structured "what does Sim mean" field. The descriptions follow the clerks'
templates ("Rejeitado o Requerimento.", "Mantido o texto.", ...), and the rules below were written
against every distinct nominal PLEN description of 2019-2026 (Câmara bulk CSVs) plus Senado API
samples. When a pattern is ambiguous the result carries ``meaning_code=None``: the UI then shows
"interpretação indisponível" next to the original description. A rule never guesses.

General principle (Câmara): in "Aprovado/Rejeitado o X", the thing named (X) is what Sim supports;
the meaning does not depend on whether it was approved. The exception is the DVS result
"Mantido/Suprimido o texto", where Sim = keep the highlighted passage (the burden is on keeping it:
with PEC quorum the text falls with fewer than 308 Sim even when Sim > Não).

kind
    merito          main text of the bill (projeto, substitutivo, subemenda substitutiva global, MP/PLV)
    pec_turn        constitutional amendment (PEC) in 1st or 2nd turn
    highlight       destaque (separate vote on a passage, or on a party's change request)
    amendment       vote on an emenda
    final_wording   redação final or emenda de redação
    procedural      requerimento, urgência, preferência, recurso, admissibility of destaques/MPs
    nomination      approval of an authority (ambassador, CNJ, TCU, STF...) or secret election
    other           anything else, including committee opinions and unknown text

meaning_code (one plain-language sentence each; the UI copy lives in web vote-meaning.ts)
    approve_text              Sim = aprovar o texto principal da proposta.
    approve_committee_substitute
                              Sim = aprovar a proposta na versão da comissão (substitutivo), no lugar do
                              texto original.
    approve_senate_changes    Sim = aceitar as mudanças feitas pelo Senado (emendas ou substitutivo do Senado).
    approve_chamber_changes   Sim = aceitar as mudanças feitas pela Câmara (substitutivo da Câmara).
    pec_first_turn            Sim = aprovar a PEC em primeiro turno.
    pec_second_turn           Sim = aprovar a PEC em segundo turno.
    keep_highlighted_text     Sim = manter no texto o trecho destacado; Não = retirá-lo.
    approve_highlight         Sim = apoiar o destaque, a mudança pedida pelo partido autor; Não = manter o texto.
    approve_amendment         Sim = aprovar a emenda (mudança no texto).
    approve_wording_amendment Sim = aprovar a emenda de redação (ajuste na redação do texto).
    approve_final_wording     Sim = aprovar a redação final do texto já aprovado.
    approve_request           Sim = aprovar o requerimento (pedido sobre o andamento da votação).
    approve_urgency           Sim = aprovar a urgência (tramitação mais rápida).
    approve_preference        Sim = votar primeiro o texto ou a emenda indicada no pedido de preferência.
    admit_highlights          Sim = admitir os destaques simples para votação em separado.
    approve_appeal            Sim = aprovar o recurso.
    mp_meets_requirements     Sim = reconhecer que a medida provisória atende aos requisitos de relevância e urgência.
    approve_nomination        Sim = aprovar a indicação da autoridade.
    approve_opinion           Sim = aprovar o parecer da comissão; a conclusão do parecer está na descrição oficial.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import NamedTuple

CLASSIFIER_VERSION = "1.4"

KINDS = frozenset({
    "merito", "pec_turn", "highlight", "amendment", "final_wording", "procedural", "nomination", "other",
})

MEANING_CODES = frozenset({
    "approve_text", "approve_committee_substitute", "approve_senate_changes", "approve_chamber_changes",
    "pec_first_turn", "pec_second_turn", "keep_highlighted_text", "approve_highlight", "approve_amendment",
    "approve_wording_amendment", "approve_final_wording", "approve_request", "approve_urgency",
    "approve_preference", "admit_highlights", "approve_appeal", "mp_meets_requirements", "approve_nomination",
    "approve_opinion",
})

# Qualified quorum for keeping a PEC passage (3/5 of 513). "Suprimido o texto" with Sim at or above it
# cannot be explained by "Sim = keep".
_PEC_QUORUM = 308
# Descriptions may add oral votes to the panel count ("Sim: 487 (Computados os votos...)" in 2224225-63
# vs votosSim 484); 2019-2026 gaps of this kind stayed within 4 votes.
_ORAL_VOTE_TOLERANCE = 5


class VoteKind(NamedTuple):
    kind: str
    meaning_code: str | None
    basis: str


_NO_RULE = VoteKind("other", None, "no_rule")


def _norm(text: str | None) -> str:
    if not text:
        return ""
    # Keep NFKD: NFD leaves the ordinal "º" alone and breaks the Senado "1º Turno" rule. The degree sign
    # typed instead of it ("Emenda n° 1", "(1° Turno)") has no decomposition, hence the replace.
    no_accents = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return " ".join(no_accents.lower().replace("°", "o").split())


# "Sim: 330; não: 91", "Sim 311; total", "Sm: 12", "não: 389: total"
_SIM = re.compile(r"\bsi?m\b\s*:?\s*(\d+)")
_NAO = re.compile(r"\bnao\b\s*:?\s*(\d+)")


def _counts(d: str) -> tuple[int | None, int | None]:
    s, n = _SIM.search(d), _NAO.search(d)
    return (int(s.group(1)) if s else None, int(n.group(1)) if n else None)


_RESULT = r"^(?:aprovad|rejeitad|aprovacao|rejeicao)\w*,?(?: por unanimidade,)?"
_ART = r"(?:o |a |os |as )?"

_SECRET_ELECTION = re.compile(r"^votos:")
_MP_PRELIMINARY = re.compile(r"em apreciacao preliminar")
_MP_FAVORABLE = re.compile(r"opiniao favoravel quanto ao atendimento dos pressupostos")
_MP_NEGATIVE = re.compile(r"nao atendimento|inadequacao")
_COMMITTEE_OPINION = re.compile(_RESULT + r" " + _ART + r"parecer\b")
_OPINION_CONCLUSION = re.compile(r"\b(?:pela|que conclui)\b|\bpelo (?!relator)")
_APPEAL = re.compile(_RESULT + r" " + _ART + r"recursos?\b")
_REQUEST = re.compile(_RESULT + r" " + _ART + r"requerimentos?\b")
_URGENCY = re.compile(r"^[^.]*\brequerimentos? de urgencia\b")
_SPLIT_VOTE = re.compile(_RESULT + r" " + _ART + r"votacao parcelada\b")
_ADMIT_HIGHLIGHTS = re.compile(_RESULT + r" " + _ART + r"admissibilidade d[oa]s? destaques? simples?\b")
_PREFERENCE = re.compile(_RESULT + r" " + _ART + r"(?:preferencia|destaque de preferencia)\b")
_HIGHLIGHT = re.compile(_RESULT + r" " + _ART + r"(?:destaque|destque|detaque|dtq)\b")
_FINAL_WORDING = re.compile(_RESULT + r" " + _ART + r"redacao final\b")
_WORDING_AMENDMENT = re.compile(_RESULT + r" " + _ART + r"emendas? de redacao\b")
_TURN = re.compile(_RESULT + r" em (primeiro|segundo) turno\b")
_TURN_CLAUSE = re.compile(r",? em (?:primeiro|segundo) turno,?(?: nos termos d[^,]*,)?")
_OPENING_TURN = re.compile(r"\bem (primeiro|segundo) turno\b")
_PEC = re.compile(r"proposta de emenda (?:a constituicao|constitucional)|\bpec\b")
# Objects a PEC-turn vote can have and still mean "aprovar a PEC": the PEC or a text replacing it
# whole. Anything else voted "em primeiro turno" (e.g. an Emenda de Plenário) is not the PEC.
_PEC_WHOLE_TEXT = re.compile(
    _RESULT + r" " + _ART + r"(?:proposta de emenda|pec\b|substitutivo\b|emenda aglutinativa substitutiva\b)"
)
_SENATE_SUBSTITUTE = re.compile(_RESULT + r" (?:\bart\.|[^.;]){0,120}?\bsubstitutivo do senado\b")
_SENATE_AMENDMENT = re.compile(_RESULT + r" " + _ART + r"[^.;]{0,80}?\bemendas?\b[^.;]{0,60}?\bsenado\b")
_KEPT_CHAMBER_TEXT = re.compile(
    r"\b(?:mantid|restabelecid)[oa]s? (?:o texto|os dispositivos) aprovad[oa]s? pela camara"
)
_ANY_AMENDMENT = re.compile(r"\bemendas?\b")
_DVS = re.compile(r"^(mantido|suprimido) o texto\b")
_DVS_OTHER_TEXT = re.compile(r"^mantido o texto (?:do|da|aprovado)\b")
# Destaques whose aim is to put text IN (inclusion/substitution): Sim = the highlighted text enters, and the
# clerk's "Mantido/Suprimido o texto" then describes the base text, not the passage. Not safe to label.
_DVS_NOT_SUPPRESSIVE = re.compile(
    r"inclusao|para que seja incluid|incorpora|substituicao|para substituir|para que substitua"
    r"|em detrimento|prevaleca|restabelec"
)
# Quoted text is the passage under vote, not the destaque's aim ("incorporados" in 2454495-154).
# Rejecting a Senate article "e ao consequente restabelecimento" of the Câmara one still suppresses
# the highlighted passage (2242140-138, 2243079-168).
_QUOTED = re.compile(r'"[^"]*"|“[^”]*”')
_REJECT_AND_RESTORE = re.compile(r"\brejeicao e ao consequente restabelecimento\b")
_GLOBAL_SUBSTITUTE_AMENDMENT = re.compile(
    _RESULT + r" " + _ART + r"emenda (?:aglutinativa )?(?:substitutiva )?global\b"
)
_AMENDMENT = re.compile(
    _RESULT + r" " + _ART + r"(?:emendas?\b|(?:parte|art\.|art\b|artigo|paragrafo)[^;]{0,80}? da emenda\b)"
)
# "Aprovada a Emenda n° 1.": only the opening says the Senate wrote it (2267686-123, 2306513-113).
_OPENING_SENATE_AMENDMENT = re.compile(r"\bemendas?(?: n\S*)?(?: \d+)? do senado\b")
_NOT_SENATE_AMENDMENT = re.compile(r"\b(?:plenario|aglutinativa|comissao|relator\w*|subemenda)\b")
_SUBEMENDA_SUBSTITUTIVA = re.compile(
    _RESULT + r" " + _ART + r"sub(?:e)?menda substitutiva\b(?:[^.;]{0,30}?\b(?:global|ao projeto|oferecida ao"
    r"|adotada pel|apresentada ao))"
)
_MAIN_TEXT = re.compile(_RESULT + r" " + _ART + r"(?:projeto|substitutivo|medida provisoria)\b")
# A rejected committee substitute is not a rejected bill: the original is voted next (2397890-54
# 34x356, then 2397890-55 approved the original 407x6).
_SUBSTITUTE = re.compile(_RESULT + r" " + _ART + r"substitutivo\b")
_BY_COMMITTEE = re.compile(r"\b(?:comissao|relator\w*|ccjc)\b")
# The PDL's "(Aprova a indicação ...)" can follow "nº 1.118" or even the counts (2312515-18).
_NOMINATION_PDL = re.compile(r"\(aprova a indicacao\b")
_UNINFORMATIVE = re.compile(r"^(?:(?:aprovad|rejeitad)[oa]s?|resultado)\s*(?:[.;:]|$)")
_OPENING_SENATE_AMENDMENTS = re.compile(r"^votacao(?:, em globo,)? das emendas do senado\b")
_OPENING_FINAL_WORDING = re.compile(r"^votacao da redacao final\b")


def _classify_dvs(d: str, opening: str) -> VoteKind:
    if _DVS_OTHER_TEXT.search(d):
        return VoteKind("highlight", None, "dvs_other_house_text")
    aim = _REJECT_AND_RESTORE.sub("rejeicao", _QUOTED.sub(" ", opening))
    if _DVS_NOT_SUPPRESSIVE.search(aim):
        return VoteKind("highlight", None, "dvs_not_suppressive")
    sim, nao = _counts(d)
    kept = d.startswith("mantido")
    if sim is not None and nao is not None:
        if kept and sim <= nao:
            return VoteKind("highlight", None, "dvs_count_conflict")
        if not kept and sim > nao and sim >= _PEC_QUORUM:
            return VoteKind("highlight", None, "dvs_count_conflict")
    return VoteKind("highlight", "keep_highlighted_text", "dvs_keep_text")


def _pec_turn_from_opening(a: str) -> VoteKind:
    turn = _OPENING_TURN.search(a)
    if not turn:
        return VoteKind("pec_turn", None, "pec_turn_unknown")
    code = "pec_first_turn" if turn.group(1) == "primeiro" else "pec_second_turn"
    return VoteKind("pec_turn", code, f"pec_{turn.group(1)}_turno_opening")


def _classify_camara_text(d: str, a: str, pec_type: bool) -> VoteKind:
    if not d or _UNINFORMATIVE.match(d):
        if _OPENING_SENATE_AMENDMENTS.match(a):
            return VoteKind("amendment", "approve_senate_changes", "opening_senate_amendments")
        if _OPENING_FINAL_WORDING.match(a):
            return VoteKind("final_wording", "approve_final_wording", "opening_final_wording")
        return _NO_RULE
    if _SECRET_ELECTION.match(d):
        return VoteKind("nomination", None, "secret_election")
    if _MP_PRELIMINARY.search(d):
        if _MP_FAVORABLE.search(d) and not _MP_NEGATIVE.search(d):
            return VoteKind("procedural", "mp_meets_requirements", "mp_preliminary_favorable")
        # "Aprovado ... pelo não atendimento" occurs for MPs that then went on to merit votes in the same
        # session (e.g. MPV 905/2019, 1034/2021), so the clerk's wording cannot be trusted here.
        return VoteKind("procedural", None, "mp_preliminary_unreliable_wording")
    if _COMMITTEE_OPINION.match(d):
        # approve_opinion tells the voter the conclusion is in the description; only true when it is
        # (2236326-19 and 2423268-40 share one wording but opposite conclusions, stated elsewhere).
        if _OPINION_CONCLUSION.search(d.split(" sim", 1)[0]):
            return VoteKind("other", "approve_opinion", "committee_opinion")
        return VoteKind("other", None, "committee_opinion_no_conclusion")
    if _APPEAL.match(d):
        return VoteKind("procedural", "approve_appeal", "appeal")
    if _REQUEST.match(d):
        if _URGENCY.match(d):
            return VoteKind("procedural", "approve_urgency", "urgency_request")
        return VoteKind("procedural", "approve_request", "request")
    if _SPLIT_VOTE.match(d):
        return VoteKind("procedural", "approve_request", "split_vote_request")
    if _ADMIT_HIGHLIGHTS.match(d):
        return VoteKind("procedural", "admit_highlights", "simple_highlights_admissibility")
    if _PREFERENCE.match(d):
        return VoteKind("procedural", "approve_preference", "preference")
    if _HIGHLIGHT.match(d):
        return VoteKind("highlight", "approve_highlight", "highlight_result")
    if _FINAL_WORDING.match(d):
        return VoteKind("final_wording", "approve_final_wording", "final_wording")
    if _WORDING_AMENDMENT.match(d):
        return VoteKind("final_wording", "approve_wording_amendment", "wording_amendment")
    head = d.split(" sim", 1)[0]
    is_pec = pec_type or bool(_PEC.search(head))
    turn = _TURN.match(d)
    if turn:
        without_turn = _TURN_CLAUSE.sub("", d, count=1)
        if is_pec:
            if not _PEC_WHOLE_TEXT.match(without_turn):
                return VoteKind("pec_turn", None, "pec_turn_other_object")
            code = "pec_first_turn" if turn.group(1) == "primeiro" else "pec_second_turn"
            return VoteKind("pec_turn", code, f"pec_{turn.group(1)}_turno")
        if _MAIN_TEXT.match(without_turn):
            return VoteKind("merito", "approve_text", "two_turn_non_pec")
        return _NO_RULE
    if _SENATE_SUBSTITUTE.match(d):
        return VoteKind("merito", "approve_senate_changes", "senate_substitute")
    if _ANY_AMENDMENT.search(d) and (_SENATE_AMENDMENT.match(d) or _KEPT_CHAMBER_TEXT.search(d)):
        return VoteKind("amendment", "approve_senate_changes", "senate_amendment")
    if _DVS.match(d):
        return _classify_dvs(d, a)
    # A PEC substitute or emenda aglutinativa substitutiva replaces the PEC's whole text: it is the PEC
    # itself, not an amendment (2293449-176). The turn is sometimes only in the opening.
    if is_pec and _PEC_WHOLE_TEXT.match(d):
        return _pec_turn_from_opening(a)
    if _GLOBAL_SUBSTITUTE_AMENDMENT.match(d):
        return VoteKind("merito", "approve_text", "global_substitute_amendment")
    if _AMENDMENT.match(d):
        if _OPENING_SENATE_AMENDMENT.search(a) and not _NOT_SENATE_AMENDMENT.search(head):
            return VoteKind("amendment", "approve_senate_changes", "senate_amendment_opening")
        return VoteKind("amendment", "approve_amendment", "amendment")
    if _SUBEMENDA_SUBSTITUTIVA.match(d):
        return VoteKind("merito", "approve_text", "subemenda_substitutiva")
    if _MAIN_TEXT.match(d):
        if _NOMINATION_PDL.search(d):
            return VoteKind("nomination", "approve_nomination", "nomination_pdl")
        if _SUBSTITUTE.match(d) and _BY_COMMITTEE.search(head):
            return VoteKind("merito", "approve_committee_substitute", "committee_substitute")
        return VoteKind("merito", "approve_text", "main_text")
    return _NO_RULE


def classify_camara(
    description: str | None,
    opening_description: str | None,
    yes_count: int | None = None,
    no_count: int | None = None,
    *,
    proposition_type: str | None = None,
) -> VoteKind:
    """Classify a Câmara PLEN vote from ``descricao`` and ``ultimaAberturaVotacao_descricao``.

    ``yes_count``/``no_count`` (votosSim/votosNao) are optional. When given and the "Sim: N; Não: M" quoted
    in the description is off by more than a few votes, the description and the vote rows do not describe
    the same vote (2271124-70: 276x155 vs 122x271; 2399598-70: "Rejeitada" 35x375 vs 315x87), so the
    meaning is dropped. Small gaps are clerks adding oral votes ("Computado o voto verbal...").

    ``proposition_type`` (optional siglaTipo, e.g. "PEC") marks the vote as a PEC vote when the description
    does not name the PEC ("Aprovado o Substitutivo..." under a PEC). It never overrides an explicit
    "Projeto"/"Medida Provisória" object in the description.
    """
    d, a = _norm(description), _norm(opening_description)
    pec_type = _norm(proposition_type) == "pec"
    result = _classify_camara_text(d, a, pec_type)
    if result.meaning_code is not None and yes_count is not None and no_count is not None:
        sim, nao = _counts(d)
        if (sim is not None and abs(sim - yes_count) > _ORAL_VOTE_TOLERANCE) or (
            nao is not None and abs(nao - no_count) > _ORAL_VOTE_TOLERANCE
        ):
            return VoteKind(result.kind, None, "description_count_mismatch")
    return result


_S_NOMINATION = re.compile(
    r"^(?:votacao(?: nominal)? (?:d[oa] )?)?(?:mensagem|msf|oficio)\b[^-]*\s-\s"
    r"|^escolha d[oa] senhora?\b|^indicacao d[oa]\b"
    # 2019 wording: "Submete à consideração do Senado Federal, nos termos do art. 84 ..., o nome do",
    # "Submetendo à apreciação do Senado a indicação/recondução do" (6009, 6019, 5944).
    r"|^submet(?:e|endo) a (?:apreciacao|consideracao) do senado\b.{0,300}?"
    r"\b(?:a escolha|o nome|a indicacao|a reconducao) d[oa]\b"
)
_S_REQUEST = re.compile(r"^(?:votacao(?: nominal)? d[oa]s? |(?:aprovad|rejeitad)[oa] [oa] )(?:rqs|requerimentos?)\b")
# 6486-6488: one roll call for three PDLs was a secret election among three TCU candidates
# (Sim = votes cast), so Sim/Não do not apply.
_S_MULTIPLE_PROJECTS = re.compile(r"^votacao(?: nominal)? dos projetos\b")
_S_PEC = re.compile(r"\bpec\b|proposta de emenda a constituicao|^votacao(?: nominal)? da proposta\b")
_S_PEC_IDENTIFICATION = re.compile(r"^pec\b")
_S_TURN = re.compile(r"\b(1o|primeiro|2o|segundo) turno\b")
_S_HIGHLIGHTED = re.compile(r"\bdestacad[oa]s?\b")
_S_AMENDMENT_OBJECT = re.compile(r"^(?:votacao(?: nominal)? da )?emenda (?:n|\d)")
# Destaque of amendments, not of the text: plural (6166), parts (6335), subemenda (6881).
_S_HIGHLIGHTED_AMENDMENT = re.compile(
    r"^(?:votacao(?: nominal)?(?: conjunta)? d[aeo]s? )?(?:(?:primeira|segunda) )?(?:parte d[ae] )?"
    r"(?:sub)?emendas?\b"
)
# A numbered emenda named inside a destacado passage; group 1 = marked as the substitute
# ("Emenda 55(Subst.)", "Emenda nº 55-PLEN - Substitutivo"), not merely near "ao Substitutivo".
_S_EMENDA_REFERENCE = re.compile(r"\bemenda (?:n[o.]?s? ?)?\d[\d.]*(\s*(?:-\s*plen\s*)?[(-]?\s*subst)?")
# "(Subst.)" (6099, 6147) and a number without "nº" (6730: "Emenda 15 (Substitutivo)").
_S_SUBSTITUTE_AMENDMENT = re.compile(
    r"^(?:votacao(?: nominal)? da )?emenda (?:n|\d)[^(]{0,15}\((?:substitutivo|subst\.?)\)"
)
# A PEC-turn vote on something other than the PEC or its substitute (6092: "Votação da Redação
# proposta pelo Relator, em substituição às Emendas nºs 34 e 45 à PEC nº 10/2020 (1º Turno)").
_S_PEC_OTHER_OBJECT = re.compile(
    r"^votacao(?: nomina[l]?)?,?(?: em (?:primeiro|segundo|1o|2o) turno,?)? d[oa]s? "
    r"(?!proposta\b|pec\b|emenda n[^(]{0,15}\(substitutivo\)|(?:1o|2o|primeiro|segundo) turno\b)"
)
_S_CHAMBER_SUBSTITUTE = re.compile(r"^votacao(?: nominal)? do substitutivo da (?:camara dos deputados|cd)\b")
# A destaque on a Câmara substitute: identificacao "PL 550/2019 (Substitutivo-CD)" or "SCD 6/2016";
# the description alone may not say it (6174 "previsto no art. 4º do Substitutivo"). Web twin:
# senadoCamaraSubstitute() in votes-queries.ts.
_S_CHAMBER_SUBSTITUTE_ID = re.compile(r"substitutivo-cd|^scd\b")
_S_ON_CHAMBER_SUBSTITUTE = re.compile(r"\bsubstitutivo da (?:camara|cd)\b")
_S_MAIN_TEXT = re.compile(
    r"^votacao(?: nominal)? d[oa] (?:projeto|pl|plp|plv|pdl|pls|plc|prs|medida provisoria|mpv)\b"
)


# Secret MSF/OFS votes 2019-2026 are authority confirmations even when the description only says
# "Votação nominal." or lacks the " - " before the name ("Mensagem nº 7, de 2021- Vinicius Clay").
_S_SECRET_AUTHORITY = re.compile(r"^(?:msf|ofs)\b")


def classify_senate(
    description: str | None, identification: str | None = None, *, is_secret: bool = False,
) -> VoteKind:
    """Classify a Senado plenary vote; see ``_classify_senate_text``. ``is_secret`` plus an MSF/OFS
    ``identification`` turns an otherwise unclassified vote into a nomination; open votes never."""
    result = _classify_senate_text(description, identification)
    unclassified = result.kind == "other" and result.meaning_code is None
    if is_secret and unclassified and _S_SECRET_AUTHORITY.match(_norm(identification)):
        return VoteKind("nomination", "approve_nomination", "senate_secret_msf_ofs")
    return result


def _classify_senate_text(description: str | None, identification: str | None = None) -> VoteKind:
    """Classify a Senado plenary vote from ``descricaoVotacao``.

    Many Senado descriptions are only the topic of a destaque ("Idade mínima para mulheres.") or the ementa;
    those stay unclassified. Do not use ``resultadoVotacao`` to fill the gap: for PEC 6/2019 destaques it
    reports the destaque's fate ("A" with Sim 42 < 49 quorum), not whether Sim won.

    ``identification`` (optional ``identificacao``, e.g. "PEC 45/2019") tells a PEC vote apart when the
    description does not name it ("Votação nominal, em 1º Turno", "Emenda nº 194 (Substitutivo)").
    """
    s = _norm(description)
    if not s:
        return _NO_RULE
    if _S_NOMINATION.search(s):
        return VoteKind("nomination", "approve_nomination", "senate_nomination")
    if _S_REQUEST.match(s):
        return VoteKind("procedural", "approve_request", "senate_request")
    if _S_MULTIPLE_PROJECTS.match(s):
        return VoteKind("other", None, "senate_multiple_projects")
    if _S_HIGHLIGHTED.search(s):
        return _classify_senate_highlight(s, _norm(identification))
    substitute = bool(_S_SUBSTITUTE_AMENDMENT.match(s))
    if _S_AMENDMENT_OBJECT.match(s) and not substitute:
        return VoteKind("amendment", "approve_amendment", "senate_amendment")
    is_pec = bool(_S_PEC.search(s) or _S_PEC_IDENTIFICATION.match(_norm(identification)))
    turn = _S_TURN.search(s)
    if is_pec and turn:
        if _S_PEC_OTHER_OBJECT.match(s):
            return VoteKind("pec_turn", None, "senate_pec_turn_other_object")
        first = turn.group(1) in ("1o", "primeiro")
        return VoteKind("pec_turn", "pec_first_turn" if first else "pec_second_turn", "senate_pec_turn")
    if _S_CHAMBER_SUBSTITUTE.match(s):
        return VoteKind("merito", "approve_chamber_changes", "senate_chamber_substitute")
    if substitute:
        if is_pec:
            return VoteKind("pec_turn", None, "senate_pec_turn_unknown")
        return VoteKind("merito", "approve_text", "senate_substitute_amendment")
    if _S_MAIN_TEXT.match(s):
        return VoteKind("merito", "approve_text", "senate_main_text")
    return _NO_RULE


def _classify_senate_highlight(s: str, identification: str = "") -> VoteKind:
    """A destacado item: Sim = approve it when it is an amendment, Sim = keep it when it is the text.

    Sim on a destacado amendment adds it (6166 "Aprovadas as Emendas 11 e 15"; 6335 Sim removed a
    paragraph via Emenda 91), so "Sim = manter" would invert it. "§ 11 constante da Emenda nº 1" is
    the amendment's own text, not in the approved text (6915). A passage of the substitute ("Emenda
    55(Subst.)", 6663) is the text. A mix of the two, or another "subst" nearby: None.

    On a Câmara substitute the Senado picks between its own and the Câmara's version, and the record
    does not say which one Sim backs: 6174 Sim 52 x 22 "Rejeitado o art. 17-E", 6113 Sim 62 x 15
    "Aprovado o art. 18". A PLC ("Substitutivo ao Projeto de Lei da Câmara", 6837) is not one.
    """
    if _S_CHAMBER_SUBSTITUTE_ID.search(identification) or _S_ON_CHAMBER_SUBSTITUTE.search(s):
        return VoteKind("highlight", None, "senate_highlight_on_chamber_substitute")
    if _S_HIGHLIGHTED_AMENDMENT.match(s):
        return VoteKind("amendment", "approve_amendment", "senate_highlighted_amendment")
    references = _S_EMENDA_REFERENCE.findall(s)
    substitutes = [r for r in references if r]
    if len(substitutes) == len(references):
        return VoteKind("highlight", "keep_highlighted_text", "senate_highlighted_text")
    if substitutes or "subst" in s:
        return VoteKind("highlight", None, "senate_highlighted_unclear")
    return VoteKind("amendment", "approve_amendment", "senate_highlighted_amendment_passage")


def coverage_report(rows: Iterable[VoteKind | Mapping[str, object] | tuple]) -> dict:
    """Counts per kind/basis and share of rows with a meaning. Rows: classifier results or dicts."""
    by_kind: Counter[str] = Counter()
    by_basis: Counter[str] = Counter()
    total = with_meaning = 0
    for row in rows:
        if isinstance(row, Mapping):
            kind, meaning, basis = row.get("kind"), row.get("meaning_code"), row.get("basis")
        else:
            kind, meaning, basis = row[0], row[1], row[2]
        total += 1
        by_kind[str(kind)] += 1
        by_basis[str(basis)] += 1
        if meaning is not None:
            with_meaning += 1
    return {
        "classifier_version": CLASSIFIER_VERSION,
        "total": total,
        "with_meaning": with_meaning,
        "with_meaning_pct": round(100.0 * with_meaning / total, 1) if total else 0.0,
        "by_kind": dict(by_kind),
        "by_basis": dict(by_basis),
    }
