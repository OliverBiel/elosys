"""camara-votes crawler against tiny bulk-CSV fixtures (no network).

tests/fixtures/camara/votes/ holds one year (2025) in the real file format (UTF-8 BOM, ';', all fields
quoted, CRLF inside a descricao). Votes in votacoes-2025.csv:
  2483860-10  PLEN nominal (3 Sim, 1 Não, 1 Artigo 17), oriented by a party, a federation and a
              truncated bloc (2025-11-05: inside the cited window of API bloc 589)
  2483860-11  PLEN symbolic (no votacoesVotos rows)            -> dropped
  2399001-5   committee (CCJC) nominal                          -> dropped
  2576389-4   PLEN secret (empty voto, yet votosSim 388 / votosNao 22 as in the real file) -> kept,
              is_secret = 1, excluded from the count check
  2520670-66  PLEN, with an idLegislatura=56 row and a "Talvez" row -> both cast rows rejected
  2600000-1   PLEN, aprovacao 'X'                               -> vote rejected
votacoesOrientacoes-2025.csv also has, for 2520670-66, an exact duplicate PL row, a conflicting PL
row ("Sim" after "Não") and a row with too few fields.
Other scenarios (legislature boundary, mixed secret, vote without propositions) are written as
local files into tmp_dir by _local_year().
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import NamedTuple

import pytest

from elosys.camara import votes
from elosys.db import connect, create_schema

FIX = Path(__file__).parent / "fixtures" / "camara"
CSV_FIX = FIX / "votes"

_ALL_TABLES = (
    "camara_vote", "camara_vote_cast", "camara_vote_orientation", "camara_vote_proposition",
    "camara_session_presence", "camara_proposition_theme", "camara_vote_party_bench",
)


@pytest.fixture
def net(monkeypatch):
    """Fake download (bulk CSVs) and API fetch; records every URL asked for."""
    calls: dict[str, list[str]] = {"download": [], "api": []}

    def fake_download(url, dest):
        calls["download"].append(url)
        shutil.copyfile(CSV_FIX / url.rsplit("/", 1)[-1], dest)
        return 200, "application/octet-stream"

    def fake_fetch_json(url, dest):
        calls["api"].append(url)
        if "/partidos" in url:
            bloc_id = url.split("/blocos/")[1].split("/")[0]
            src = FIX / f"bloco-{bloc_id}-partidos.json"
            body = src.read_bytes() if src.exists() else json.dumps({"dados": []}).encode()
        else:
            leg = url.split("idLegislatura=")[1].split("&")[0]
            body = (FIX / f"blocos-leg{leg}.json").read_bytes()
        Path(dest).write_bytes(body)
        return 200, "application/json"

    monkeypatch.setattr(votes, "download", fake_download)
    monkeypatch.setattr(votes, "_fetch_json", fake_fetch_json)
    return calls


@pytest.fixture
def con(tmp_path):
    path = tmp_path / "t.db"
    create_schema(path)
    c = connect(path, write=True)
    c.execute("INSERT INTO people (cpf, cpf_trusted, canonical_name, created_at) "
              "VALUES ('11144477735', 1, 'ANA ALVES', '2026-01-01T00:00:00Z')")
    c.commit()
    yield c
    c.close()


def _run(con, tmp_path):
    tmp = tmp_path / "dl"
    tmp.mkdir(exist_ok=True)
    return votes.run(con, years=[2025], tmp_dir=tmp)


def _counts(con) -> dict[str, int]:
    return {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in _ALL_TABLES}  # noqa: S608


def test_keeps_only_plen_nominal_votes_and_accounts_for_every_row(con, tmp_path, net):
    rep = _run(con, tmp_path)

    ids = {r[0] for r in con.execute("SELECT camara_vote_id FROM camara_vote")}
    assert ids == {"2483860-10", "2576389-4", "2520670-66"}
    assert rep["years"][2025]["plen_nominal_votes"] == 3

    files = rep["years"][2025]["files"]
    assert files["votacoes"]["dropped"] == {"not_plen": 1, "symbolic": 1}
    assert files["votacoes"]["rejected"] == {"invalid_aprovacao": 1}
    assert files["votacoesVotos"]["rejected"] == {"legislature_mismatch": 1, "unknown_vote_value": 1}
    assert files["votacoesVotos"]["dropped"] == {"not_plen": 1, "vote_not_in_votacoes": 1, "vote_rejected": 1}
    assert files["votacoesOrientacoes"]["rejected"] == {
        "duplicate": 1, "conflicting_duplicate": 1, "malformed_row": 1}
    assert files["eventosPresencaDeputados"]["dropped"] == {"event_not_stored": 1}
    for tally in files.values():
        assert tally["rows"] == tally["inserted"] + sum(tally["rejected"].values()) + sum(tally["dropped"].values())

    assert _counts(con) == {
        "camara_vote": 3, "camara_vote_cast": 12, "camara_vote_orientation": 8, "camara_vote_proposition": 4,
        "camara_session_presence": 12, "camara_proposition_theme": 2, "camara_vote_party_bench": 12,
    }
    assert rep["tables"] == _counts(con)


def test_vote_fields_verbatim_and_classified_with_counts(con, tmp_path, net):
    _run(con, tmp_path)
    v = con.execute("SELECT * FROM camara_vote WHERE camara_vote_id = '2483860-10'").fetchone()
    assert v["date"] == "2025-11-05"
    assert v["registered_at"] == "2025-11-05T19:42:10"  # Brasília local time, no conversion
    assert v["legislature"] == 57
    assert v["event_id"] == 74600
    assert (v["approved"], v["yes_count"], v["no_count"], v["other_count"]) == (1, 3, 1, 1)
    assert v["description"] == "Aprovado o Projeto de Lei nº 1.234, de 2025.\r\nSim: 3; Não: 1; Total: 5."
    assert v["last_filed_proposition_id"] == 2483860
    assert (v["kind"], v["meaning_code"], v["kind_basis"]) == ("merito", "approve_text", "main_text")
    assert v["is_secret"] == 0

    # '0' is the source's "no item" sentinel.
    assert con.execute("SELECT last_filed_proposition_id FROM camara_vote WHERE camara_vote_id = '2520670-66'"
                       ).fetchone()[0] is None


def test_classifier_receives_counts_and_unambiguous_proposition_type(con, tmp_path, net, monkeypatch):
    seen = {}
    real = votes.classify_camara

    def spy(description, opening, yes_count=None, no_count=None, *, proposition_type=None):
        seen[description] = (yes_count, no_count, proposition_type)
        return real(description, opening, yes_count=yes_count, no_count=no_count,
                    proposition_type=proposition_type)

    monkeypatch.setattr(votes, "classify_camara", spy)
    rep = _run(con, tmp_path)
    by_vote = {r[0]: seen[r[1]] for r in con.execute("SELECT camara_vote_id, description FROM camara_vote")}
    assert by_vote["2483860-10"] == (3, 1, "PL")
    assert by_vote["2576389-4"] == (388, 22, "MSC")
    # 2520670-66 is linked to a PL and a PEC: which one the vote is about is unknown -> no type.
    assert by_vote["2520670-66"] == (2, 2, None)
    assert rep["ambiguous_proposition_type_vote_ids"] == ["2520670-66"]


def test_secret_vote_stays_secret(con, tmp_path, net):
    rep = _run(con, tmp_path)
    v = con.execute("SELECT is_secret, kind FROM camara_vote WHERE camara_vote_id = '2576389-4'").fetchone()
    assert v["is_secret"] == 1
    casts = con.execute("SELECT vote_raw, vote FROM camara_vote_cast WHERE camara_vote_id = '2576389-4'").fetchall()
    assert len(casts) == 3
    assert {(c["vote_raw"], c["vote"]) for c in casts} == {("", "secret")}
    assert rep["secret_votes"] == 1


def test_article_17_is_presiding_not_absence(con, tmp_path, net):
    _run(con, tmp_path)
    row = con.execute("SELECT vote_raw, vote FROM camara_vote_cast WHERE camara_vote_id = '2483860-10' "
                      "AND camara_deputy_id = 204005").fetchone()
    assert (row["vote_raw"], row["vote"]) == ("Artigo 17", "presiding")
    got = dict(con.execute("SELECT camara_deputy_id, vote FROM camara_vote_cast WHERE camara_vote_id = '2483860-10'"))
    assert got == {204001: "yes", 204002: "yes", 204003: "yes", 204004: "no", 204005: "presiding"}


def test_corrupt_legislature_and_unknown_vote_rows_are_not_stored(con, tmp_path, net):
    _run(con, tmp_path)
    deps = {r[0] for r in con.execute("SELECT camara_deputy_id FROM camara_vote_cast "
                                      "WHERE camara_vote_id = '2520670-66'")}
    assert deps == {204001, 204002, 204004, 204006}


def test_count_check_ok_and_excludes_secret_votes(con, tmp_path, net):
    rep = _run(con, tmp_path)
    assert rep["count_check"]["checked"] == 2
    assert rep["count_check"]["mismatches"] == []


def test_count_check_lists_mismatching_votes(con, tmp_path, net):
    _run(con, tmp_path)
    con.execute("UPDATE camara_vote SET yes_count = 9 WHERE camara_vote_id = '2483860-10'")
    check = votes.count_check(con)
    assert [m["camara_vote_id"] for m in check["mismatches"]] == ["2483860-10"]


def test_orientations_normalized_with_raw_kept(con, tmp_path, net):
    _run(con, tmp_path)
    rows = {r["bench_label"]: (r["bench_kind"], r["orientation_raw"], r["orientation"]) for r in con.execute(
        "SELECT * FROM camara_vote_orientation WHERE camara_vote_id = '2483860-10'")}
    assert rows == {
        "PL": ("party", "Sim", "yes"),
        "Fdr PT-PCdoB-PV": ("federation", "Sim", "yes"),
        "Bl UniPpPsd...": ("bloc", "Sim", "yes"),
        "PSOL": ("party", "", "none"),
        "Novo": ("party", "Liberado", "free"),
        "Governo": ("government", "Sim", "yes"),
        "Minoria": ("minority", "Talvez", "other"),
    }


def test_party_bench_mapping_with_evidence(con, tmp_path, net):
    rep = _run(con, tmp_path)
    got = {r["party_abbr"]: (r["bench_label"], r["match_basis"], r["match_evidence"], r["unmatched_reason"])
           for r in con.execute("SELECT * FROM camara_vote_party_bench WHERE camara_vote_id = '2483860-10'")}
    api = "https://dadosabertos.camara.leg.br/api/v2/blocos"
    # bloc_api evidence = the /partidos URL plus the cited window that dates it (benches.py).
    for party in ("UNIÃO", "MDB"):
        label, basis, evidence, reason = got.pop(party)
        assert (label, basis, reason) == ("Bl UniPpPsd...", "bloc_api", None)
        assert evidence.startswith(f"{api}/589/partidos")
    assert got == {
        "PL": ("PL", "party", None, None),
        "PSOL": ("PSOL", "party", None, None),
        "PT": ("Fdr PT-PCdoB-PV", "federation", f"{api}/584", None),
    }
    secret = {r[0] for r in con.execute("SELECT unmatched_reason FROM camara_vote_party_bench "
                                        "WHERE camara_vote_id = '2576389-4'")}
    assert secret == {"no_bench_row"}
    assert rep["bench"]["by_basis"] == {"party": 3, "federation": 1, "bloc_api": 2}
    assert rep["bench"]["by_reason"] == {"no_bench_row": 6}
    # Only legislature 57 is needed for 2025 votes: one list + one /partidos per bloc.
    assert net["api"][0].endswith("/blocos?idLegislatura=57&itens=100")
    assert len(net["api"]) == 1 + 7


def test_evidence_from_bench_match_namedtuple_is_used(con, tmp_path, net, monkeypatch):
    class BenchMatch(NamedTuple):
        bench_label: str | None
        match_basis: str | None
        unmatched_reason: str | None
        match_evidence: str | None

    seen_dates = []

    def fake_map(party, labels, blocs, vote_date):
        seen_dates.append(vote_date)
        return BenchMatch("Bl UniPpPsd...", "bloc_curated", None, "https://www.camara.leg.br/x")

    monkeypatch.setattr(votes, "map_party_to_bench", fake_map)
    _run(con, tmp_path)
    assert {r[0] for r in con.execute("SELECT match_evidence FROM camara_vote_party_bench")} == {
        "https://www.camara.leg.br/x"}
    assert all(isinstance(d, str) and len(d) == 10 for d in seen_dates)


def test_presence_only_for_events_of_stored_votes(con, tmp_path, net):
    _run(con, tmp_path)
    events = {r[0] for r in con.execute("SELECT DISTINCT event_id FROM camara_session_presence")}
    assert events == {74600, 74601, 74602}
    assert votes._presence_check(con) == {"votes_without_presence_list": 0, "casts_not_in_presence_list": 0}
    con.execute("DELETE FROM camara_session_presence WHERE event_id = 74602 AND camara_deputy_id = 204006")
    assert votes._presence_check(con) == {"votes_without_presence_list": 0, "casts_not_in_presence_list": 1}


def test_themes_downloaded_only_for_stored_proposition_years(con, tmp_path, net):
    rep = _run(con, tmp_path)
    themes = [u.rsplit("/", 1)[-1] for u in net["download"] if "proposicoesTemas" in u]
    assert sorted(themes) == ["proposicoesTemas-2024.csv", "proposicoesTemas-2025.csv"]
    got = {(r[0], r[1], r[2]) for r in con.execute(
        "SELECT proposition_id, theme_code, theme FROM camara_proposition_theme")}
    assert got == {(2483860, 46, "Educação"), (2576389, 34, "Administração Pública")}
    assert rep["themes"]["files"][2025]["dropped"] == {"proposition_not_stored": 1}
    assert rep["themes"]["files"][2025]["rejected"] == {"duplicate": 1}
    assert rep["theme_coverage_pct"] == 66.7  # 2520670-66 has no theme


def test_kind_report(con, tmp_path, net):
    rep = _run(con, tmp_path)
    assert rep["kind"]["total"] == 3
    assert rep["kind"]["by_kind"]["merito"] == 1
    assert rep["kind"]["by_kind"]["nomination"] == 1


def test_provenance_resolves_to_source_and_matches_parse_counts(con, tmp_path, net):
    _run(con, tmp_path)
    for table in _ALL_TABLES:
        names = {r[0] for r in con.execute(
            f"SELECT DISTINCT s.name FROM {table} t JOIN parse p ON p.id = t.provenance_id "  # noqa: S608
            "JOIN collection c ON c.id = p.collection_id JOIN source s ON s.id = c.source_id")}
        assert names == {votes.SOURCE["name"]}, table
        bad = con.execute(
            f"SELECT p.id FROM parse p JOIN (SELECT provenance_id, count(*) n FROM {table} "  # noqa: S608
            "GROUP BY provenance_id) t ON t.provenance_id = p.id WHERE p.rows_extracted != t.n").fetchall()
        assert bad == [], table
    rejected = con.execute(
        "SELECT p.rows_rejected FROM parse p JOIN collection c ON c.id = p.collection_id "
        "WHERE c.url LIKE '%votacoesVotos-2025.csv' AND p.parser_name = ?", (votes.PARSER_NAME,)).fetchone()[0]
    assert rejected == 2
    api_cols = con.execute("SELECT count(*) FROM collection c JOIN source s ON s.id = c.source_id "
                           "WHERE s.name = ?", (votes.API_SOURCE["name"],)).fetchone()[0]
    assert api_cols == 8


def test_never_creates_people(con, tmp_path, net):
    _run(con, tmp_path)
    assert con.execute("SELECT count(*) FROM people").fetchone()[0] == 1


def test_rerun_is_idempotent(con, tmp_path, net):
    first = _run(con, tmp_path)
    counts = _counts(con)
    second = _run(con, tmp_path)
    assert _counts(con) == counts
    assert second["years"] == first["years"]
    n_collections = con.execute("SELECT count(*) FROM collection").fetchone()[0]
    assert n_collections == 7 + 8  # 7 CSV files + 8 API responses, nothing left from the first run


def test_local_file_is_used_and_kept_downloads_are_removed(con, tmp_path, net):
    tmp = tmp_path / "dl"
    tmp.mkdir()
    shutil.copyfile(CSV_FIX / "votacoes-2025.csv", tmp / "votacoes-2025.csv")
    votes.run(con, years=[2025], tmp_dir=tmp)
    assert not any(u.endswith("/votacoes-2025.csv") for u in net["download"])
    assert sorted(p.name for p in tmp.iterdir()) == ["votacoes-2025.csv"]
    status = con.execute("SELECT http_status FROM collection WHERE url LIKE '%/votacoes-2025.csv'").fetchone()[0]
    assert status is None


def test_vote_repeated_in_another_year_file_is_counted_not_duplicated(con, tmp_path, net, monkeypatch):
    def same_file_for_2024(url, dest):
        name = url.rsplit("/", 1)[-1]
        if "proposicoesTemas" not in name:
            name = name.replace("-2024.", "-2025.")
        shutil.copyfile(CSV_FIX / name, dest)
        return 200, "application/octet-stream"

    monkeypatch.setattr(votes, "download", same_file_for_2024)
    tmp = tmp_path / "dl"
    tmp.mkdir()
    rep = votes.run(con, years=[2024, 2025], tmp_dir=tmp)
    assert con.execute("SELECT count(*) FROM camara_vote").fetchone()[0] == 3
    dup = rep["years"][2025]["files"]["votacoes"]["dropped"]
    assert dup["already_stored_from_other_file"] == 3
    # Reported once, from the file that stored them (2024), not once per file.
    assert rep["kind"]["total"] == 3
    assert rep["ambiguous_proposition_type_vote_ids"] == ["2520670-66"]
    assert rep["bench"]["total"] == 12


def test_unknown_year_rejected(con, tmp_path, net):
    with pytest.raises(ValueError):
        votes.run(con, years=[2018], tmp_dir=tmp_path)


def test_unexpected_header_fails_loudly(con, tmp_path, net):
    tmp = tmp_path / "dl"
    tmp.mkdir()
    (tmp / "votacoes-2025.csv").write_text('﻿"id";"data"\r\n"1-1";"2025-01-01"\r\n', encoding="utf-8")
    with pytest.raises(ValueError, match="siglaOrgao"):
        votes.run(con, years=[2025], tmp_dir=tmp)


def test_conflicting_orientation_keeps_the_first_and_is_listed(con, tmp_path, net):
    rep = _run(con, tmp_path)
    assert rep["conflicting_orientation_ids"] == ["2520670-66 PL"]
    row = con.execute("SELECT orientation_raw FROM camara_vote_orientation "
                      "WHERE camara_vote_id = '2520670-66' AND bench_label = 'PL'").fetchone()
    assert row[0] == "Não"


def test_party_bench_parse_version_tracks_benches(con, tmp_path, net):
    _run(con, tmp_path)
    versions = {r[0] for r in con.execute("SELECT parser_version FROM parse WHERE parser_name = ?",
                                          (votes.PARTY_BENCH_PARSER_NAME,))}
    assert versions == {f"{votes.PARSER_VERSION}+benches{votes.BENCHES_VERSION}"}


def test_votes_after_the_last_cited_bloc_window_are_reported(con, tmp_path, net, monkeypatch, caplog):
    rep = _run(con, tmp_path)
    assert rep["votes_after_last_window_end"]["count"] == 0
    monkeypatch.setattr(votes, "LAST_WINDOW_END", "2025-06-01")
    with caplog.at_level(logging.WARNING, logger="elosys.camara.votes"):
        rep = _run(con, tmp_path)
    assert rep["votes_after_last_window_end"] == {
        "last_window_end": "2025-06-01", "count": 2, "ids": ["2520670-66", "2483860-10"]}
    assert "extend benches.py" in caplog.text


# --- failures: one transaction, nothing half-written, no files left behind ----------------


def _tables_empty(con) -> bool:
    return all(n == 0 for n in _counts(con).values())


@pytest.mark.parametrize("missing", ["votacoesOrientacoes-2025.csv", "proposicoesTemas-2024.csv"])
def test_missing_file_fails_loudly_and_leaves_no_partial_data(con, tmp_path, net, monkeypatch, missing):
    _run(con, tmp_path)
    real = votes.download

    def download_404(url, dest):
        if url.endswith("/" + missing):
            Path(dest).write_bytes(b'"idVotacao";"trunc')  # what a cut connection leaves behind
            raise RuntimeError(f"failed to download {url}: HTTP 404")
        return real(url, dest)

    monkeypatch.setattr(votes, "download", download_404)
    with pytest.raises(RuntimeError, match="404"):
        _run(con, tmp_path)
    # Rolled back to the committed reset: empty, never earlier years or votes without benches.
    assert _tables_empty(con)
    assert con.execute("SELECT count(*) FROM collection").fetchone()[0] == 0
    assert list((tmp_path / "dl").iterdir()) == []


def test_blocs_api_failure_rolls_back_the_votes(con, tmp_path, net, monkeypatch):
    def api_down(url, dest):
        raise RuntimeError(f"failed to fetch {url}: HTTP 503")

    monkeypatch.setattr(votes, "_fetch_json", api_down)
    with pytest.raises(RuntimeError, match="503"):
        _run(con, tmp_path)
    assert _tables_empty(con)
    assert list((tmp_path / "dl").iterdir()) == []


def test_bad_year_is_rejected_before_the_reset(con, tmp_path, net):
    _run(con, tmp_path)
    before = _counts(con)
    with pytest.raises(ValueError):
        votes.run(con, years=[2025, 2018], tmp_dir=tmp_path / "dl")
    assert _counts(con) == before


def test_downloads_go_through_part_files_and_stale_part_is_ignored(con, tmp_path, net, monkeypatch):
    tmp = tmp_path / "dl"
    tmp.mkdir()
    (tmp / "votacoes-2025.csv.part").write_bytes(b'"id";"da')  # left by a crashed run
    dests = []
    real = votes.download

    def spy(url, dest):
        dests.append(Path(dest).name)
        return real(url, dest)

    monkeypatch.setattr(votes, "download", spy)
    votes.run(con, years=[2025], tmp_dir=tmp)
    assert dests and all(d.endswith(".csv.part") for d in dests)
    assert "votacoes-2025.csv.part" in dests  # the stale .part was not taken as a local file
    assert list(tmp.iterdir()) == []


# --- synthetic years written as local files -----------------------------------------------


def _write_csv(path: Path, header, rows) -> None:
    lines = [";".join(f'"{h}"' for h in header)]
    lines += [";".join(f'"{r.get(h, "")}"' for h in header) for r in rows]
    path.write_text("﻿" + "\r\n".join(lines) + "\r\n", encoding="utf-8", newline="")


def _local_year(tmp: Path, year: int, **datasets) -> Path:
    tmp.mkdir(exist_ok=True)
    for name, header in votes._REQUIRED.items():
        if name != "proposicoesTemas":
            _write_csv(tmp / f"{name}-{year}.csv", header, datasets.get(name, []))
    for prop_year, rows in datasets.get("themes", {}).items():
        _write_csv(tmp / f"proposicoesTemas-{prop_year}.csv", votes._REQUIRED["proposicoesTemas"], rows)
    return tmp


def _vote(vid, day, yes=1, no=0, desc="Aprovado o Requerimento.", **kw):
    return {"id": vid, "data": day, "dataHoraRegistro": f"{day}T10:00:00", "siglaOrgao": "PLEN",
            "idEvento": "1", "aprovacao": "1", "votosSim": str(yes), "votosNao": str(no), "votosOutros": "0",
            "descricao": desc, "ultimaApresentacaoProposicao_idProposicao": "0", **kw}


def _cast(vid, dep, voto, leg):
    return {"idVotacao": vid, "voto": voto, "deputado_id": str(dep), "deputado_nome": f"Dep {dep}",
            "deputado_siglaPartido": "PL", "deputado_siglaUf": "SP", "deputado_idLegislatura": str(leg)}


def test_legislature_guard_at_the_2023_boundary(con, tmp_path, net):
    # 2023-01-31 is the last day of legislature 56; 2023-02-01 already belongs to 57.
    tmp = _local_year(tmp_path / "dl", 2023,
                      votacoes=[_vote("1-1", "2023-01-31"), _vote("1-2", "2023-02-01")],
                      votacoesVotos=[_cast("1-1", 1, "Sim", 56), _cast("1-2", 1, "Sim", 56),
                                     _cast("1-2", 2, "Sim", 57)])
    rep = votes.run(con, years=[2023], tmp_dir=tmp)
    assert dict(con.execute("SELECT camara_vote_id, legislature FROM camara_vote")) == {"1-1": 56, "1-2": 57}
    stored = {tuple(r) for r in con.execute("SELECT camara_vote_id, camara_deputy_id FROM camara_vote_cast")}
    assert stored == {("1-1", 1), ("1-2", 2)}
    assert rep["years"][2023]["files"]["votacoesVotos"]["rejected"] == {"legislature_mismatch": 1}


def test_empty_vote_mixed_with_sim_nao_makes_the_vote_secret(con, tmp_path, net):
    tmp = _local_year(tmp_path / "dl", 2023,
                      votacoes=[_vote("2-1", "2023-03-01", yes=1, no=9), _vote("2-2", "2023-03-01")],
                      votacoesVotos=[_cast("2-1", 1, "", 57), _cast("2-1", 2, "Sim", 57),
                                     _cast("2-1", 3, "Não", 57), _cast("2-2", 1, "Sim", 57)])
    rep = votes.run(con, years=[2023], tmp_dir=tmp)
    secret = dict(con.execute("SELECT camara_vote_id, is_secret FROM camara_vote"))
    assert secret == {"2-1": 1, "2-2": 0}
    assert rep["mixed_secret_vote_ids"] == ["2-1"]
    # votosNao 9 vs 1 stored "Não" would be a mismatch: the secret vote is outside the check.
    assert rep["count_check"] == {"checked": 1, "mismatches": [], "ok": True}


def test_vote_without_propositions_is_stored_and_counts_in_theme_coverage(con, tmp_path, net, monkeypatch):
    seen = {}
    real = votes.classify_camara

    def spy(description, opening, yes_count=None, no_count=None, *, proposition_type=None):
        seen[description] = proposition_type
        return real(description, opening, yes_count=yes_count, no_count=no_count,
                    proposition_type=proposition_type)

    monkeypatch.setattr(votes, "classify_camara", spy)
    prop = {"idVotacao": "5-1", "proposicao_id": "77", "proposicao_siglaTipo": "PL", "proposicao_ano": "2023"}
    theme = {"uriProposicao": "https://dadosabertos.camara.leg.br/api/v2/proposicoes/77", "codTema": "46",
             "tema": "Educação"}
    tmp = _local_year(tmp_path / "dl", 2023,
                      votacoes=[_vote("5-1", "2023-03-01", desc="Aprovado o Projeto."),
                                _vote("5-2", "2023-03-01", desc="Aprovado o Requerimento.")],
                      votacoesVotos=[_cast("5-1", 1, "Sim", 57), _cast("5-2", 1, "Sim", 57)],
                      votacoesProposicoes=[prop], themes={2023: [theme]})
    rep = votes.run(con, years=[2023], tmp_dir=tmp)
    assert {r[0] for r in con.execute("SELECT camara_vote_id FROM camara_vote")} == {"5-1", "5-2"}
    assert seen == {"Aprovado o Projeto.": "PL", "Aprovado o Requerimento.": None}
    assert rep["theme_coverage_pct"] == 50.0


# --- API fetch ----------------------------------------------------------------------------


class _Resp(NamedTuple):
    status_code: int
    headers: dict
    content: bytes = b'{"dados": []}'


@pytest.fixture
def http(monkeypatch):
    state = {"responses": [], "calls": 0, "sleeps": []}

    def fake_get(url, **kw):
        state["calls"] += 1
        return state["responses"].pop(0)

    monkeypatch.setattr(votes.requests, "get", fake_get)
    monkeypatch.setattr(votes.time, "sleep", lambda s: state["sleeps"].append(s))
    return state


def _backoffs(state) -> list:
    return [s for s in state["sleeps"] if s != votes._API_DELAY]


def test_fetch_json_client_error_fails_at_once(http, tmp_path):
    http["responses"] = [_Resp(404, {})]
    with pytest.raises(RuntimeError, match="HTTP 404"):
        votes._fetch_json("https://x/blocos", tmp_path / "b.json")
    assert http["calls"] == 1 and _backoffs(http) == []


def test_fetch_json_retries_5xx_without_sleeping_after_the_last_try(http, tmp_path):
    http["responses"] = [_Resp(503, {}) for _ in range(votes._API_RETRIES)]
    with pytest.raises(RuntimeError, match="HTTP 503"):
        votes._fetch_json("https://x/blocos", tmp_path / "b.json")
    assert http["calls"] == votes._API_RETRIES
    assert len(_backoffs(http)) == votes._API_RETRIES - 1


def test_fetch_json_honours_retry_after_on_429(http, tmp_path):
    http["responses"] = [_Resp(429, {"Retry-After": "7"}), _Resp(200, {"Content-Type": "application/json"})]
    dest = tmp_path / "b.json"
    assert votes._fetch_json("https://x/blocos", dest) == (200, "application/json")
    assert _backoffs(http) == [7]
    assert json.loads(dest.read_bytes()) == {"dados": []}
