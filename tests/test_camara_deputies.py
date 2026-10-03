"""Câmara deputies crawler (API /deputados) against fixture JSON (no network)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from curl_cffi import CurlError

from elosys.camara import deputies
from elosys.db import connect, create_schema

FIXTURE = Path(__file__).parent / "fixtures" / "camara_deputies" / "api_responses.json"
API_BASE = "https://dadosabertos.camara.leg.br/api/v2"
T = "2026-01-01T00:00:00Z"
TODAY = date(2026, 10, 2)   # legislature 56 finished, 57 ongoing
REAL_FETCH = deputies._fetch


def _person(con, cpf, *, trusted=1, history=()):
    pid = con.execute("INSERT INTO people (cpf, cpf_trusted, canonical_name, created_at) VALUES (?, ?, 'X', ?)",
                      (cpf, trusted, T)).lastrowid
    for full_name, birth in history:
        con.execute(
            "INSERT INTO politician_history (person_id, cpf, cpf_trusted, full_name, year, office, "
            "birth_date, provenance_id, collected_at) VALUES (?, ?, ?, ?, 2022, 'DEPUTADO FEDERAL', ?, 1, ?)",
            (pid, cpf, trusted, full_name, birth, T))
    return pid


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    create_schema(path)
    con = connect(path, write=True)
    con.execute("INSERT INTO source (id, name, agency, type, base_url, created_at) "
                "VALUES (1, 'TSE', 'a', 'csv', 'u', ?)", (T,))
    con.execute("INSERT INTO collection (id, source_id, url, accessed_at, payload_sha256, size_bytes) "
                "VALUES (1, 1, 'u', ?, 'h', 0)", (T,))
    con.execute("INSERT INTO parse (id, collection_id, parser_name, parser_version, run_at) "
                "VALUES (1, 1, 'p', '1', ?)", (T,))
    ids = {
        # 1001: cpf + same birth date (name differs on purpose: birth date alone is enough)
        "joao": _person(con, "11122233396", history=[("JOAO SILVA", "1970-05-10")]),
        # 1002: cpf + birth differs, but the civil name agrees after normalization
        "maria": _person(con, "22233344405", history=[("MARIA DE SOUZA", "1980-01-01")]),
        # 1003: two trusted people share the cpf
        "amb1": _person(con, "33344455508", history=[("PEDRO AMBIGUO", "1960-01-01")]),
        "amb2": _person(con, "33344455508", history=[("PEDRO AMBIGUO", "1960-01-01")]),
        # 1004: only an UNTRUSTED people row has this cpf
        "untrusted": _person(con, "44455566619", trusted=0, history=[("ANA FORA", "1975-03-03")]),
        # 1007: cpf matches, but neither birth date nor name agree
        "other": _person(con, "55566677720", history=[("ALGUEM DIFERENTE", "1999-09-09")]),
    }
    con.commit()
    con.close()

    responses = json.loads(FIXTURE.read_text(encoding="utf-8"))
    calls: list[str] = []

    def fake_fetch(url, dest):
        assert url.startswith(API_BASE), url
        assert len(calls) < 100, "runaway pagination"
        calls.append(url)
        body = responses[url[len(API_BASE):]]
        if isinstance(body, Exception):
            raise body
        text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_text(text, encoding="utf-8")
        return 200, "application/json"

    monkeypatch.setattr(deputies, "_fetch", fake_fetch)
    return path, ids, calls, responses


def _run(con, tmp_path, today=TODAY):
    return deputies.run(con, tmp_dir=tmp_path / "tmp", today=today)


def _deputy(con, did):
    return con.execute("SELECT * FROM camara_deputy WHERE camara_deputy_id = ?", (did,)).fetchone()


def _terms(con, did):
    return [tuple(r) for r in con.execute(
        "SELECT legislature, started_at, start_date, ended_at, situation, in_office FROM camara_deputy_term "
        "WHERE camara_deputy_id = ? ORDER BY started_at", (did,))]


def _counts(con):
    return {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            for t in ("camara_deputy", "camara_deputy_term", "collection", "parse")}


def _tmp_files(tmp_path):
    return [p for p in (tmp_path / "tmp").rglob("*") if p.is_file()]


def test_follows_pagination_and_dedupes_roster(db, tmp_path):
    path, _, calls, _ = db
    con = connect(path, write=True)
    rep = _run(con, tmp_path)

    assert f"{API_BASE}/deputados?idLegislatura=56&ordem=ASC&ordenarPor=id&pagina=2&itens=100" in calls
    assert rep["deputies_per_legislature"] == {"56": 5, "57": 4}
    assert rep["deputies"] == 8
    assert con.execute("SELECT count(*) FROM camara_deputy").fetchone()[0] == 8
    # one detail + one historico call per DISTINCT deputy (1001 appears 3x in the rosters)
    assert sum(c.endswith("/deputados/1001") for c in calls) == 1
    con.close()


def test_roster_pagination_loop_aborts_instead_of_spinning(db, tmp_path):
    path, _, _, responses = db
    page2 = responses["/deputados?idLegislatura=56&ordem=ASC&ordenarPor=id&pagina=2&itens=100"]
    page2["links"] = [{"rel": "next", "href": deputies.ROSTER_URL.format(leg=56)}]
    con = connect(path, write=True)
    with pytest.raises(RuntimeError, match="pagination loops back"):
        _run(con, tmp_path)
    con.close()


def test_cpf_plus_birth_date_links_person(db, tmp_path):
    path, ids, _, _ = db
    con = connect(path, write=True)
    _run(con, tmp_path)
    d = _deputy(con, 1001)
    assert (d["person_id"], d["match_basis"], d["unmatched_reason"]) == (ids["joao"], "cpf+nascimento", None)
    assert d["cpf"] == "11122233396" and d["birth_date"] == "1970-05-10"
    con.close()


def test_cpf_plus_civil_name_fallback_when_birth_differs(db, tmp_path):
    path, ids, _, _ = db
    con = connect(path, write=True)
    _run(con, tmp_path)
    d = _deputy(con, 1002)
    assert (d["person_id"], d["match_basis"]) == (ids["maria"], "cpf+nome")
    assert d["cpf"] == "22233344405"
    con.close()


def test_birth_date_agreeing_with_any_tse_row_is_enough(db):
    # TSE birth dates are sometimes mistyped in one candidacy row: one agreeing row matches
    path, _, _, _ = db
    con = connect(path, write=True)
    pid = _person(con, "77788899941", history=[("NOME TSE", "1950-01-01"), ("NOME TSE", "1970-01-01")])
    assert deputies.match_person(con, "77788899941", "1970-01-01", "OUTRO NOME") == (
        pid, "cpf+nascimento", None)
    con.close()


@pytest.mark.parametrize(("did", "reason"), [
    (1003, "cpf_ambiguous"),
    (1004, "cpf_untrusted"),
    (1005, "no_cpf"),
    (1006, "cpf_invalid"),
    (1007, "cpf_match_but_birth_and_name_differ"),
    (1008, "cpf_not_in_people"),
])
def test_unmatched_deputies_keep_null_person_and_a_reason(db, tmp_path, did, reason):
    path, _, _, _ = db
    con = connect(path, write=True)
    rep = _run(con, tmp_path)
    d = _deputy(con, did)
    assert (d["person_id"], d["match_basis"], d["unmatched_reason"]) == (None, None, reason)
    assert {"camara_deputy_id": did, "name": d["parliamentary_name"], "reason": reason} in rep["unmatched_list"]
    con.close()


def test_report_counts_by_basis_and_reason(db, tmp_path):
    path, _, _, _ = db
    con = connect(path, write=True)
    rep = _run(con, tmp_path)
    assert rep["matched"] == {"cpf+nascimento": 1, "cpf+nome": 1}
    assert rep["unmatched"] == {"no_cpf": 1, "cpf_invalid": 1, "cpf_untrusted": 1, "cpf_not_in_people": 1,
                                "cpf_ambiguous": 1, "cpf_match_but_birth_and_name_differ": 1}
    assert rep["deputies_without_terms"] == 1   # 1004: empty historico
    con.close()


def test_terms_one_row_per_status_with_leave_and_return_and_artefacts_skipped(db, tmp_path):
    path, _, _, _ = db
    con = connect(path, write=True)
    rep = _run(con, tmp_path)
    assert _terms(con, 1001) == [
        (56, "2019-02-01T11:45", "2019-02-01", "2020-03-01T10:00", "Exercício", 1),
        (56, "2020-03-01T10:00", "2020-03-01", "2020-06-15T09:00", "Exercício", 1),
        (56, "2020-06-15T09:00", "2020-06-15", "2020-09-01T10:00", "Licença", 0),
        (56, "2020-09-01T10:00", "2020-09-01", "2021-03-31T14:00", "Licença", 0),
        (56, "2021-03-31T14:00", "2021-03-31", "2023-01-31T23:59", "Exercício", 1),
        # NOT reopened by the leg-56 "Nome no início da legislatura" artefact at 2023-02-01T00:00;
        # closed at the end of finished legislature 56, so it cannot overlap legislature 57
        (56, "2023-01-31T23:59", "2023-01-31", "2023-01-31T23:59", "FIM_MANDATO", 0),
        (57, "2023-02-01T12:05", "2023-02-01", None, "Exercício", 1),
    ]
    assert rep["skipped_artefact_rows"] == 3
    assert rep["skipped_other_legislature_rows"] == 3   # legislatures 55 and 50
    con.close()


def test_row_before_legislature_start_is_skipped_and_reassuncao_is_in_office(db, tmp_path):
    # real case 74140: a leg-57 'Exercício' row dated 2022 would seat him from 2023-02-01
    path, _, _, _ = db
    con = connect(path, write=True)
    rep = _run(con, tmp_path)
    assert _terms(con, 1008) == [
        (57, "2023-03-01T10:00", "2023-03-01", "2023-03-01T11:00", "CONVOCADO", 0),
        # suplente called back: votes in the real data, so in office despite 'CONVOCADO'
        (57, "2023-03-01T11:00", "2023-03-01", "2024-05-01T00:00", "CONVOCADO", 1),
        (57, "2024-05-01T00:00", "2024-05-01", None, "SUPLENCIA", 0),
    ]
    assert rep["skipped_pre_legislature_rows"] == 1
    con.close()


def test_terms_same_timestamp_keeps_last_row_and_report_counts(db, tmp_path):
    path, _, _, _ = db
    con = connect(path, write=True)
    rep = _run(con, tmp_path)
    # CONVOCADO and the posse share 2019-05-02T15:00: the posse (last in API order) is the state
    assert _terms(con, 1002) == [
        (56, "2019-05-02T15:00", "2019-05-02", "2019-12-10T10:00", "Exercício", 1),
        (56, "2019-12-10T10:00", "2019-12-10", "2023-01-31T23:59", "SUPLENCIA", 0),
    ]
    assert rep["collapsed_same_timestamp_rows"] == 1
    desc = con.execute("SELECT status_description FROM camara_deputy_term WHERE camara_deputy_id = 1002 "
                       "AND started_at = '2019-05-02T15:00'").fetchone()[0]
    assert desc == "Entrada - Posse de Suplente - Posse como Suplente"
    assert _terms(con, 1004) == []
    # 1005 still 'Exercício' with no FIM_MANDATO in finished legislature 56: closed and flagged
    assert _terms(con, 1005) == [(56, "2019-02-01T11:45", "2019-02-01", "2023-01-31T23:59", "Exercício", 1)]
    assert rep["terms_exercising_at_end_of_finished_legislature"] == 1
    assert rep["terms"] == con.execute("SELECT count(*) FROM camara_deputy_term").fetchone()[0] == 18
    con.close()


def test_legislature_57_closes_only_once_it_is_over(db, tmp_path):
    path, _, _, _ = db
    con = connect(path, write=True)
    rep = _run(con, tmp_path, today=date(2027, 2, 1))
    assert _terms(con, 1001)[-1] == (57, "2023-02-01T12:05", "2023-02-01", "2027-01-31T23:59", "Exercício", 1)
    # 1005 (leg 56) + 1001 and 1006 still in office at the end of leg 57
    assert rep["terms_exercising_at_end_of_finished_legislature"] == 3
    con.close()


def test_report_seat_check_samples_every_month_up_to_today(db, tmp_path):
    path, _, _, _ = db
    con = connect(path, write=True)
    rep = _run(con, tmp_path)
    # leg 56: 2019-02..2023-01 = 48 months; leg 57: 2023-02..2026-09 = 44 (Oct 15th > today)
    # most in office at once: 1001, 1003, 1005 + 1002 from 2019-05-02
    assert rep["seat_check"] == {"sample_instants": 92, "max_in_office": 4, "violations": []}
    con.close()


def _seat_terms(n, state, *, start=0, in_office=1):
    return [{"camara_deputy_id": start + i, "legislature": 56, "started_at": "2019-02-01T11:45",
             "ended_at": "2019-03-01T00:00", "in_office": in_office, "state": state} for i in range(n)]


def test_seat_check_flags_a_state_over_its_seats():
    terms = (_seat_terms(8, "AC") + _seat_terms(9, "RR", start=100)
             + _seat_terms(1, "AC", start=200, in_office=0))   # out of office: does not count
    check = deputies.seat_check(terms, "2026-10-02")
    assert check["max_in_office"] == 17
    assert check["violations"] == [
        {"at": "2019-02-15T12:00", "legislature": 56, "state": "RR", "in_office": 9, "seats": 8}]


def test_seat_check_flags_more_deputies_than_the_house_has_seats():
    assert deputies.TOTAL_SEATS == 513
    check = deputies.seat_check(_seat_terms(514, "SP"), "2026-10-02")
    assert check["violations"][0] == {"at": "2019-02-15T12:00", "legislature": 56, "state": None,
                                      "in_office": 514, "seats": 513}


def test_historico_parse_counts_dropped_rows_as_rejected(db, tmp_path):
    path, _, _, _ = db
    con = connect(path, write=True)
    _run(con, tmp_path)
    rejected = {r["url"][len(API_BASE):]: (r["rows_extracted"], r["rows_rejected"]) for r in con.execute(
        "SELECT c.url, p.rows_extracted, p.rows_rejected FROM parse p JOIN collection c "
        "ON c.id = p.collection_id WHERE c.url LIKE '%/historico'")}
    assert rejected["/deputados/1001/historico"] == (7, 3)   # 3 NULL-situacao artefacts
    assert rejected["/deputados/1002/historico"] == (2, 1)   # 1 same-timestamp row collapsed
    assert rejected["/deputados/1008/historico"] == (3, 1)   # 1 row before the legislature
    con.close()


def test_never_creates_people_and_rerun_is_idempotent(db, tmp_path):
    path, _, _, _ = db
    con = connect(path, write=True)
    people_before = con.execute("SELECT count(*) FROM people").fetchone()[0]
    _run(con, tmp_path)
    first = _counts(con)
    _run(con, tmp_path)
    assert _counts(con) == first
    assert con.execute("SELECT count(*) FROM people").fetchone()[0] == people_before
    con.close()


def test_every_response_is_a_collection_and_tmp_is_cleaned(db, tmp_path):
    path, _, calls, _ = db
    con = connect(path, write=True)
    _run(con, tmp_path)
    src = con.execute("SELECT id FROM source WHERE name = ?", (deputies.SOURCE["name"],)).fetchone()["id"]
    urls = [r["url"] for r in con.execute("SELECT url FROM collection WHERE source_id = ?", (src,))]
    assert sorted(urls) == sorted(calls)
    orphan = con.execute(
        "SELECT count(*) FROM camara_deputy d LEFT JOIN parse p ON p.id = d.provenance_id "
        "WHERE p.parser_name IS NOT ? ", (deputies.PARSER_NAME,)).fetchone()[0]
    assert orphan == 0
    assert _tmp_files(tmp_path) == []
    con.close()


def _failing_rerun(path, tmp_path, match, exc=RuntimeError):
    con = connect(path, write=True)
    with pytest.raises(exc, match=match):
        _run(con, tmp_path)
    con.close()
    con = connect(path)   # fresh connection: sees only what was committed
    counts = _counts(con)
    con.close()
    return counts


def test_failure_mid_run_leaves_no_partial_tables_and_no_tmp(db, tmp_path):
    # a half-filled roster would silently make the missing deputies "never in office"
    path, _, _, responses = db
    con = connect(path, write=True)
    _run(con, tmp_path)
    con.close()
    responses["/deputados/1005/historico"] = "{not json"
    counts = _failing_rerun(path, tmp_path, "Expecting", exc=json.JSONDecodeError)
    assert counts == {"camara_deputy": 0, "camara_deputy_term": 0, "collection": 1, "parse": 1}
    assert _tmp_files(tmp_path) == []


def test_detail_without_dados_aborts_the_run(db, tmp_path):
    path, _, _, responses = db
    responses["/deputados/1003"] = {"dados": {}, "links": []}
    counts = _failing_rerun(path, tmp_path, "/deputados/1003 returned no dados")
    assert counts["camara_deputy"] == 0


def test_non_retryable_404_on_one_deputy_aborts_the_whole_run(db, tmp_path, monkeypatch):
    path, _, _, responses = db
    requested: list[str] = []

    def http_get(url):
        requested.append(url)
        if url.endswith("/deputados/1003"):
            return _Resp(404)
        return _Resp(200, body=json.dumps(responses[url[len(API_BASE):]]).encode())

    monkeypatch.setattr(deputies, "_fetch", REAL_FETCH)
    monkeypatch.setattr(deputies, "_http_get", http_get)
    monkeypatch.setattr(deputies.time, "sleep", lambda s: None)
    counts = _failing_rerun(path, tmp_path, "HTTP 404")
    assert requested.count(f"{API_BASE}/deputados/1003") == 1   # not retried
    assert counts["camara_deputy"] == 0
    assert _tmp_files(tmp_path) == []


class _Resp:
    def __init__(self, status, headers=None, body=b"{}"):
        self.status_code, self.headers, self.content = status, headers or {}, body


def test_retry_after_is_honored_on_429(monkeypatch, tmp_path):
    responses = [_Resp(429, {"Retry-After": "7"}), _Resp(200, {"Content-Type": "application/json"})]
    sleeps: list[float] = []
    monkeypatch.setattr(deputies, "_http_get", lambda url: responses.pop(0))
    monkeypatch.setattr(deputies.time, "sleep", sleeps.append)
    status, ctype = deputies._fetch(f"{API_BASE}/deputados/1", tmp_path / "x.json")
    assert (status, ctype) == (200, "application/json")
    assert 7 in sleeps


@pytest.mark.parametrize("headers", [{}, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}])
def test_429_without_numeric_retry_after_waits_the_default(monkeypatch, tmp_path, headers):
    responses = [_Resp(429, headers), _Resp(200)]
    sleeps: list[float] = []
    monkeypatch.setattr(deputies, "_http_get", lambda url: responses.pop(0))
    monkeypatch.setattr(deputies.time, "sleep", sleeps.append)
    deputies._fetch(f"{API_BASE}/deputados/1", tmp_path / "x.json")
    assert sleeps == [30, deputies.DELAY_SECONDS]


def _raise_curl(url):
    raise CurlError("connection reset")


@pytest.mark.parametrize(("http_get", "sleeps_expected", "error"), [
    (lambda url: _Resp(503), [5, 10, 20, 40], "HTTP 503"),
    (_raise_curl, [5, 10, 20, 40], "connection reset"),
    (lambda url: _Resp(429, {"Retry-After": "7"}), [7, 7, 7, 7], "HTTP 429"),
])
def test_retries_exhausted_raise_without_sleeping_after_the_last_attempt(
        monkeypatch, tmp_path, http_get, sleeps_expected, error):
    sleeps: list[float] = []
    monkeypatch.setattr(deputies, "_http_get", http_get)
    monkeypatch.setattr(deputies.time, "sleep", sleeps.append)
    with pytest.raises(RuntimeError, match=f"failed to download .*{error}"):
        deputies._fetch(f"{API_BASE}/deputados/1", tmp_path / "x.json")
    assert sleeps == sleeps_expected
    assert not (tmp_path / "x.json").exists()
