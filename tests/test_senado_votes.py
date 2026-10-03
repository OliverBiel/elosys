"""Senado roll-call crawler (API /votacao + /senador) against fixture JSON (no network)."""

from __future__ import annotations

import inspect
import json
import re
from datetime import date
from pathlib import Path

import pytest
from curl_cffi import CurlError

from elosys.camara.vote_kind import VoteKind, classify_senate
from elosys.db import connect, create_schema
from elosys.senado import votes

FIXTURE = Path(__file__).parent / "fixtures" / "senado" / "api_responses.json"
T = "2026-01-01T00:00:00Z"
REAL_FETCH = votes._fetch
SENATE_TABLES = ("senate_vote", "senate_vote_cast", "senate_vote_orientation", "senate_senator")


def _person(con, history):
    pid = con.execute("INSERT INTO people (cpf, cpf_trusted, canonical_name, created_at) VALUES (NULL, 0, 'X', ?)",
                      (T,)).lastrowid
    for full_name, birth, office in history:
        con.execute(
            "INSERT INTO politician_history (person_id, cpf, cpf_trusted, full_name, year, office, birth_date, "
            "provenance_id, collected_at) VALUES (?, NULL, 0, ?, 2018, ?, ?, 1, ?)",
            (pid, full_name, office, birth, T))
    return pid


@pytest.fixture
def responses():
    """The fake API, mutable per test: {'votes': {month: [...]}, 'orientations': ..., 'senators': ...}."""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def db(tmp_path, monkeypatch, responses):
    path = tmp_path / "t.db"
    create_schema(path)
    con = connect(path, write=True)
    con.execute("INSERT INTO source (id, name, agency, type, base_url, created_at) "
                "VALUES (1, 'TSE', 'a', 'csv', 'u', ?)", (T,))
    con.execute("INSERT INTO collection (id, source_id, url, accessed_at, payload_sha256, size_bytes) "
                "VALUES (1, 1, 'u', ?, 'h', 0)", (T,))
    con.execute("INSERT INTO parse (id, collection_id, parser_name, parser_version, run_at) VALUES (1, 1, 'p', '1', ?)",
                (T,))
    ids = {
        # 101: TSE spells the civil name in caps without accents -> same after normalize_name
        "maria": _person(con, [("MARIA JOSE DA SILVA", "1960-01-02", "SENADOR")]),
        # 102: two people share name + birth (one as suplente) -> homonyms
        "pedro1": _person(con, [("PEDRO HOMONIMO", "1950-05-05", "SENADOR")]),
        "pedro2": _person(con, [("PEDRO HOMONIMO", "1950-05-05", "1º SUPLENTE")]),
        # 103: same name, different birth date
        "ana": _person(con, [("ANA DIFERENTE", "1971-07-07", "SENADOR")]),
        # 104: name + birth agree, but only as a federal deputy (outside senate offices)
        "carlos": _person(con, [("CARLOS DEPUTADO", "1965-03-03", "DEPUTADO FEDERAL")]),
    }
    con.commit()

    def fake_fetch(url, dest):
        m = re.search(r"/votacao\?dataInicio=(\d{4}-\d{2})-01&dataFim=", url)
        o = re.search(r"/orientacaoBancada/(\d{4})(\d{2})01/\d{8}$", url)
        if m:
            payload = responses["votes"].get(m.group(1), [])
        elif o:
            payload = responses["orientations"].get(f"{o.group(1)}-{o.group(2)}", {"votacoes": []})
        else:
            code = re.search(r"/senador/(\d+)\.json$", url).group(1)
            payload = responses["senators"].get(code, {"DetalheParlamentar": {}})
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return 200, "application/json"

    monkeypatch.setattr(votes, "_fetch", fake_fetch)
    yield con, ids, tmp_path
    con.close()


def _run(con, tmp_path):
    return votes.run(con, years=[2019], tmp_dir=tmp_path / "tmp")


def test_cast_codes_follow_the_cited_table_and_unknown_codes_stay_other_with_raw(db):
    con, _, tmp_path = db
    report = _run(con, tmp_path)
    got = {(r[0], r[1]): (r[2], r[3]) for r in con.execute(
        "SELECT senate_vote_code, senator_code, vote_raw, vote FROM senate_vote_cast "
        "WHERE senate_vote_code IN (5953, 6005)")}
    assert got == {
        (5953, 101): ("Sim", "yes"),
        (5953, 102): ("Não", "no"),
        (5953, 103): ("Abstenção", "abstention"),
        (5953, 104): ("P-NRV", "present_not_voted"),
        (5953, 105): ("Presidente (art. 51 RISF)", "presiding"),
        (5953, 106): ("AP", "absent_justified"),
        (6005, 101): ("NA", "other"),
        (6005, 102): ("MERC", "other"),
        (6005, 103): ("P-OD", "obstruction"),
        (6005, 104): ("NCom", "absent"),
        (6005, 105): ("LS", "absent_justified"),
        (6005, 106): ("Sim", "yes"),
    }
    assert report["unknown_vote_codes"] == {
        "NA": {"casts": 1, "api_description": ["Dispositivo não citado"]},
        "MERC": {"casts": 1, "api_description": []},
    }
    row = con.execute("SELECT name, party_abbr, state FROM senate_vote_cast "
                      "WHERE senate_vote_code = 5953 AND senator_code = 102").fetchone()
    assert tuple(row) == ("Pedro Homônimo", "MDB", "MG")


def test_secret_vote_keeps_totals_and_open_vote_totals_stay_null(db):
    con, _, tmp_path = db
    _run(con, tmp_path)
    rows = {r[0]: tuple(r[1:]) for r in con.execute(
        "SELECT senate_vote_code, is_secret, yes_count, no_count, abstention_count FROM senate_vote")}
    assert rows[6008] == (1, 2, 1, 0)
    assert rows[5953] == (0, None, None, None)
    secret_casts = {r[0]: r[1] for r in con.execute(
        "SELECT senator_code, vote FROM senate_vote_cast WHERE senate_vote_code = 6008")}
    assert secret_casts == {101: "secret", 102: "secret", 103: "secret", 104: "absent_justified"}


def test_informe_text_is_the_session_record_verbatim_and_null_when_the_api_omits_it(db, responses):
    con, _, tmp_path = db
    # 6249 (PLP 19/2019): resultadoVotacao 'A' is the destaque's fate; the record says the text's.
    record = ("(Sessão Deliberativa Remota realizada em 03/11/2020) \n"
              "Rejeitada a Emenda nº 12 – PLEN, destacada, com o seguinte resultado: Sim - 12; Não - 50; "
              "Pres. - 1; Total - 63.  ")
    by_code = {v["codigoSessaoVotacao"]: v for v in responses["votes"]["2019-09"] + responses["votes"]["2019-10"]}
    by_code[6005]["informeLegislativo"]["texto"] = record
    by_code[7001]["informeLegislativo"] = None
    _run(con, tmp_path)
    got = dict(con.execute("SELECT senate_vote_code, informe_text FROM senate_vote"))
    assert got[6005] == record
    assert got[7001] is None
    assert got[5953] is None  # texto: null


def test_create_schema_adds_informe_text_to_an_existing_senate_vote_table(tmp_path):
    path = tmp_path / "old.db"
    create_schema(path)
    con = connect(path, write=True)
    con.execute("ALTER TABLE senate_vote DROP COLUMN informe_text")
    con.close()
    create_schema(path)
    con = connect(path)
    assert "informe_text" in {r[1] for r in con.execute("PRAGMA table_info(senate_vote)")}
    con.close()


def test_plenary_filter_uses_the_session_not_the_matter_colegiado_and_counts_drops(db):
    con, _, tmp_path = db
    report = _run(con, tmp_path)
    stored = {r[0]: (r[1], r[2], r[3]) for r in con.execute(
        "SELECT senate_vote_code, house, body, date FROM senate_vote")}
    # 5953 has informeLegislativo.siglaColegiado NULL but is a DOR plenary vote
    assert stored == {
        5953: ("SF", None, "2019-04-03"),
        6005: ("SF", "PLEN", "2019-09-03"),
        6008: ("SF", None, "2019-09-11"),
        6009: ("SF", None, "2019-09-11"),
        7001: ("SF", None, "2019-10-02"),
        7002: ("SF", None, "2019-10-02"),
    }
    assert report["filter"]["dropped"] == {"no_votos": 1, "session_type_not_deliberative_plenary": 1}
    assert sorted((d["senate_vote_code"], d["reason"]) for d in report["filter"]["dropped_list"]) == [
        (5981, "no_votos"), (9001, "session_type_not_deliberative_plenary")]
    assert report["api_votes_seen"] == 8
    assert report["votes_per_month"] == {"2019-04": 1, "2019-09": 3, "2019-10": 2}


def test_count_check_lists_secret_votes_whose_totals_disagree_with_votou_rows(db):
    con, _, tmp_path = db
    report = _run(con, tmp_path)
    cc = report["count_check"]
    assert cc["secret_votes_checked"] == 2
    assert cc["open_votes_totals_not_published"] == 4
    assert [(m["senate_vote_code"], m["api_totals"], m["casts"]) for m in cc["mismatches"]] == [
        (6009, {"secret": 2}, {"secret": 3})]


def test_senator_identity_needs_name_and_birth_of_exactly_one_person_and_creates_nobody(db):
    con, ids, tmp_path = db
    people_before = con.execute("SELECT count(*) FROM people").fetchone()[0]
    report = _run(con, tmp_path)
    got = {r[0]: (r[1], r[2], r[3]) for r in con.execute(
        "SELECT senator_code, person_id, match_basis, unmatched_reason FROM senate_senator")}
    assert got == {
        101: (ids["maria"], "nome+nascimento", None),
        102: (None, None, "homonyms"),
        103: (None, None, "name_found_birth_differs"),
        104: (None, None, "not_in_history"),
        105: (None, None, "no_birth_date"),
        106: (None, None, "no_detail"),
    }
    assert con.execute("SELECT count(*) FROM people").fetchone()[0] == people_before
    assert report["senators"]["matched"] == 1
    assert report["senators"]["total"] == 6


def test_rerun_rewrites_instead_of_accumulating(db):
    con, _, tmp_path = db
    _run(con, tmp_path)
    tables = ("senate_vote", "senate_vote_cast", "senate_senator", "senate_vote_orientation", "collection",
              "parse")
    first = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables}
    _run(con, tmp_path)
    second = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables}
    assert second == first
    assert first["senate_vote"] == 6
    assert first["senate_vote_cast"] == 21
    assert first["senate_vote_orientation"] == 8


def test_orientation_links_by_sequence_or_unique_tally_and_normalizes_labels(db):
    con, _, tmp_path = db
    _run(con, tmp_path)
    got = {(r[0], r[1]): tuple(r[2:]) for r in con.execute(
        "SELECT senate_vote_code, bench_label, link_basis, bench_kind, bench_norm, orientation_raw, orientation "
        "FROM senate_vote_orientation")}
    assert got == {
        # panel vote dated the day after the session (past midnight), same sequence and 1 x 1 tally
        (5953, "Podemos"): ("sequencial", "party", "PODE", "SIM", "yes"),
        (5953, "PT"): ("sequencial", "party", "PT", "NÃO", "no"),
        (5953, "Governo"): ("sequencial", "government", None, "NÃO", "no"),
        (5953, "Banc Fem"): ("sequencial", "other", None, "LIVRE", "free"),
        (5953, "MDB"): ("sequencial", "party", "MDB", "", "none"),
        # 6009 has no sequencialVotacao: linked by same day + secret totals 2 x 0
        (6009, "PROGRES"): ("placar", "party", "PP", "OBSTRUÇÃO", "obstruction"),
        (6009, "Bloco Parlamentar X"): ("placar", "bloc", None, "SIM", "yes"),
        (6009, "Liderança Y"): ("placar", "other", None, "NÃO", "no"),
    }


def test_orientation_rows_of_unlinkable_panel_votes_are_reported_not_inserted(db):
    con, _, tmp_path = db
    report = _run(con, tmp_path)["orientations"]
    assert sorted((u["panel_vote_code"], u["reason"], u["orientations"]) for u in report["unlinked_panel_votes"]) == [
        (2562, "congress_session", 1),
        (2993, "sequence_match_but_date_or_tally_differs", 1),
        (9200, "ambiguous_tally", 1),
    ]
    assert report["orientation_rows_unlinked_not_inserted"] == 3
    assert con.execute("SELECT count(*) FROM senate_vote_orientation WHERE senate_vote_code IN "
                       "(6005, 7001, 7002)").fetchone()[0] == 0
    assert report["unknown_labels"] == {"Liderança Y": 1}


def test_orientation_coverage_counts_votes_and_vote_party_pairs(db):
    con, _, tmp_path = db
    cov = _run(con, tmp_path)["orientations"]["coverage_per_year"]["2019"]
    # 6 votes, 2 oriented (5953, 6009). Pairs: 5953 has 6 parties (PT oriented NÃO; MDB's row is a
    # panel null = no orientation), 6005: 6, 6008: 4, 6009: 3 (PP oriented, no PP senator voted),
    # 7001/7002: 1 each -> 21 pairs, 1 with its own party's orientation.
    assert (cov["votes"], cov["votes_with_orientation"]) == (6, 2)
    assert (cov["vote_party_pairs"], cov["pairs_with_party_orientation"]) == (21, 1)
    assert cov["pairs_in_oriented_votes"] == 9
    assert cov["pairs_excluded_without_party"] == 0


@pytest.mark.parametrize("no_party", ["S/Partido", "Sem registro"])
def test_coverage_ignores_null_orientations_and_senators_without_party(db, responses, no_party):
    con, _, tmp_path = db
    for o in responses["orientations"]["2019-04"]["votacoes"][0]["orientacoesLideranca"]:
        o["voto"] = None   # 5953: every bench row present but none says how to vote
    responses["votes"]["2019-10"][0]["votos"][0]["siglaPartidoParlamentar"] = no_party
    responses["votes"]["2019-10"][1]["votos"][0]["siglaPartidoParlamentar"] = None
    cov = _run(con, tmp_path)["orientations"]["coverage_per_year"]["2019"]
    assert con.execute("SELECT count(*) FROM senate_vote_orientation WHERE senate_vote_code = 5953"
                       ).fetchone()[0] == 5
    assert (cov["votes"], cov["votes_with_orientation"]) == (6, 1)
    assert (cov["vote_party_pairs"], cov["pairs_excluded_without_party"]) == (19, 2)
    assert (cov["pairs_with_party_orientation"], cov["pairs_in_oriented_votes"]) == (0, 3)


def _counts(path):
    con = connect(path)   # fresh connection: sees only what was committed
    src = con.execute("SELECT id FROM source WHERE name = ?", (votes.SOURCE["name"],)).fetchone()
    out = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in SENATE_TABLES}
    out["collection"] = con.execute("SELECT count(*) FROM collection WHERE source_id = ?",
                                    (src[0] if src else -1,)).fetchone()[0]
    con.close()
    return out


def _tmp_files(tmp_path):
    return sorted(p.name for p in (tmp_path / "tmp").rglob("*") if p.is_file())


@pytest.mark.parametrize("years", [[2018], [], [2019, 2027]])
def test_bad_years_raise_before_the_reset_and_leave_the_data_intact(db, years):
    con, _, tmp_path = db
    _run(con, tmp_path)
    before = _counts(tmp_path / "t.db")
    with pytest.raises(ValueError):
        votes.run(con, years=years, tmp_dir=tmp_path / "tmp")
    assert _counts(tmp_path / "t.db") == before
    assert before["senate_vote"] == 6


@pytest.mark.parametrize(("fail_on", "exc"), [
    ("dataInicio=2019-09-01", json.JSONDecodeError),   # votes: month body is not JSON
    ("orientacaoBancada/20191001", RuntimeError),      # orientations: download fails
    ("/senador/106.json", RuntimeError),               # last senator: download fails
])
def test_failure_mid_run_leaves_empty_tables_and_no_tmp_files(db, monkeypatch, fail_on, exc):
    # a half-filled dataset would look complete: votes up to the failure, no senators
    con, _, tmp_path = db
    _run(con, tmp_path)
    fake_fetch = votes._fetch

    def failing_fetch(url, dest):
        if fail_on not in url:
            return fake_fetch(url, dest)
        if exc is json.JSONDecodeError:
            Path(dest).write_text("{not json", encoding="utf-8")
            return 200, "application/json"
        raise RuntimeError(f"failed to download {url}: HTTP 503")

    monkeypatch.setattr(votes, "_fetch", failing_fetch)
    with pytest.raises(exc):
        _run(con, tmp_path)
    assert _counts(tmp_path / "t.db") == {t: 0 for t in (*SENATE_TABLES, "collection")}
    assert not con.in_transaction   # rolled back: a later commit on this connection adds nothing
    assert con.execute("SELECT count(*) FROM senate_vote").fetchone()[0] == 0
    assert _tmp_files(tmp_path) == []


def test_two_panel_votes_linked_to_one_stored_vote_abort_the_run(db, responses):
    con, _, tmp_path = db
    panel = responses["orientations"]["2019-04"]["votacoes"]
    panel.append({**panel[0], "codigoVotacaoSve": 2570})   # same sequence, date and tally as 2561
    with pytest.raises(RuntimeError, match="vote 5953 linked to panel votes 2561 and 2570"):
        _run(con, tmp_path)
    assert _counts(tmp_path / "t.db")["senate_vote"] == 0


def test_months_cover_whole_calendar_months_up_to_today():
    months = votes._months([2020], date(2026, 10, 2))
    assert len(months) == 12
    assert (date(2020, 2, 1), date(2020, 2, 29)) in months
    assert months[-1] == (date(2020, 12, 1), date(2020, 12, 31))
    current = votes._months([2026], date(2026, 10, 2))
    assert len(current) == 10
    assert current[-1] == (date(2026, 10, 1), date(2026, 10, 31))


class _Resp:
    def __init__(self, status, headers=None, body=b"[]"):
        self.status_code, self.headers, self.content = status, headers or {}, body


def _raise_curl(url):
    raise CurlError("connection reset")


@pytest.mark.parametrize(("first", "sleeps_expected"), [
    (_Resp(429, {"Retry-After": "7"}), [7]),
    (_Resp(429), [votes._DEFAULT_RETRY_AFTER]),
    (_Resp(502), [5]),
    (CurlError("connection reset"), [5]),
])
def test_fetch_retries_429_5xx_and_network_errors_then_succeeds(monkeypatch, tmp_path, first, sleeps_expected):
    answers = [first, _Resp(200, {"Content-Type": "application/json"})]
    sleeps: list[float] = []

    def http_get(url):
        a = answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a

    monkeypatch.setattr(votes, "_http_get", http_get)
    monkeypatch.setattr(votes.time, "sleep", sleeps.append)
    assert votes._fetch(votes.API_BASE + "/x", tmp_path / "x.json") == (200, "application/json")
    assert sleeps == [*sleeps_expected, votes.DELAY_SECONDS]
    assert (tmp_path / "x.json").read_bytes() == b"[]"


@pytest.mark.parametrize(("http_get", "sleeps_expected", "error"), [
    (lambda url: _Resp(503), [5, 10, 20, 40], "HTTP 503"),
    (_raise_curl, [5, 10, 20, 40], "connection reset"),
    (lambda url: _Resp(429, {"Retry-After": "7"}), [7, 7, 7, 7], "HTTP 429"),
])
def test_fetch_gives_up_after_five_attempts_without_sleeping_after_the_last(
        monkeypatch, tmp_path, http_get, sleeps_expected, error):
    sleeps: list[float] = []
    monkeypatch.setattr(votes, "_http_get", http_get)
    monkeypatch.setattr(votes.time, "sleep", sleeps.append)
    with pytest.raises(RuntimeError, match=f"failed to download .*{error}"):
        votes._fetch(votes.API_BASE + "/x", tmp_path / "x.json")
    assert sleeps == sleeps_expected
    assert not (tmp_path / "x.json").exists()


def test_fetch_raises_on_404_without_retrying(monkeypatch, tmp_path):
    calls: list[str] = []
    sleeps: list[float] = []
    monkeypatch.setattr(votes, "_http_get", lambda url: calls.append(url) or _Resp(404))
    monkeypatch.setattr(votes.time, "sleep", sleeps.append)
    with pytest.raises(RuntimeError, match="HTTP 404"):
        votes._fetch(votes.API_BASE + "/senador/1.json", tmp_path / "x.json")
    assert (len(calls), sleeps) == (1, [])


def test_every_row_traces_to_the_api_response_it_came_from(db):
    con, _, tmp_path = db
    _run(con, tmp_path)

    def origin(table, where):
        return tuple(con.execute(
            f"SELECT c.url, s.name, p.parser_name FROM {table} t JOIN parse p ON p.id = t.provenance_id "
            f"JOIN collection c ON c.id = p.collection_id JOIN source s ON s.id = c.source_id WHERE {where}"
        ).fetchone())

    name = votes.SOURCE["name"]
    assert origin("senate_vote", "senate_vote_code = 5953") == (
        votes.VOTES_URL.format(start="2019-04-01", end="2019-04-30"), name, "senado.votes")
    assert origin("senate_vote_cast", "senate_vote_code = 6008 AND senator_code = 104") == (
        votes.VOTES_URL.format(start="2019-09-01", end="2019-09-30"), name, "senado.votes")
    assert origin("senate_senator", "senator_code = 101") == (
        votes.SENATOR_URL.format(code=101), name, "senado.votes")
    assert origin("senate_vote_orientation", "senate_vote_code = 6009 AND bench_label = 'PROGRES'") == (
        votes.ORIENTATION_URL.format(start="20190901", end="20190930"), name, "senado.votes")
    assert _tmp_files(tmp_path) == []


def test_parse_rows_extracted_equals_the_rows_stored_under_it(db):
    con, _, tmp_path = db
    _run(con, tmp_path)
    union = " UNION ALL ".join(f"SELECT provenance_id FROM {t}" for t in SENATE_TABLES)
    got = {r[0]: (r[1], r[2]) for r in con.execute(
        f"SELECT p.id, p.rows_extracted, (SELECT count(*) FROM ({union}) u WHERE u.provenance_id = p.id) "
        "FROM parse p WHERE p.parser_name = ?", (votes.PARSER_NAME,))}
    assert got and all(extracted == stored for extracted, stored in got.values())
    assert sum(stored for _, stored in got.values()) == 6 + 21 + 8 + 6


def _stored(code, date_, tally, sequence=None):
    return {"senate_vote_code": code, "date": date_, "tally": tally, "sequence": sequence}


def _panel(code, at, sim, nao, sequence=None):
    return {"codigoVotacaoSve": code, "sequencialVotacao": sequence, "dataInicioVotacao": at,
            "qtdVotosSim": sim, "qtdVotosNao": nao, "votosParlamentar": [], "tipoSessao": "Deliberativa"}


def test_two_panel_votes_claiming_one_unsequenced_vote_are_both_ambiguous():
    stored = [_stored(6009, "2019-09-11", (2, 0))]
    panel = [_panel(1, "2019-09-11T16:00:00", 2, 0), _panel(2, "2019-09-11T17:00:00", 2, 0)]
    assert [(o["codigoVotacaoSve"], code, basis) for o, code, basis in votes.link_panel_votes(panel, stored)] == [
        (1, None, "ambiguous_tally"), (2, None, "ambiguous_tally")]


@pytest.mark.parametrize(("at", "expected"), [
    ("2019-09-10T23:00:00", (None, "sequence_match_but_date_or_tally_differs")),
    ("2019-09-11T16:00:00", (6008, "sequencial")),
    ("2019-09-12T00:30:00", (6008, "sequencial")),
    ("2019-09-13T16:00:00", (None, "sequence_match_but_date_or_tally_differs")),
])
def test_sequence_link_needs_session_day_or_the_next(at, expected):
    stored = [_stored(6008, "2019-09-11", (2, 1), sequence=3002)]
    [(_, code, basis)] = votes.link_panel_votes([_panel(9, at, 2, 1, sequence=3002)], stored)
    assert (code, basis) == expected


@pytest.mark.parametrize(("label", "expected"), [
    ("Governo", ("government", None)), ("Maioria", ("majority", None)), ("Minoria", ("minority", None)),
    ("Oposição", ("opposition", None)), ("B.Feminina", ("other", None)), ("Banc Fem", ("other", None)),
    ("Republica", ("party", "REPUBLICANOS")), ("PRB", ("party", "REPUBLICANOS")), ("PR", ("party", "PL")),
    ("PPS", ("party", "CIDADANIA")), ("Cidadania", ("party", "CIDADANIA")), ("PROGRES", ("party", "PP")),
    ("Podemos", ("party", "PODE")), ("PSD", ("party", "PSD")), ("Bl Vanguarda", ("bloc", None)),
    ("Bloco Parlamentar Democracia", ("bloc", None)), ("Liderança Y", ("other", None)), ("PSDB", ("other", None)),
])
def test_normalize_bench(label, expected):
    assert votes.normalize_bench(label, {"PSD", "PT"}) == expected


def test_plenary_filter_audit_counts_by_body(db):
    con, _, tmp_path = db
    f = _run(con, tmp_path)["filter"]
    assert f["by_body"] == {"dropped_body_NULL": 2, "kept_body_NULL": 5, "kept_body_PLEN": 1}
    assert f["kept_with_body_not_PLEN"] == 5


def test_unusable_orientation_rows_are_counted_apart_from_panel_vote_links(db, responses):
    con, _, tmp_path = db
    rows = responses["orientations"]["2019-04"]["votacoes"][0]["orientacoesLideranca"]
    rows += [{"dataHora": None, "partido": "  ", "voto": "SIM"}, {"dataHora": None, "partido": "PT", "voto": "SIM"}]
    o = _run(con, tmp_path)["orientations"]
    assert o["orientation_rows_rejected"] == {"orientation_without_label": 1, "duplicate_label_in_vote": 1}
    assert set(o["link"]) == {"sequencial", "placar", "congress_session", "ambiguous_tally",
                              "sequence_match_but_date_or_tally_differs"}
    assert con.execute("SELECT orientation_raw FROM senate_vote_orientation "
                       "WHERE senate_vote_code = 5953 AND bench_label = 'PT'").fetchone()[0] == "NÃO"


def test_missing_full_name_and_history_without_birth_are_their_own_reasons(db, responses):
    con, _, tmp_path = db
    ident = responses["senators"]["103"]["DetalheParlamentar"]["Parlamentar"]["IdentificacaoParlamentar"]
    ident["NomeCompletoParlamentar"] = " "
    _person(con, [("CARLOS DEPUTADO", None, "2º SUPLENTE")])   # 104: senate row, no birth
    report = _run(con, tmp_path)
    got = {r[0]: r[1] for r in con.execute("SELECT senator_code, unmatched_reason FROM senate_senator")}
    assert (got[103], got[104]) == ("no_full_name", "history_without_birth")
    assert report["senators"]["unmatched_by_reason"]["history_without_birth"] == 1


def test_a_differing_birth_date_wins_over_a_history_row_without_birth(db):
    con, _, _ = db
    _person(con, [("ANA DIFERENTE", None, "1º SUPLENTE")])
    index = votes.SenatorIndex(con)
    assert index.match("Ana Diferente", "1970-07-07") == (None, None, "name_found_birth_differs")


def test_presence_audit_lists_voter_only_lists_and_lists_over_81(db, responses):
    con, _, tmp_path = db
    extra = [{"codigoParlamentar": 1000 + i, "nomeParlamentar": f"S{i}", "siglaPartidoParlamentar": "PT",
              "siglaUFParlamentar": "DF", "siglaVotoParlamentar": "Sim"} for i in range(81)]
    responses["votes"]["2019-10"][0]["votos"] += extra   # 7001: 82 rows
    report = _run(con, tmp_path)
    assert [(v["senate_vote_code"], v["casts"]) for v in report["votes_listing_only_voters"]] == [
        (6009, 3), (7002, 1)]
    assert [(v["senate_vote_code"], v["casts"]) for v in report["votes_with_more_than_81"]] == [(7001, 82)]
    assert report["p_nrv_entries"] == 1


class _Classifier:
    def __init__(self, takes_is_secret):
        self.calls: dict[str, dict] = {}
        if takes_is_secret:
            def classify(description, identification=None, *, is_secret=False):
                self.calls[identification] = {"is_secret": is_secret}
                return VoteKind("other", None, "fake")
        else:
            def classify(description, identification=None):
                self.calls[identification] = {}
                return VoteKind("other", None, "fake")
        self.classify = classify


@pytest.mark.parametrize("takes_is_secret", [True, False])
def test_classifier_gets_identification_and_is_secret_when_it_accepts_it(db, monkeypatch, takes_is_secret):
    con, _, tmp_path = db
    fake = _Classifier(takes_is_secret)
    monkeypatch.setattr(votes, "classify_senate", fake.classify)
    _run(con, tmp_path)
    if takes_is_secret:
        assert fake.calls == {"PEC 34/2019": {"is_secret": False}, "PEC 82/2019": {"is_secret": False},
                              "OFS 29/2019": {"is_secret": True}, "OFS 30/2019": {"is_secret": True},
                              "PL 7/2019": {"is_secret": False}}
    else:
        assert set(fake.calls) == {"PEC 34/2019", "PEC 82/2019", "OFS 29/2019", "OFS 30/2019", "PL 7/2019"}


@pytest.mark.skipif("is_secret" not in inspect.signature(classify_senate).parameters,
                    reason="vote_kind.classify_senate has no is_secret yet")
def test_secret_ofs_vote_without_a_telling_description_is_a_nomination(db, responses):
    con, _, tmp_path = db
    responses["votes"]["2019-09"][1]["descricaoVotacao"] = "Votação nominal."   # 6008, secret OFS
    _run(con, tmp_path)
    row = con.execute("SELECT kind, meaning_code, kind_basis FROM senate_vote WHERE senate_vote_code = 6008"
                      ).fetchone()
    assert tuple(row) == ("nomination", "approve_nomination", "senate_secret_msf_ofs")
