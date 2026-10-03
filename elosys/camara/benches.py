"""Which orientation row (siglaBancada of votacoesOrientacoes) speaks for a party in a Câmara vote.

The bulk file lists one orientation per *bancada*: a party, a federation ("Fdr PT-PCdoB-PV"), a bloc
whose label is a camel-case concatenation of member abbreviations, usually truncated
("Bl UniPpPsd..."), or a leadership (Governo/Maioria/Minoria/Oposição). Labels are also truncated to
10 characters ("Solidaried", "Republican") and change case between years ("UNIÃO"/"União").

``map_party_to_bench`` links a party to a row only on unambiguous evidence, in this order:

1. ``party``        — the party's own row (after alias normalization);
2. ``federation``   — a federation row whose (untruncated) label lists the party;
3. ``bloc_api``     — a bloc row resolved through the Câmara API: the label's visible tokens must
                      match, in order, the first members of exactly one API bloc of the vote's
                      legislature, the vote date must be inside that bloc's ``API_BLOC_WINDOWS``
                      entry, its ``/partidos`` must equal the composition documented there, and the
                      party must be in it;
4. ``bloc_curated`` — a bloc row covered by ``CURATED_BLOCS`` (dated, every entry cites an official
                      camara.leg.br document);
5. ``bloc_label``   — any other bloc row whose label is untruncated (no "...") and made only of party
                      siglas ("PpMdbPtb" = PP, MDB, PTB): the label itself lists the whole membership.
                      Leg-56 blocs have empty /partidos, so for them the label is the only evidence.

The API has no dates (``/blocos`` never returns dataInicio/dataFim): ``/partidos`` is the composition
on the crawl day only. So the API never dates a membership by itself; outside a cited window, or when
a re-crawl no longer matches the documented composition, the row falls through to ``CURATED_BLOCS``.

Anything else is unmatched with a reason: ``no_party`` (the deputy has no party, "S.PART."),
``no_bench_row`` (no row could be the party's),
``bloc_unresolved`` (the vote has bloc/federation rows whose membership is unknown for that date) or
``ambiguous`` (more than one row could be the party's). Leadership rows never orient a party.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import NamedTuple

from elosys.util import normalize_name

API_URL = "https://dadosabertos.camara.leg.br/api/v2"

# Bump on any change to the mapping rules or tables: it is part of the party-bench parse version.
BENCHES_VERSION = "1.2"


class BenchMatch(NamedTuple):
    """Result of ``map_party_to_bench``; exactly one of bench_label/unmatched_reason is None.

    ``match_evidence`` is what proves the membership (camara_vote_party_bench.match_evidence):
    the /blocos URL or the label itself (federation), the /partidos URL plus the cited window
    (bloc_api), the cited official URLs (bloc_curated); None for 'party' and unmatched results.
    """

    bench_label: str | None
    match_basis: str | None
    unmatched_reason: str | None
    match_evidence: str | None = None


def _unmatched(reason: str) -> BenchMatch:
    return BenchMatch(None, None, reason, None)

# --- vocabulary -------------------------------------------------------------------------

# Leadership rows. They orient the government/majority/minority/opposition, never a party.
# "GOV." is how 2019-2022 files spell Governo (639 PLEN rows in 2021).
_LEADERSHIP: dict[str, tuple[str, str]] = {
    "GOVERNO": ("government", "GOVERNO"),
    "GOV.": ("government", "GOVERNO"),
    "GOV": ("government", "GOVERNO"),
    "MAIORIA": ("majority", "MAIORIA"),
    "MINORIA": ("minority", "MINORIA"),
    "OPOSICAO": ("opposition", "OPOSICAO"),
}

# Normalized (upper, no accents) label or deputy party -> party sigla as used in votacoesVotos.
PARTY_ALIASES: dict[str, str] = {
    # siglaBancada is cut at 10 characters (2019-2026 files).
    "SOLIDARIED": "SOLIDARIEDADE",
    "REPUBLICAN": "REPUBLICANOS",
    # Bench rows use the party name; votacoesVotos.deputado_siglaPartido uses the sigla PODE.
    "PODEMOS": "PODE",
    # votacoesVotos-2019 spells Patriota both "PATRI" (its sigla until 2019) and "PATRIOTA";
    # the bench row is "Patriota". Same party (TSE nº 51), only the sigla changed.
    "PATRI": "PATRIOTA",
}

# Every party sigla seen in votacoesVotos 2019-2026 (normalized), plus registered parties without
# deputies so a lone upper-case row is still recognized. "S.PART." (no party) is excluded.
KNOWN_PARTIES: frozenset[str] = frozenset({
    "AGIR", "AVANTE", "CIDADANIA", "DC", "DEM", "MDB", "MISSAO", "MOBILIZA", "NOVO", "PATRIOTA",
    "PCB", "PCDOB", "PCO", "PDT", "PHS", "PL", "PMB", "PMN", "PODE", "PP", "PPL", "PPS", "PR",
    "PRB", "PRD", "PROS", "PRP", "PRTB", "PSB", "PSC", "PSD", "PSDB", "PSL", "PSOL", "PSTU", "PT",
    "PTB", "PTC", "PV", "REDE", "REPUBLICANOS", "SOLIDARIEDADE", "UNIAO", "UP",
})

_NO_PARTY = frozenset({"S.PART.", "S.PART", "SEM PARTIDO"})

_CAMEL_TOKEN = re.compile(r"[A-Z][a-z]+|[A-Z]+(?![a-z])")


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normalize_party(abbr: str | None) -> str | None:
    """Deputy party (votacoesVotos) or bench party label -> canonical sigla; None for no party."""
    norm = normalize_name(abbr)
    if not norm or norm in _NO_PARTY:
        return None
    return PARTY_ALIASES.get(norm, norm)


def _label_body(label: str) -> tuple[str, bool]:
    """Strip the "Bl "/"Bloco " prefix and the "..." truncation marker."""
    body = _strip_accents(label.strip())
    truncated = body.endswith("...")
    if truncated:
        body = body[:-3]
    for prefix in ("Bloco ", "Bl "):
        if body.lower().startswith(prefix.lower()):
            body = body[len(prefix):]
            break
    return body.strip(), truncated


def _camel_tokens(body: str) -> list[str]:
    return [t.upper() for t in _CAMEL_TOKEN.findall(body.replace(" ", ""))]


def normalize_bench(label: str | None) -> tuple[str, str]:
    """Raw siglaBancada -> (bench_kind, bench_norm).

    bench_kind ∈ party, federation, bloc, government, majority, minority, opposition, other.
    bench_norm is upper-case without accents; parties map to the votacoesVotos sigla
    ("Solidaried" -> SOLIDARIEDADE, "Podemos" -> PODE), leadership to GOVERNO/MAIORIA/MINORIA/OPOSICAO,
    federations and blocs keep their (upper-cased) label so case variants collapse
    ("Bl PlFdrPTUniPp..." == "Bl PlFdrPtUniPp...").
    """
    raw = (label or "").strip()
    norm = normalize_name(raw) or ""
    if not norm:
        return ("other", "")
    if norm in _LEADERSHIP:
        return _LEADERSHIP[norm]
    if norm.startswith(("FDR ", "FEDERACAO ")):
        return ("federation", norm)
    if norm.startswith(("BL ", "BLOCO ")):
        return ("bloc", norm)
    party = PARTY_ALIASES.get(norm, norm)
    if party in KNOWN_PARTIES:
        return ("party", party)
    body, truncated = _label_body(raw)
    # 2019-2022 blocs have no "Bl" prefix: "PpMdbPtb", "PdtSdPode...", "NovoCidPv".
    if " " not in body and any(c.islower() for c in body) and (truncated or len(_camel_tokens(body)) >= 2):
        return ("bloc", norm)
    if re.fullmatch(r"[A-Z0-9]{2,12}", norm):
        return ("party", norm)  # an all-caps sigla not yet in KNOWN_PARTIES
    return ("other", norm)


# --- API blocs --------------------------------------------------------------------------


@dataclass(frozen=True)
class Bloc:
    """A bloc or federation from /blocos?idLegislatura= plus /blocos/{id}/partidos."""

    bloc_id: str
    name: str
    legislature: int | None
    is_federation: bool
    members: tuple[str, ...]  # normalized siglas from /partidos (empty = API gave none)
    # Members grouped and ordered as in ``name`` (federations expanded); None when the name cannot
    # be reconciled with ``members`` — such a bloc is never matched against a truncated label.
    name_parts: tuple[tuple[str, ...], ...] | None
    start_date: str | None = None
    end_date: str | None = None


def _dados(obj: object) -> list:
    if isinstance(obj, Mapping):
        return list(obj.get("dados") or [])
    return list(obj or [])  # type: ignore[arg-type]


def _int_or_none(v: object) -> int | None:
    try:
        return int(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def parse_blocs(blocs_json: object, bloc_parties_json_by_id: Mapping[str, object]) -> list[Bloc]:
    """Build ``Bloc`` objects from raw API JSON (``{"dados": [...]}`` or the bare list).

    ``bloc_parties_json_by_id`` maps bloc id -> /blocos/{id}/partidos response. A bloc without a
    /partidos entry (or with an empty one) gets ``members=()``: membership is never inferred from the
    free-text ``nome`` (e.g. leg. 56 bloc 576 is named just "PSL").
    """
    raw = _dados(blocs_json)
    members_by_id: dict[str, tuple[str, ...]] = {}
    for item in raw:
        bid = str(item.get("id"))
        seen: list[str] = []
        for p in _dados(bloc_parties_json_by_id.get(bid)):
            sigla = normalize_party(p.get("sigla"))
            if sigla and sigla not in seen:
                seen.append(sigla)
        members_by_id[bid] = tuple(seen)

    fed_members: dict[tuple[int | None, str], tuple[str, ...]] = {}
    for item in raw:
        if item.get("federacao"):
            key = (_int_or_none(item.get("idLegislatura")), normalize_name(item.get("nome")) or "")
            fed_members[key] = members_by_id[str(item.get("id"))]

    out: list[Bloc] = []
    for item in raw:
        bid = str(item.get("id"))
        leg = _int_or_none(item.get("idLegislatura"))
        is_fed = bool(item.get("federacao"))
        members = members_by_id[bid]
        parts = None if is_fed or not members else _name_parts(item.get("nome") or "", leg, members, fed_members)
        out.append(Bloc(
            bloc_id=bid,
            name=item.get("nome") or "",
            legislature=leg,
            is_federation=is_fed,
            members=members,
            name_parts=parts,
            start_date=item.get("dataInicio") or None,
            end_date=item.get("dataFim") or None,
        ))
    return out


def _name_parts(
    name: str,
    leg: int | None,
    members: tuple[str, ...],
    fed_members: Mapping[tuple[int | None, str], tuple[str, ...]],
) -> tuple[tuple[str, ...], ...] | None:
    """"UNIÃO, PP, PSD, ..., Federação PSDB CIDADANIA, PODE" -> ((UNIAO,), (PP,), ..., (PSDB, CIDADANIA), (PODE,))."""
    parts: list[tuple[str, ...]] = []
    for comp in name.split(","):
        norm = normalize_name(comp)
        if not norm:
            return None
        if norm.startswith("FEDERACAO"):
            fed = fed_members.get((leg, norm))
            if not fed:
                return None
            parts.append(fed)
        else:
            party = normalize_party(norm)
            if not party:
                return None
            parts.append((party,))
    flat = [p for part in parts for p in part]
    if sorted(flat) != sorted(members):
        return None  # name and /partidos disagree: do not trust the order
    return tuple(parts)


def _match_tokens(tokens: Sequence[str], parts: Sequence[tuple[str, ...]], truncated: bool) -> bool:
    """Visible label tokens are, in order, abbreviations of the bloc's leading members."""
    i = 0
    for part in parts:
        if i == len(tokens):
            return truncated
        if len(part) == 1:
            if not part[0].startswith(tokens[i]):
                return False
            i += 1
            continue
        # A federation inside a bloc shows up as "Fdr" + abbreviations of its first member(s).
        if tokens[i] != "FDR":
            return False
        i += 1
        j = 0
        while i < len(tokens) and j < len(part) and part[j].startswith(tokens[i]):
            i += 1
            j += 1
        if j == 0 and i < len(tokens):
            return False
    return i == len(tokens)


# --- curated blocs ----------------------------------------------------------------------


@dataclass(frozen=True)
class CuratedBloc:
    label: str  # siglaBancada as printed in votacoesOrientacoes (matched after normalize_bench)
    start_date: str  # inclusive, ISO
    end_date: str  # inclusive, ISO
    members: frozenset[str]
    sources: tuple[str, ...]  # official camara.leg.br pages that state the composition
    evidence: str  # what the sources say and why the window/members are cut where they are
    # True when the sources give the WHOLE composition for every day of the window, so a party
    # outside ``members`` is known to be outside (-> no_bench_row instead of bloc_unresolved).
    complete: bool = False


@dataclass(frozen=True)
class ApiBlocWindow:
    """Dates on which an API bloc's /partidos is known (from cited documents) to be its composition."""

    start_date: str  # inclusive, ISO
    end_date: str  # inclusive, ISO: last date a cited document confirms the composition
    members: frozenset[str]  # documented composition; a re-crawl that differs disables the window
    sources: tuple[str, ...]
    evidence: str


def _od(codteor: int) -> str:
    """Official Ordem do Dia PDF of a plenary session; its "Lideranças" section lists every bloc."""
    return f"https://www.camara.leg.br/proposicoesWeb/prop_mostrarintegra?codteor={codteor}"


_NEWS_2019_BLOCS = (
    "https://www.camara.leg.br/noticias/551176-tres-blocos-parlamentares-sao-formalizados-na-camara-dos-deputados/"
)
_EV_2019 = (
    "2019-02-01 news: 'PSL (52), PP (38), PSD (35), MDB (34), PR (33), PRB (30), DEM (29), PSDB (29), "
    "PTB (10), PSC (8) e PMN (3)' and 'PDT (28), Pode (17), Solidariedade (13), PCdoB (10), Patri (9), "
    "PPS (8), Pros (8), Avante (7), PV (4) e Democracia Cristã (1)'. Only members visible in the label "
    "are listed ('Sd' = Solidariedade); the others may have left after the Mesa election."
)
_NEWS_2023_MDB = (
    "https://www.camara.leg.br/noticias/949338-deputado-fabio-macedo-e-eleito-lider-de-bloco-que-reune-142-deputados/"
)
_NEWS_2023_UNI = (
    "https://www.camara.leg.br/noticias/952375-novo-bloco-parlamentar-reune-nove-partidos-e-173-deputados-na-camara/"
)
_NEWS_2025_MEGA = (
    "https://www.camara.leg.br/noticias/"
    "1129399-bloco-com-17-partidos-e-494-deputados-e-formado-para-disputar-presidencia-da-camara"
)
_BANCADAS_ATUAIS = "https://www.camara.leg.br/deputados/liderancas-e-bancadas-partidarias/bancadas-atuais"

# Mesa-election bloc of 2025-02-01: 17 of 20 parties; only NOVO and PSOL-REDE stayed out.
_MEGA_2025 = frozenset({
    "PL", "PT", "PCDOB", "PV", "UNIAO", "PP", "REPUBLICANOS", "PSD", "MDB", "PDT", "PSDB",
    "CIDADANIA", "PSB", "PODE", "AVANTE", "SOLIDARIEDADE", "PRD",
})
# "BLOCO UNIÃO, PP, PSD, REPUBLICANOS, MDB, FEDERAÇÃO PSDB CIDADANIA, PODE" (= API bloc 589).
_UNI_2025 = frozenset({"UNIAO", "PP", "PSD", "REPUBLICANOS", "MDB", "PSDB", "CIDADANIA", "PODE"})
_UNI_2025_SOURCES = (
    _od(3038294), _od(3063582), _od(3064515), _od(3077816), _od(3089784), _od(3101406), _od(3125417),
    _od(3126252), _BANCADAS_ATUAIS,
)
_UNI_2025_EVIDENCE = (
    "Ordem do Dia 2025-11-04..2026-05-08 and Bancadas Atuais (2026-10-02): 'BLOCO UNIÃO, PP, PSD, "
    "REPUBLICANOS, MDB, FEDERAÇÃO PSDB CIDADANIA, PODE'; label 'Bl UniPpPsd...' from 2025-10-29 "
    "(Ordem do Dia 2025-10-21/22 list the same eight plus PL)."
)

# A member is listed only for dates where official documents place it in the bloc on both sides of
# the window (or the label prints it). Undocumented exits drop the party -> "bloc_unresolved".
# Verified by hand against every cited page on 2026-10-02.
CURATED_BLOCS: tuple[CuratedBloc, ...] = (
    # 56th legislature. Not curated (no official composition found): "PpMdbPtb", "PpPrPsd",
    # "PdtAvante" (2019-20), "PscPtb", "NovoCidPv" (2022).
    CuratedBloc("PslPpPsd...", "2019-02-01", "2019-03-26", frozenset({"PSL", "PP", "PSD"}),
                (_NEWS_2019_BLOCS,), _EV_2019),
    CuratedBloc("PdtSdPode...", "2019-02-01", "2019-03-20", frozenset({"PDT", "SOLIDARIEDADE", "PODE"}),
                (_NEWS_2019_BLOCS,), _EV_2019),
    CuratedBloc("PdtPodePros...", "2019-03-21", "2019-03-31", frozenset({"PDT", "PODE", "PROS"}),
                (_NEWS_2019_BLOCS,), _EV_2019),
    CuratedBloc("PdtPros...", "2019-04-01", "2019-04-02", frozenset({"PDT", "PROS"}),
                (_NEWS_2019_BLOCS,), _EV_2019),
    # 57th legislature. Not curated: "Bloco Parlamentar" (2023-03, remnant of the Mesa bloc).
    CuratedBloc(
        "Bl MdbPsdRepPodePsc", "2023-03-29", "2023-08-16",
        frozenset({"MDB", "PSD", "REPUBLICANOS", "PODE", "PSC"}),
        (_NEWS_2023_MDB,),
        "2023-03-29 news: bloc 'formado por MDB, PSD, Republicanos, Podemos e PSC'. Untruncated label.",
        complete=True,
    ),
    CuratedBloc(
        "Bl MdbPsdRepPode", "2023-08-17", "2025-01-31", frozenset({"MDB", "PSD", "REPUBLICANOS", "PODE"}),
        (_NEWS_2023_MDB, _od(2663227), _od(2845478)),
        "PSC absorbed by Podemos; Ordem do Dia 2024-10-30 and 2024-12-19: 'Bloco MDB, PSD, REPUBLICANOS, "
        "PODE'. Untruncated label.",
        complete=True,
    ),
    CuratedBloc(
        "Bl UniPpFdrPsdbCid...", "2023-04-12", "2025-01-31",
        frozenset({"UNIAO", "PP", "PSDB", "CIDADANIA", "PDT", "AVANTE", "SOLIDARIEDADE"}),
        (_NEWS_2023_UNI, _od(2663227), _od(2845478)),
        "2023-04-12 news: 'União Brasil (59), PP (49), Federação PSDB-Cidadania (18), PDT (17), PSB (14), "
        "Avante (7), Solidariedade (5) e Patriota (4)'. Ordem do Dia 2024-10-30/2024-12-19: 'Bloco UNIÃO, PP, "
        "Federação PSDB CIDADANIA, PDT, AVANTE, SOLIDARIEDADE, PRD'. PSB (own row from 2024-02-20) and "
        "Patriota (merged into PRD) left at undocumented dates -> omitted.",
    ),
    CuratedBloc(
        "Bl UniPpFdrPsdbCid...", "2024-03-12", "2025-01-31", frozenset({"PRD"}),
        (_od(2394240), _od(2404279), _od(2408391), _od(2416852), _od(2463052), _od(2483041), _od(2663227),
         _od(2845478)),
        "Ordem do Dia 2024-03-12, 04-09, 04-16, 05-06, 08-12, 10-08, 10-30, 12-19: 'Bloco UNIÃO, PP, "
        "Federação PSDB CIDADANIA, PDT, AVANTE, SOLIDARIEDADE, PRD'. PRD only from the first one found.",
    ),
    CuratedBloc(
        "Bl PlFdrPtUniPp...", "2025-02-01", "2025-02-26", _MEGA_2025,
        (_NEWS_2025_MEGA, _od(2861097)),
        "2025-02-01 news: PT, PL, 'PCdoB, PV, União, PP, Republicanos, PSD, MDB, PDT, PSDB, Cidadania, PSB, "
        "Podemos, Avante, Solidariedade e PRD'; only Novo and Psol-Rede outside. Ordem do Dia 2025-02-26 "
        "lists the same 17 parties.",
        complete=True,
    ),
    CuratedBloc(
        "Bl PlFdrPtUniPp...", "2025-02-27", "2025-05-27", _MEGA_2025 - {"SOLIDARIEDADE"},
        (_od(2861097), _od(2864200), _od(2875636), _od(2887637), _od(2901639), _od(2918902), _od(2920198)),
        "Ordem do Dia 2025-02-26, 03-11, 04-01, 04-16, 05-07, 05-27, 05-28: 'Bloco PL, Federação Brasil da "
        "Esperança - Fe Brasil, UNIÃO, PP, PSD, MDB, REPUBLICANOS, PDT, Federação PSDB CIDADANIA, PSB, PODE, "
        "AVANTE, PRD'. SOLIDARIEDADE is its own leadership from 2025-03-11 (left between 02-26 and 03-11 "
        "-> omitted). Ends the day 'Bl AvanSolidPrd...' appears (2025-05-28; the agenda predates the vote).",
    ),
    CuratedBloc(
        "Bl PlFdrPtUniPp...", "2025-05-28", "2025-08-25", _MEGA_2025 - {"AVANTE", "SOLIDARIEDADE", "PRD"},
        (_od(2922102), _od(2924656), _od(2934629), _od(2935912), _od(2944505), _od(2950956), _od(2959144),
         _od(2968249), _od(2971423), _od(2979717), _od(2985447)),
        "Ordem do Dia 2025-05-29..2025-08-26: 'Bloco PL, Federação Brasil da Esperança - Fe Brasil, UNIÃO, PP, "
        "PSD, REPUBLICANOS, MDB, PDT, Federação PSDB CIDADANIA, PSB, PODE'; AVANTE/SOLIDARIEDADE/PRD in their "
        "own bloc. The label becomes 'Bl PlUniPpPsd...' on 2025-08-26.",
    ),
    CuratedBloc(
        "Bl PlUniPpPsd...", "2025-08-26", "2025-10-28",
        frozenset({"PL", "UNIAO", "PP", "PSD", "REPUBLICANOS", "MDB", "PSDB", "CIDADANIA", "PODE"}),
        (_od(2991281), _od(2992500), _od(3001361), _od(3025922), _od(3026278)),
        "Ordem do Dia 2025-09-02, 09-03, 09-17, 10-21, 10-22: 'BLOCO PL, UNIÃO, PP, PSD, REPUBLICANOS, MDB, "
        "FEDERAÇÃO PSDB CIDADANIA, PODE' (all nine also in the bloc it came from on 2025-08-26).",
    ),
    CuratedBloc(
        "Bl PlUniPpPsd...", "2025-08-26", "2025-09-02", frozenset({"PDT", "PSB"}),
        (_od(2985447), _od(2991281), _od(2992500)),
        "Ordem do Dia 2025-08-26 lists PDT and PSB in the bloc this one came from; 2025-09-02 and 09-03: "
        "'Bloco PL, UNIÃO, PP, PSD, REPUBLICANOS, MDB, Federação PSDB CIDADANIA, PODE, PDT, PSB'. "
        "Own rows from 2025-09-03; outside on 2025-09-17 (3001361).",
    ),
    CuratedBloc(
        "Bl UniPpPsd...", "2025-10-29", "2026-10-02", _UNI_2025, _UNI_2025_SOURCES,
        _UNI_2025_EVIDENCE + " Same as API bloc 589 (bloc_api wins inside API_BLOC_WINDOWS).",
        complete=True,
    ),
    CuratedBloc(
        "Bl AvanSolidPrd...", "2025-05-28", "2026-05-18", frozenset({"AVANTE", "SOLIDARIEDADE", "PRD"}),
        (_od(2922102), _od(2924656), _od(2959144), _od(3001361), _od(3025922), _od(3038294), _od(3063582),
         _od(3077816), _od(3101406), _od(3126252)),
        "Ordem do Dia 2025-05-29..2026-05-08: 'Bloco AVANTE, SOLIDARIEDADE, PRD' (API bloc 590 has an empty "
        "/partidos). Label prints all three; their own rows return on 2026-05-19.",
        complete=True,
    ),
)

# API bloc id -> dates its /partidos is usable for. Outside them, or when a re-crawl differs from
# ``members``, the API says nothing about the vote date and the row falls through to CURATED_BLOCS.
API_BLOC_WINDOWS: dict[str, ApiBlocWindow] = {
    "589": ApiBlocWindow("2025-10-29", "2026-10-02", _UNI_2025, _UNI_2025_SOURCES, _UNI_2025_EVIDENCE),
}


# --- mapping ----------------------------------------------------------------------------


def _iso_day(vote_date: str | date) -> str:
    """date, datetime or ISO string -> 'YYYY-MM-DD' (a datetime's 'T15:00' must not pass a window end)."""
    return (vote_date.isoformat() if isinstance(vote_date, date) else str(vote_date))[:10]


def legislature_of(vote_date: str | date) -> int:
    """Câmara legislature of a date: 56 = 2019-02-01..2023-01-31, 57 = 2023-02-01..2027-01-31."""
    d = date.fromisoformat(_iso_day(vote_date))
    year = d.year if (d.month, d.day) >= (2, 1) else d.year - 1
    return 56 + (year - 2019) // 4


# Committee rows cut "Fdr PSDB-CIDADANIA" at 16 chars WITHOUT a "..." marker (2023-2026 CCJC).
_FEDERATION_ALIASES: dict[str, str] = {"CIDADAN": "CIDADANIA"}
_SIGLA = re.compile(r"[A-Z0-9]{2,12}")


def _federation_label_parts(label: str) -> tuple[list[str], bool]:
    """ "Fdr PT-PCdoB-PV" -> ([PT, PCDOB, PV], False); "Fdr PT-PCdoB..." -> ([PT, PCDOB], True)."""
    body = label.strip()
    truncated = body.endswith("...")
    if truncated:
        body = body[:-3]
    norm = normalize_name(body) or ""
    for prefix in ("FDR ", "FEDERACAO "):
        if norm.startswith(prefix):
            norm = norm[len(prefix):]
            break
    parts = [normalize_party(p.strip()) or "" for p in norm.split("-")]
    return [_FEDERATION_ALIASES.get(p, p) for p in parts if p], truncated


def _federation_label_members(label: str) -> frozenset[str]:
    """ "Fdr PT-PCdoB-PV" -> {PT, PCDOB, PV}; empty when unparseable (truncated, or not siglas)."""
    parts, truncated = _federation_label_parts(label)
    if truncated or not parts or not all(_SIGLA.fullmatch(p) for p in parts):
        return frozenset()
    return frozenset(parts)


def _federation_names(label: str, party: str) -> bool:
    """An unparseable federation label still visibly lists ``party`` ("Fdr PT-PCdoB..." names PT)."""
    parts, truncated = _federation_label_parts(label)
    if party in parts:
        return True
    return truncated and bool(parts) and len(parts[-1]) >= 2 and party.startswith(parts[-1])


def _visibly_names(label: str, party: str) -> bool:
    # A token that is itself a sigla names only that party: "Pr" (PR) is not PROS, "Psd" not PSDB.
    body, _ = _label_body(label)
    return any(
        len(t) >= 2 and (t == party or (t not in KNOWN_PARTIES and party.startswith(t)))
        for t in _camel_tokens(body)
    )


def _api_blocs_for(label: str, blocs: Iterable[Bloc], leg: int, iso: str) -> list[Bloc]:
    """API blocs whose leading members fit the label's visible tokens (dates: see ``_api_window``)."""
    body, truncated = _label_body(label)
    tokens = _camel_tokens(body)
    if not tokens:
        return []
    out = []
    for b in blocs:
        if b.is_federation or not b.name_parts or b.legislature != leg:
            continue
        if (b.start_date and iso < b.start_date[:10]) or (b.end_date and iso > b.end_date[:10]):
            continue
        if _match_tokens(tokens, b.name_parts, truncated):
            out.append(b)
    return out


def _api_window(bloc: Bloc, iso: str) -> ApiBlocWindow | None:
    """The cited window that lets ``bloc``'s /partidos speak for ``iso``; None = API unusable."""
    w = API_BLOC_WINDOWS.get(bloc.bloc_id)
    if w is None or not (w.start_date <= iso <= w.end_date) or frozenset(bloc.members) != w.members:
        return None
    return w


def _curated_entries(bench_norm: str, iso: str) -> list[CuratedBloc]:
    return [
        c for c in CURATED_BLOCS
        if normalize_bench(c.label)[1] == bench_norm and c.start_date <= iso <= c.end_date
    ]


# Party labels are cut at 10 characters with no "..." ("Solidaried"): a 10-char label may be cut.
_CUT_LENGTH = 10


def bloc_label_members(label: str) -> frozenset[str]:
    """ "PpMdbPtb" -> {PP, MDB, PTB}; empty unless the label is untruncated and EVERY camel-case token
    is a known party sigla ("NovoCidPv": CID is not a sigla; "PdtSdPode...": truncated)."""
    body, truncated = _label_body(label)
    if truncated or len(label.strip()) == _CUT_LENGTH:
        return frozenset()
    raw = _CAMEL_TOKEN.findall(body.replace(" ", ""))
    tokens = [PARTY_ALIASES.get(t.upper(), t.upper()) for t in raw]
    if len(tokens) < 2 or "".join(raw) != body.replace(" ", "") or not all(t in KNOWN_PARTIES for t in tokens):
        return frozenset()
    return frozenset(tokens)


def _federation_evidence(label: str, party: str, blocs: Sequence[Bloc], leg: int) -> str:
    members = _federation_label_members(label)
    feds = [b for b in blocs if b.is_federation and b.legislature == leg and frozenset(b.members) == members]
    if len(feds) == 1:
        return f"{API_URL}/blocos/{feds[0].bloc_id}"
    return f"siglaBancada '{label}' lists {party}"


def map_party_to_bench(
    party_abbr: str | None,
    vote_bench_labels: Iterable[str],
    blocs: Sequence[Bloc],
    vote_date: str | date,
) -> BenchMatch:
    """-> BenchMatch(bench_label, match_basis, unmatched_reason, match_evidence).

    ``vote_bench_labels``: raw siglaBancada values of one vote. ``vote_date``: votacoes.data
    (ISO string, date or datetime; only the day counts).
    """
    party = normalize_party(party_abbr)
    if not party:
        return _unmatched("no_party")
    iso = _iso_day(vote_date)
    leg = legislature_of(iso)

    parsed = [(lbl, *normalize_bench(lbl)) for lbl in dict.fromkeys(vote_bench_labels)]

    own = [lbl for lbl, kind, norm in parsed if kind == "party" and norm == party]
    if len(own) > 1:
        return _unmatched("ambiguous")
    if own:
        return BenchMatch(own[0], "party", None, None)

    fed_rows = [lbl for lbl, kind, _ in parsed if kind == "federation"]
    feds = [lbl for lbl in fed_rows if party in _federation_label_members(lbl)]
    if len(feds) > 1:
        return _unmatched("ambiguous")
    if feds:
        return BenchMatch(feds[0], "federation", None, _federation_evidence(feds[0], party, blocs, leg))

    candidates: list[BenchMatch] = []
    ambiguous = False
    unresolved_naming_party = False
    # A row whose membership is unknown on this date: the party may be in it -> not no_bench_row.
    unresolved_rows = False
    for lbl in fed_rows:
        if not _federation_label_members(lbl):
            unresolved_rows = True
            unresolved_naming_party |= _federation_names(lbl, party)

    for lbl, kind, norm in parsed:
        if kind != "bloc":
            continue
        api = _api_blocs_for(lbl, blocs, leg, iso)
        if len(api) > 1:
            # The truncated label fits several API blocs: membership unknown for this row.
            unresolved_rows = True
            if any(party in b.members for b in api) or _visibly_names(lbl, party):
                ambiguous = True
            continue
        window = _api_window(api[0], iso) if api else None
        if window is not None:
            if party in window.members:
                evidence = (f"{API_URL}/blocos/{api[0].bloc_id}/partidos; same composition "
                            f"{window.start_date}..{window.end_date} per {' '.join(window.sources)}")
                candidates.append(BenchMatch(lbl, "bloc_api", None, evidence))
            continue
        entries = _curated_entries(norm, iso)
        if entries:
            with_party = [c for c in entries if party in c.members]
            if with_party:
                sources = dict.fromkeys(s for c in with_party for s in c.sources)
                candidates.append(BenchMatch(lbl, "bloc_curated", None, " ".join(sources)))
            elif not any(c.complete for c in entries):
                unresolved_rows = True
            continue
        listed = bloc_label_members(lbl)
        if listed:
            # The label lists every member: a party outside it is known to be outside.
            if party in listed:
                evidence = f"siglaBancada '{lbl}' (rótulo completo, sem truncamento)"
                candidates.append(BenchMatch(lbl, "bloc_label", None, evidence))
            continue
        unresolved_rows = True
        unresolved_naming_party |= _visibly_names(lbl, party)

    if ambiguous or len(candidates) > 1 or (candidates and unresolved_naming_party):
        return _unmatched("ambiguous")
    if candidates:
        return candidates[0]
    return _unmatched("bloc_unresolved" if unresolved_rows else "no_bench_row")


def measure_coverage(results: Iterable[Sequence[str | None]]) -> dict:
    """Counts of ``map_party_to_bench`` results (BenchMatch or 3/4-tuples): by basis and by reason."""
    by_basis: Counter[str] = Counter()
    by_reason: Counter[str] = Counter()
    for res in results:
        basis, reason = res[1], res[2]
        if basis:
            by_basis[basis] += 1
        else:
            by_reason[reason or "unknown"] += 1
    matched = sum(by_basis.values())
    total = matched + sum(by_reason.values())
    return {
        "total": total,
        "matched": matched,
        "unmatched": total - matched,
        "matched_pct": round(100 * matched / total, 1) if total else 0.0,
        "by_basis": dict(by_basis),
        "by_reason": dict(by_reason),
    }
