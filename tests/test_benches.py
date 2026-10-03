"""Party -> orienting bancada mapping (Câmara votacoesOrientacoes).

Label strings below are copied from the real bulk files
votacoesOrientacoes-{2019,2021,2023,2024,2025}.csv (PLEN rows); bloc JSON fixtures are real
responses of /api/v2/blocos?idLegislatura=56|57 and /api/v2/blocos/{id}/partidos.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import pytest

from elosys.camara import benches
from elosys.camara.benches import (
    API_BLOC_WINDOWS,
    API_URL,
    BENCHES_VERSION,
    CURATED_BLOCS,
    BenchMatch,
    CuratedBloc,
    bloc_label_members,
    map_party_to_bench,
    measure_coverage,
    normalize_bench,
    parse_blocs,
)

FIX = Path(__file__).parent / "fixtures" / "camara"
OD = "https://www.camara.leg.br/proposicoesWeb/prop_mostrarintegra?codteor="

LEADERSHIP = ["Governo", "Maioria", "Minoria", "Oposição"]

# 2025-11 .. 2026-03 (121 + 33 PLEN votes have exactly this label set).
LABELS_2025_NOV = [
    "Bl AvanSolidPrd...", "Bl UniPpPsd...", "Fdr PSOL-REDE", "Fdr PT-PCdoB-PV",
    "Novo", "PDT", "PL", "PSB", *LEADERSHIP,
]
# 2025-09-03 .. 2025-10-28.
LABELS_2025_SEP = [
    "Bl AvanSolidPrd...", "Bl PlUniPpPsd...", "Fdr PSOL-REDE", "Fdr PT-PCdoB-PV",
    "Novo", "PDT", "PSB", *LEADERSHIP,
]
# 2025-09-02 (vote 2428615-16): PDT/PSB have no row yet.
LABELS_2025_SEP_02 = [
    "Bl AvanSolidPrd...", "Bl PlUniPpPsd...", "Fdr PSOL-REDE", "Fdr PT-PCdoB-PV", "Novo", *LEADERSHIP,
]
# 2025-02-11 .. 2025-02-26 (mega bloc formed for the Mesa election).
LABELS_2025_FEB = ["Bl PlFdrPTUniPp...", "Fdr PSOL-REDE", "Novo", *LEADERSHIP]
# 2025-03-18 .. 2025-05-27 (e.g. 2485066-33, 2121642-105).
LABELS_2025_MAY = ["Bl PlFdrPTUniPp...", "Fdr PSOL-REDE", "Novo", "Solidaried", *LEADERSHIP]
# 2025-05-28 .. 2025-08-19.
LABELS_2025_JUN = ["Bl AvanSolidPrd...", "Bl PlFdrPtUniPp...", "Fdr PSOL-REDE", "Novo", *LEADERSHIP]
# 2024-03-12 (vote 2417025-55).
LABELS_2024_MAR = [
    "Bl UniPpFdrPsdbCid...", "Bl MdbPsdRepPode", "Fdr PSOL-REDE", "Fdr PT-PCdoB-PV", "Novo", "PL", "PSB",
    *LEADERSHIP,
]
# 2021: no blocs, truncated/aliased party labels and "GOV.".
LABELS_2021 = [
    "Avante", "Cidadania", "DEM", "MDB", "Novo", "PCdoB", "PDT", "PL", "PP", "PROS", "PSB",
    "PSC", "PSD", "PSDB", "PSL", "PSOL", "PT", "PTB", "PV", "Patriota", "Podemos", "Rede",
    "Republican", "Solidaried", "GOV.", "Maioria", "Minoria", "Oposição",
]
# 2019-02-12 .. 2019-03-12.
LABELS_2019_FEB = ["NOVO", "PPS", "PSB", "PSOL", "PT", "PdtSdPode...", "PslPpPsd...", "GOV.", "Minoria"]
# 2019-04-02 (vote 2193540-77): "Pr" in "PpPrPsd" is PR, not PROS.
LABELS_2019_APR = [
    "PT", "Maioria", "Minoria", "REDE", "PPS", "PSOL", "PRB", "PSL", "PSDB", "PSB", "Podemos", "PpPrPsd",
    "PCdoB", "PdtPros...", "NOVO", "Solidaried",
]
# 2019-07-10 (vote 2192459-636, PEC 6/2019 1st turn): one untruncated bloc label, "PpMdbPtb".
LABELS_2019_JUL = [
    "PROS", "PSB", "PL", "Oposição", "Solidaried", "REDE", "PCdoB", "PSOL", "PT", "GOV.", "PRB", "Avante",
    "PSL", "PDT", "Maioria", "Minoria", "DEM", "PSC", "NOVO", "PSD", "Patriota", "PV", "PSDB", "CIDADANIA",
    "Podemos", "PpMdbPtb",
]

UNI_SOURCES = API_BLOC_WINDOWS["589"].sources


def _load(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def _curated_evidence(label: str, iso: str, party: str) -> str:
    norm = normalize_bench(label)[1]
    return " ".join(dict.fromkeys(
        s for c in CURATED_BLOCS
        if normalize_bench(c.label)[1] == norm and c.start_date <= iso <= c.end_date and party in c.members
        for s in c.sources
    ))


@pytest.fixture(scope="module")
def blocs():
    listing = _load("blocos-leg56.json")["dados"] + _load("blocos-leg57.json")["dados"]
    parties = {}
    for p in FIX.glob("bloco-*-partidos.json"):
        parties[p.name.split("-")[1]] = _load(p.name)
    return parse_blocs({"dados": listing}, parties)


# --- normalize_bench --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("GOV.", ("government", "GOVERNO")),
        ("Governo", ("government", "GOVERNO")),
        ("Maioria", ("majority", "MAIORIA")),
        ("Minoria", ("minority", "MINORIA")),
        ("Oposição", ("opposition", "OPOSICAO")),
        ("Solidaried", ("party", "SOLIDARIEDADE")),
        ("SOLIDARIEDADE", ("party", "SOLIDARIEDADE")),
        ("Republican", ("party", "REPUBLICANOS")),
        ("Podemos", ("party", "PODE")),
        ("PODE", ("party", "PODE")),
        ("União", ("party", "UNIAO")),
        ("UNIÃO", ("party", "UNIAO")),
        ("Novo", ("party", "NOVO")),
        ("PCdoB", ("party", "PCDOB")),
        ("Missão", ("party", "MISSAO")),
        ("Fdr PT-PCdoB-PV", ("federation", "FDR PT-PCDOB-PV")),
        ("Bl PlFdrPTUniPp...", ("bloc", "BL PLFDRPTUNIPP...")),
        ("Bl PlFdrPtUniPp...", ("bloc", "BL PLFDRPTUNIPP...")),
        ("Bl MdbPsdRepPode", ("bloc", "BL MDBPSDREPPODE")),
        ("Bloco Parlamentar", ("bloc", "BLOCO PARLAMENTAR")),
        ("PdtSdPode...", ("bloc", "PDTSDPODE...")),
        ("PpMdbPtb", ("bloc", "PPMDBPTB")),
        ("NovoCidPv", ("bloc", "NOVOCIDPV")),
    ],
)
def test_normalize_bench_real_labels(label, expected):
    assert normalize_bench(label) == expected


def test_normalize_bench_rejects_blank():
    assert normalize_bench("  ") == ("other", "")


# --- parse_blocs ------------------------------------------------------------------------


def test_parse_blocs_marks_federations_and_members(blocs):
    by_id = {b.bloc_id: b for b in blocs}
    assert by_id["584"].is_federation and by_id["584"].members == ("PT", "PCDOB", "PV")
    assert by_id["585"].is_federation and by_id["585"].members == ("PSDB", "CIDADANIA")
    big = by_id["589"]
    assert not big.is_federation and big.legislature == 57
    assert set(big.members) == {"UNIAO", "PP", "PSD", "REPUBLICANOS", "MDB", "PSDB", "CIDADANIA", "PODE"}
    # API returns no /partidos for 590 or any legislature-56 bloc: membership unknown, never guessed
    # from the free-text "nome".
    assert by_id["590"].members == ()
    assert by_id["575"].members == ()


# --- map_party_to_bench: result shape ---------------------------------------------------


def test_result_is_bench_match_with_four_positional_fields(blocs):
    res = map_party_to_bench("PL", LABELS_2025_NOV, blocs, "2025-11-04")
    assert isinstance(res, BenchMatch)
    label, basis, reason, evidence = res
    assert (label, basis, reason, evidence) == ("PL", "party", None, None)


def test_no_party_abbr_is_no_party(blocs):
    assert map_party_to_bench(None, LABELS_2021, blocs, "2021-06-01") == (None, None, "no_party", None)


# --- map_party_to_bench: own row / aliases ----------------------------------------------


@pytest.mark.parametrize(
    ("party", "label"),
    [
        ("SOLIDARIEDADE", "Solidaried"),
        ("REPUBLICANOS", "Republican"),
        ("PODE", "Podemos"),
        ("NOVO", "Novo"),
        ("REDE", "Rede"),
        ("PATRIOTA", "Patriota"),
        ("PCdoB", "PCdoB"),
    ],
)
def test_2021_aliased_own_rows(blocs, party, label):
    assert map_party_to_bench(party, LABELS_2021, blocs, "2021-06-01") == (label, "party", None, None)


def test_own_party_row_wins_over_bloc(blocs):
    # PL has its own row AND is the first visible member of "Bl PlUniPpPsd..." in some votes.
    labels = ["PL", "Bl PlUniPpPsd...", "Fdr PT-PCdoB-PV", *LEADERSHIP]
    assert map_party_to_bench("PL", labels, blocs, "2025-09-10") == ("PL", "party", None, None)
    assert map_party_to_bench("PL", LABELS_2025_NOV, blocs, "2025-11-04") == ("PL", "party", None, None)


def test_two_rows_for_same_party_is_ambiguous(blocs):
    assert map_party_to_bench("NOVO", ["Novo", "NOVO"], blocs, "2021-06-01") == (None, None, "ambiguous", None)


# --- federation -------------------------------------------------------------------------


@pytest.mark.parametrize("party", ["PT", "PCdoB", "PV"])
def test_federation_path(blocs, party):
    assert map_party_to_bench(party, LABELS_2025_NOV, blocs, "2025-11-04") == (
        "Fdr PT-PCdoB-PV", "federation", None, f"{API_URL}/blocos/584",
    )


def test_federation_without_api_bloc_cites_the_label(blocs):
    # The fixtures hold no legislature-56 federation: the untruncated label itself is the evidence.
    assert map_party_to_bench("PT", ["Fdr PT-PCdoB-PV", "PL"], blocs, "2022-06-01") == (
        "Fdr PT-PCdoB-PV", "federation", None, "siglaBancada 'Fdr PT-PCdoB-PV' lists PT",
    )


def test_federation_row_beats_bloc_row(blocs):
    # PSDB/Cidadania are inside bloc 589 but a federation row present in the vote is more specific.
    labels = ["Fdr PSDB-CIDADANIA", "Bl UniPpPsd...", *LEADERSHIP]
    assert map_party_to_bench("PSDB", labels, blocs, "2025-11-04") == (
        "Fdr PSDB-CIDADANIA", "federation", None, f"{API_URL}/blocos/585",
    )


def test_committee_federation_label_cut_at_16_chars(blocs):
    # CCJC/CE rows print "Fdr PSDB-CIDADAN" (no "..."): still the PSDB-Cidadania federation.
    labels = ["Fdr PSDB-CIDADAN", "Fdr PT-PCdoB-PV", "PL", *LEADERSHIP]
    assert map_party_to_bench("CIDADANIA", labels, blocs, "2025-06-10") == (
        "Fdr PSDB-CIDADAN", "federation", None, f"{API_URL}/blocos/585",
    )


@pytest.mark.parametrize("label", ["Fdr PT-PCdoB...", "Federação Brasil da Esperança"])
def test_unparseable_federation_row_is_unresolved_not_no_row(blocs, label):
    assert map_party_to_bench("PT", [label, "PL", *LEADERSHIP], blocs, "2025-11-04") == (
        None, None, "bloc_unresolved", None,
    )


def test_truncated_federation_naming_party_makes_bloc_match_ambiguous(blocs):
    labels = ["Fdr PSDB...", "Bl UniPpPsd...", *LEADERSHIP]
    assert map_party_to_bench("PSDB", labels, blocs, "2025-11-04") == (None, None, "ambiguous", None)


# --- truncated bloc via API -------------------------------------------------------------


@pytest.mark.parametrize("party", ["PSD", "UNIÃO", "PP", "REPUBLICANOS", "MDB", "PODE", "PSDB", "CIDADANIA"])
def test_truncated_bloc_resolved_by_unique_api_bloc(blocs, party):
    res = map_party_to_bench(party, LABELS_2025_NOV, blocs, "2025-11-04")
    assert res[:3] == ("Bl UniPpPsd...", "bloc_api", None)
    assert res.match_evidence == (
        f"{API_URL}/blocos/589/partidos; same composition 2025-10-29..2026-10-02 per {' '.join(UNI_SOURCES)}"
    )


def test_truncated_bloc_needs_exactly_one_api_match(blocs):
    twin = replace(next(b for b in blocs if b.bloc_id == "589"), bloc_id="9999")
    assert map_party_to_bench("PSD", LABELS_2025_NOV, [*blocs, twin], "2025-11-04") == (
        None, None, "ambiguous", None,
    )


def test_api_bloc_requires_label_tokens_as_prefix_in_order(blocs):
    # "Bl UniPpFdrPsdbCid..." (2023-24) shares UNIÃO/PP with bloc 589 but its 3rd member is the
    # PSDB-Cidadania federation, not PSD: bloc 589 must not be used.
    labels = ["Bl MdbPsdRepPode", "Bl UniPpFdrPsdbCid...", "Fdr PSOL-REDE", "Fdr PT-PCdoB-PV", "Novo", "PL"]
    assert map_party_to_bench("PSD", labels, blocs, "2024-05-07")[:3] == ("Bl MdbPsdRepPode", "bloc_curated", None)
    assert map_party_to_bench("PP", labels, blocs, "2024-05-07")[:3] == (
        "Bl UniPpFdrPsdbCid...", "bloc_curated", None,
    )


def test_api_bloc_respects_legislature(blocs):
    # Same label text on a date of legislature 56: bloc 589 (legislature 57) cannot apply.
    assert map_party_to_bench("PSD", ["Bl UniPpPsd...", "PT"], blocs, "2022-06-01") == (
        None, None, "bloc_unresolved", None,
    )


def test_api_bloc_respects_validity_dates_when_present(blocs):
    # An API end date before the vote disables bloc_api; the curated (cited) entry takes over.
    dated = [replace(b, end_date="2025-10-01") if b.bloc_id == "589" else b for b in blocs]
    assert map_party_to_bench("PSD", LABELS_2025_NOV, dated, "2025-11-04") == (
        "Bl UniPpPsd...", "bloc_curated", None, " ".join(UNI_SOURCES),
    )


def test_api_bloc_start_date_after_the_vote(blocs):
    dated = [replace(b, start_date="2025-12-01") if b.bloc_id == "589" else b for b in blocs]
    assert map_party_to_bench("PSD", LABELS_2025_NOV, dated, "2025-11-04")[:3] == (
        "Bl UniPpPsd...", "bloc_curated", None,
    )


def test_api_membership_never_applies_outside_its_cited_window(blocs):
    # /partidos has no dates: before 2025-10-29 and after 2026-10-02 no document backs it.
    labels = ["Bl UniPpPsd...", "PL", *LEADERSHIP]
    assert map_party_to_bench("PSD", labels, blocs, "2025-03-11") == (None, None, "bloc_unresolved", None)
    assert map_party_to_bench("PSD", labels, blocs, "2026-10-03") == (None, None, "bloc_unresolved", None)
    assert map_party_to_bench("PSD", labels, blocs, "2026-10-02").match_basis == "bloc_api"


def test_recrawled_api_composition_does_not_rewrite_past_votes(blocs):
    # A later crawl shows DC inside 589: DC must not inherit the bloc's past orientations, and the
    # documented members fall back to the curated entry.
    b589 = next(b for b in blocs if b.bloc_id == "589")
    grown = replace(b589, members=(*b589.members, "DC"), name_parts=(*b589.name_parts, ("DC",)))
    later = [grown if b.bloc_id == "589" else b for b in blocs]
    assert map_party_to_bench("DC", LABELS_2025_NOV, later, "2026-03-10").match_basis is None
    assert map_party_to_bench("PSD", LABELS_2025_NOV, later, "2026-03-10") == (
        "Bl UniPpPsd...", "bloc_curated", None, " ".join(UNI_SOURCES),
    )


def test_party_visible_in_another_unresolved_label_is_ambiguous(blocs):
    labels = ["Bl UniPpPsd...", "Bl PsdXyz...", *LEADERSHIP]
    assert map_party_to_bench("PSD", labels, blocs, "2025-11-04") == (None, None, "ambiguous", None)


def test_sigla_token_is_not_a_prefix_of_longer_siglas(blocs):
    # "PpPrPsd" (unresolved) shows PR, not PROS: PROS resolves through the curated "PdtPros...".
    assert map_party_to_bench("PROS", LABELS_2019_APR, blocs, "2019-04-02") == (
        "PdtPros...", "bloc_curated", None, _curated_evidence("PdtPros...", "2019-04-02", "PROS"),
    )


# --- curated table ----------------------------------------------------------------------


def test_curated_mega_bloc_2025(blocs):
    for party in ("PT", "AVANTE"):
        assert map_party_to_bench(party, LABELS_2025_FEB, blocs, "2025-02-11") == (
            "Bl PlFdrPTUniPp...", "bloc_curated", None,
            _curated_evidence("Bl PlFdrPTUniPp...", "2025-02-11", party),
        )


def test_curated_evidence_lists_only_entries_that_contain_the_party(blocs):
    res = map_party_to_bench("AVANTE", LABELS_2025_MAY, blocs, "2025-04-01")
    assert res[:3] == ("Bl PlFdrPTUniPp...", "bloc_curated", None)
    assert f"{OD}2875636" in res.match_evidence.split()  # Ordem do Dia 2025-04-01 lists AVANTE


@pytest.mark.parametrize(
    ("iso", "matched"),
    [("2025-01-31", False), ("2025-02-01", True), ("2025-05-27", True), ("2025-05-28", False)],
)
def test_curated_window_boundaries_are_inclusive(blocs, iso, matched):
    labels = ["Bl PlFdrPTUniPp...", "Fdr PSOL-REDE", "Novo", *LEADERSHIP]
    res = map_party_to_bench("AVANTE", labels, blocs, iso)
    assert res[:3] == (("Bl PlFdrPTUniPp...", "bloc_curated", None) if matched else (None, None, "bloc_unresolved"))


@pytest.mark.parametrize("when", [date(2025, 5, 27), datetime(2025, 5, 27, 23, 59, 59)])
def test_date_and_datetime_on_a_window_last_day(blocs, when):
    assert map_party_to_bench("AVANTE", LABELS_2025_MAY, blocs, when)[:3] == (
        "Bl PlFdrPTUniPp...", "bloc_curated", None,
    )


def test_solidariedade_leaves_mega_bloc_after_2025_02_26(blocs):
    # Ordem do Dia 2025-02-26 lists it inside; 2025-03-11 shows its own leadership.
    labels = ["Bl PlFdrPTUniPp...", "Fdr PSOL-REDE", "Novo", *LEADERSHIP]
    assert map_party_to_bench("SOLIDARIEDADE", labels, blocs, "2025-02-26")[1] == "bloc_curated"
    assert map_party_to_bench("SOLIDARIEDADE", labels, blocs, "2025-02-27") == (
        None, None, "bloc_unresolved", None,
    )


def test_pdt_psb_stay_in_bloc_until_their_own_rows(blocs):
    for party in ("PDT", "PSB"):
        assert map_party_to_bench(party, LABELS_2025_JUN, blocs, "2025-08-19")[:3] == (
            "Bl PlFdrPtUniPp...", "bloc_curated", None,
        )
        res = map_party_to_bench(party, LABELS_2025_SEP_02, blocs, "2025-09-02")
        assert res[:3] == ("Bl PlUniPpPsd...", "bloc_curated", None)
        assert f"{OD}2991281" in res.match_evidence.split()
        assert map_party_to_bench(party, LABELS_2025_SEP, blocs, "2025-09-10") == (party, "party", None, None)


def test_prd_in_2024_bloc_from_first_agenda_listing_it(blocs):
    assert map_party_to_bench("PRD", LABELS_2024_MAR, blocs, "2024-03-12")[:3] == (
        "Bl UniPpFdrPsdbCid...", "bloc_curated", None,
    )
    assert map_party_to_bench("PRD", LABELS_2024_MAR, blocs, "2024-03-05") == (
        None, None, "bloc_unresolved", None,
    )


def test_curated_bloc_drops_party_with_undocumented_exit(blocs):
    # PSB left "Bl UniPpFdrPsdbCid..." at an undocumented date (own row from 2024-02-20).
    labels = ["Bl UniPpFdrPsdbCid...", "Bl MdbPsdRepPode", "Fdr PT-PCdoB-PV", "PL", *LEADERSHIP]
    assert map_party_to_bench("PSB", labels, blocs, "2023-10-10") == (None, None, "bloc_unresolved", None)


def test_party_in_two_resolved_bloc_rows_is_ambiguous(blocs, monkeypatch):
    extra = CuratedBloc("Bl AvanSolidPrd...", "2025-03-01", "2025-03-01", frozenset({"AVANTE"}),
                        ("https://www.camara.leg.br/x",), "test")
    monkeypatch.setattr(benches, "CURATED_BLOCS", (*CURATED_BLOCS, extra))
    labels = ["Bl AvanSolidPrd...", "Bl PlFdrPTUniPp...", *LEADERSHIP]
    assert map_party_to_bench("AVANTE", labels, blocs, "2025-03-01") == (None, None, "ambiguous", None)


def test_party_resolved_while_unresolved_row_names_it_is_ambiguous(blocs):
    # On 2025-03-01 "Bl AvanSolidPrd..." has no documented composition but visibly names Avante.
    labels = ["Bl AvanSolidPrd...", "Bl PlFdrPTUniPp...", *LEADERSHIP]
    assert map_party_to_bench("AVANTE", labels, blocs, "2025-03-01") == (None, None, "ambiguous", None)


def test_curated_bloc_api_without_members_2025(blocs):
    # API bloc 590 has empty /partidos; composition comes from the curated (cited) table.
    for party in ("AVANTE", "SOLIDARIEDADE", "PRD"):
        assert map_party_to_bench(party, LABELS_2025_NOV, blocs, "2025-11-04")[:3] == (
            "Bl AvanSolidPrd...", "bloc_curated", None,
        )


def test_curated_outside_window_is_unresolved(blocs):
    assert map_party_to_bench("PT", LABELS_2025_FEB, blocs, "2027-03-01") == (None, None, "bloc_unresolved", None)


def test_curated_entries_cite_official_source():
    assert CURATED_BLOCS
    for entry in [*CURATED_BLOCS, *API_BLOC_WINDOWS.values()]:
        assert entry.sources and all(s.startswith("https://www.camara.leg.br/") for s in entry.sources)
        assert entry.start_date <= entry.end_date
        assert entry.members and all(m == m.upper() for m in entry.members)
    assert all(normalize_bench(c.label)[0] == "bloc" for c in CURATED_BLOCS)


def test_pre_2023_bloc_member_not_visible_in_label_is_unresolved(blocs):
    # PCdoB was in the PDT bloc on 2019-02-01 but nothing confirms it at vote time.
    assert map_party_to_bench("PCdoB", LABELS_2019_FEB, blocs, "2019-02-12") == (None, None, "bloc_unresolved", None)
    assert map_party_to_bench("PDT", LABELS_2019_FEB, blocs, "2019-02-12")[:3] == (
        "PdtSdPode...", "bloc_curated", None,
    )


# --- untruncated bloc label -------------------------------------------------------------


@pytest.mark.parametrize("party", ["PP", "MDB", "PTB"])
def test_untruncated_label_of_siglas_lists_the_whole_bloc(blocs, party):
    # Leg-56 blocs have empty /partidos and "PpMdbPtb" is not curated: the label is the evidence.
    assert map_party_to_bench(party, LABELS_2019_JUL, blocs, "2019-07-10") == (
        "PpMdbPtb", "bloc_label", None, "siglaBancada 'PpMdbPtb' (rótulo completo, sem truncamento)",
    )


def test_party_outside_an_untruncated_label_has_no_row(blocs):
    # PHS has no row and the only bloc row lists its whole membership.
    assert map_party_to_bench("PHS", LABELS_2019_JUL, blocs, "2019-07-10") == (None, None, "no_bench_row", None)


@pytest.mark.parametrize(
    ("label", "members"),
    [
        ("PpMdbPtb", {"PP", "MDB", "PTB"}),
        ("PpPrPsd", {"PP", "PR", "PSD"}),
        ("PscPtb", {"PSC", "PTB"}),
        ("PdtAvante", {"PDT", "AVANTE"}),
        ("NovoCidPv", set()),  # CID is an abbreviation of Cidadania, not a sigla
        ("PdtSdPode...", set()),  # truncated
        ("Bl MdbPsdRepPode", set()),  # REP is not a sigla
        ("Bloco Parlamentar", set()),
        ("PpMdbPtbPv", set()),  # 10 characters: may be cut like "Solidaried"
    ],
)
def test_bloc_label_members_only_for_untruncated_labels_of_siglas(label, members):
    assert bloc_label_members(label) == members


def test_untruncated_label_with_a_non_sigla_token_stays_unresolved(blocs):
    labels = ["NovoCidPv", "PT", *LEADERSHIP]
    assert map_party_to_bench("NOVO", labels, blocs, "2022-03-08") == (None, None, "bloc_unresolved", None)


def test_party_in_two_untruncated_labels_is_ambiguous(blocs):
    labels = ["PpMdbPtb", "PscPtb", "PT"]
    assert map_party_to_bench("PTB", labels, blocs, "2022-03-08") == (None, None, "ambiguous", None)
    assert map_party_to_bench("PSC", labels, blocs, "2022-03-08")[:3] == ("PscPtb", "bloc_label", None)


def test_party_in_untruncated_label_while_unresolved_row_names_it_is_ambiguous(blocs):
    labels = ["PscPtb", "PtbAbc...", "PT"]
    assert map_party_to_bench("PTB", labels, blocs, "2022-03-08") == (None, None, "ambiguous", None)


def test_benches_version():
    assert BENCHES_VERSION == "1.2"


# --- never mapped -----------------------------------------------------------------------


@pytest.mark.parametrize("party", ["PT", "PL", "NOVO"])
def test_leadership_rows_never_orient_a_party(blocs, party):
    labels = ["Governo", "GOV.", "Maioria", "Minoria", "Oposição"]
    assert map_party_to_bench(party, labels, blocs, "2021-06-01") == (None, None, "no_bench_row", None)


def test_party_without_row_and_without_blocs(blocs):
    assert map_party_to_bench("DC", ["PL", "PT", *LEADERSHIP], blocs, "2026-03-10") == (
        None, None, "no_bench_row", None,
    )


def test_party_outside_every_fully_known_bloc_has_no_row(blocs):
    # 2026: both bloc rows have a documented full composition and DC is in neither.
    assert map_party_to_bench("DC", LABELS_2025_NOV, blocs, "2026-03-10") == (None, None, "no_bench_row", None)


def test_no_party(blocs):
    # A deputy without party is not a party missing its row: keep the two apart in the report.
    assert map_party_to_bench("S.PART.", LABELS_2021, blocs, "2021-06-01") == (None, None, "no_party", None)
    assert map_party_to_bench("", LABELS_2021, blocs, "2021-06-01") == (None, None, "no_party", None)


# --- evidence ---------------------------------------------------------------------------


def test_every_non_party_match_carries_evidence(blocs):
    cases = [
        ("PT", LABELS_2025_NOV, "2025-11-04", "federation"),
        ("PSD", LABELS_2025_NOV, "2025-11-04", "bloc_api"),
        ("PRD", LABELS_2025_NOV, "2025-11-04", "bloc_curated"),
        ("PTB", LABELS_2019_JUL, "2019-07-10", "bloc_label"),
    ]
    for party, labels, iso, basis in cases:
        res = map_party_to_bench(party, labels, blocs, iso)
        assert res.match_basis == basis and res.match_evidence


# --- coverage ---------------------------------------------------------------------------


def test_measure_coverage():
    results = [
        BenchMatch("PL", "party", None),
        BenchMatch("Fdr PT-PCdoB-PV", "federation", None, f"{API_URL}/blocos/584"),
        BenchMatch("Bl UniPpPsd...", "bloc_api", None, f"{API_URL}/blocos/589/partidos"),
        (None, None, "bloc_unresolved", None),
        (None, None, "bloc_unresolved"),
        BenchMatch(None, None, "no_bench_row"),
    ]
    cov = measure_coverage(results)
    assert cov["total"] == 6
    assert cov["matched"] == 3 and cov["unmatched"] == 3
    assert cov["by_basis"] == {"party": 1, "federation": 1, "bloc_api": 1}
    assert cov["by_reason"] == {"bloc_unresolved": 2, "no_bench_row": 1}
    assert cov["matched_pct"] == 50.0


def test_measure_coverage_empty():
    assert measure_coverage([]) == {
        "total": 0, "matched": 0, "unmatched": 0, "matched_pct": 0.0, "by_basis": {}, "by_reason": {},
    }
