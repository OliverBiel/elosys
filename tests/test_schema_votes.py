"""Roll-call vote tables (Câmara + Senado) in elosys/schema.sql."""

from __future__ import annotations

import sqlite3

import pytest

from elosys.db import connect, create_schema

# table -> columns of its ux_* unique index, in order
UNIQUE_KEYS: dict[str, tuple[str, ...]] = {
    "camara_vote": ("camara_vote_id",),
    "camara_vote_proposition": ("camara_vote_id", "proposition_id"),
    "camara_proposition_theme": ("proposition_id", "theme_code"),
    "camara_vote_cast": ("camara_vote_id", "camara_deputy_id"),
    "camara_vote_orientation": ("camara_vote_id", "bench_label"),
    "camara_vote_party_bench": ("camara_vote_id", "party_abbr"),
    "camara_session_presence": ("event_id", "camara_deputy_id"),
    "camara_deputy": ("camara_deputy_id",),
    "camara_deputy_term": ("camara_deputy_id", "legislature", "started_at"),
    "senate_vote": ("senate_vote_code",),
    "senate_vote_cast": ("senate_vote_code", "senator_code"),
    "senate_senator": ("senator_code",),
    "senate_vote_orientation": ("senate_vote_code", "bench_label"),
}

T = "2026-10-02T00:00:00Z"

# A minimal valid row (NOT NULL + CHECK columns) for every table that has a CHECK.
BASE_ROWS: dict[str, dict[str, object]] = {
    "camara_vote": {"camara_vote_id": "1-1", "date": "2025-11-05", "legislature": 57,
                    "approved": 1, "is_secret": 0},
    "camara_vote_cast": {"camara_vote_id": "1-1", "camara_deputy_id": 1, "vote_raw": "Sim",
                         "vote": "yes"},
    "camara_vote_orientation": {"camara_vote_id": "1-1", "bench_label": "PL", "bench_kind": "party",
                                "orientation_raw": "Sim", "orientation": "yes"},
    "camara_vote_party_bench": {"camara_vote_id": "1-1", "party_abbr": "PL",
                                "match_basis": "bloc_curated",
                                "match_evidence": "https://www.camara.leg.br/blocos"},
    "camara_deputy": {"camara_deputy_id": 1, "match_basis": "cpf+nome"},
    "camara_deputy_term": {"camara_deputy_id": 1, "legislature": 57, "started_at": "2023-02-01T12:05",
                           "start_date": "2023-02-01", "in_office": 1},
    "senate_vote": {"senate_vote_code": 7007, "house": "SF", "body": "PLEN", "date": "2025-09-02",
                    "is_secret": 0},
    "senate_vote_cast": {"senate_vote_code": 7007, "senator_code": 1, "vote_raw": "P-NRV",
                         "vote": "present_not_voted"},
    "senate_senator": {"senator_code": 1, "match_basis": "nome+nascimento"},
}

# (table, column) -> every value its CHECK must accept. The parsers' normalization tables
# must emit exactly these; a value missing from the schema makes the crawler fail on insert.
ALLOWED: dict[tuple[str, str], tuple[object, ...]] = {
    ("camara_vote", "approved"): (0, 1),
    ("camara_vote", "is_secret"): (0, 1),
    ("camara_vote_cast", "vote"): ("yes", "no", "abstention", "obstruction", "presiding", "secret"),
    ("camara_vote_orientation", "bench_kind"): ("party", "federation", "bloc", "government",
                                                "majority", "minority", "opposition", "other"),
    ("camara_vote_orientation", "orientation"): ("yes", "no", "obstruction", "free", "none", "other"),
    ("camara_vote_party_bench", "match_basis"): ("party", "federation", "bloc_api", "bloc_curated",
                                                 "bloc_label"),
    ("camara_deputy", "match_basis"): ("cpf+nascimento", "cpf+nome"),
    ("camara_deputy_term", "in_office"): (0, 1),
    ("senate_vote", "is_secret"): (0, 1),
    ("senate_vote_cast", "vote"): ("yes", "no", "abstention", "obstruction", "secret",
                                   "present_not_voted", "absent_justified", "absent", "presiding",
                                   "other"),
    ("senate_senator", "match_basis"): ("nome+nascimento",),
}


@pytest.fixture
def con(tmp_path):
    path = tmp_path / "t.db"
    create_schema(path)
    c = connect(path, write=True)
    c.execute("INSERT INTO source (id, name, agency, type, base_url, created_at) "
              "VALUES (1, 's', 'a', 'csv', 'u', ?)", (T,))
    c.execute("INSERT INTO collection (id, source_id, url, accessed_at, payload_sha256, size_bytes) "
              "VALUES (1, 1, 'u', ?, 'h', 0)", (T,))
    c.execute("INSERT INTO parse (id, collection_id, parser_name, parser_version, run_at) "
              "VALUES (1, 1, 'p', '1', ?)", (T,))
    c.commit()
    yield c
    c.close()


def _unique_index_columns(con: sqlite3.Connection, table: str) -> list[tuple[str, ...]]:
    out = []
    for idx in con.execute(f"PRAGMA index_list({table})"):
        if idx["unique"] and idx["name"].startswith("ux_"):
            cols = con.execute(f"PRAGMA index_info({idx['name']})").fetchall()
            out.append(tuple(r["name"] for r in sorted(cols, key=lambda r: r["seqno"])))
    return out


@pytest.mark.parametrize(("table", "key"), UNIQUE_KEYS.items())
def test_vote_table_has_its_natural_unique_key(con, table, key):
    assert key in _unique_index_columns(con, table)


def test_create_schema_is_idempotent(tmp_path):
    path = tmp_path / "t.db"
    create_schema(path)
    create_schema(path)
    c = connect(path)
    names = {r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    c.close()
    assert set(UNIQUE_KEYS) <= names


def test_vote_tables_only_reference_parse_and_people(con):
    # reset_source empties a crawler's tables with foreign_keys=ON and refuses to drop its parse
    # rows while another crawler's table points at them; a FK between crawlers' tables (e.g.
    # vote_cast -> camara_deputy) would make `camara-deputies` un-resettable while votes exist.
    for table in UNIQUE_KEYS:
        targets = {r["table"] for r in con.execute(f"PRAGMA foreign_key_list({table})")}
        assert targets <= {"parse", "people"}, (table, targets)


def test_deputy_rows_can_be_wiped_while_casts_exist(con):
    con.execute("INSERT INTO camara_deputy (camara_deputy_id, parliamentary_name, provenance_id, "
                "collected_at) VALUES (204379, 'Acácio Favacho', 1, ?)", (T,))
    con.execute("INSERT INTO camara_vote_cast (camara_vote_id, camara_deputy_id, name, party_abbr, "
                "state, vote_raw, vote, provenance_id, collected_at) "
                "VALUES ('2438467-47', 204379, 'Acácio Favacho', 'MDB', 'AP', 'Sim', 'yes', 1, ?)", (T,))
    con.execute("DELETE FROM camara_deputy")
    assert con.execute("SELECT count(*) FROM camara_vote_cast").fetchone()[0] == 1


def _cast(con, vote_raw, vote):
    con.execute("INSERT INTO camara_vote_cast (camara_vote_id, camara_deputy_id, name, party_abbr, "
                "state, vote_raw, vote, provenance_id, collected_at) "
                "VALUES ('2576389-4', 1, 'X', 'PL', 'DF', ?, ?, 1, ?)", (vote_raw, vote, T))


def test_secret_vote_is_its_own_value_not_coerced(con):
    _cast(con, "", "secret")
    row = con.execute("SELECT vote, vote_raw FROM camara_vote_cast").fetchone()
    assert (row["vote"], row["vote_raw"]) == ("secret", "")


def _insert(con, table, **overrides):
    row = {**BASE_ROWS.get(table, {}), **overrides, "provenance_id": 1, "collected_at": T}
    con.execute(f"INSERT INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                tuple(row.values()))


@pytest.mark.parametrize(("table", "column", "value"),
                         [(t, c, v) for (t, c), values in ALLOWED.items() for v in values])
def test_check_columns_accept_every_allowed_value(con, table, column, value):
    _insert(con, table, **{column: value})
    assert con.execute(f"SELECT {column} FROM {table}").fetchone()[0] == value


@pytest.mark.parametrize(("table", "column", "bad"), [
    ("camara_vote_cast", "vote", "Sim"),
    ("camara_vote_orientation", "orientation", "Liberado"),
    ("camara_vote_orientation", "bench_kind", "bloco"),
    ("camara_vote_party_bench", "match_basis", "guess"),
    ("camara_deputy", "match_basis", "nome"),
    ("camara_deputy_term", "in_office", "Exercício"),
    ("senate_vote_cast", "vote", "P-NRV"),
    ("senate_senator", "match_basis", "nome"),
])
def test_normalized_columns_reject_raw_source_values(con, table, column, bad):
    with pytest.raises(sqlite3.IntegrityError):
        _insert(con, table, **{column: bad})


@pytest.mark.parametrize(("basis", "needs_evidence"), [
    ("party", False), ("federation", True), ("bloc_api", True), ("bloc_curated", True), ("bloc_label", True),
])
def test_bloc_derived_bench_match_requires_evidence(con, basis, needs_evidence):
    # a federation/bloc match is only auditable with the /blocos id or the cited official URL
    if needs_evidence:
        with pytest.raises(sqlite3.IntegrityError):
            _insert(con, "camara_vote_party_bench", match_basis=basis, match_evidence=None)
    else:
        _insert(con, "camara_vote_party_bench", match_basis=basis, match_evidence=None)
        assert con.execute("SELECT count(*) FROM camara_vote_party_bench").fetchone()[0] == 1


def test_unmatched_bench_needs_no_evidence(con):
    _insert(con, "camara_vote_party_bench", bench_label=None, match_basis=None, match_evidence=None,
            unmatched_reason="several blocs match")
    assert con.execute("SELECT count(*) FROM camara_vote_party_bench").fetchone()[0] == 1


def _term(con, started_at, situation, legislature=57):
    con.execute("INSERT INTO camara_deputy_term (camara_deputy_id, legislature, started_at, "
                "start_date, situation, in_office, provenance_id, collected_at) "
                "VALUES (74646, ?, ?, ?, ?, ?, 1, ?)",
                (legislature, started_at, started_at[:10], situation, int(situation == "Exercício"), T))


def test_deputy_term_keeps_two_status_changes_on_the_same_day(con):
    # /historico: posse at 12:05 and a leave at 18:01 the same day are both real changes
    _term(con, "2023-02-01T12:05", "Exercício")
    _term(con, "2023-02-01T18:01", "Licença")
    rows = con.execute("SELECT started_at, situation FROM camara_deputy_term "
                       "ORDER BY started_at").fetchall()
    assert [tuple(r) for r in rows] == [("2023-02-01T12:05", "Exercício"),
                                        ("2023-02-01T18:01", "Licença")]


def test_deputy_term_in_office_is_mandatory(con):
    # absence logic filters on in_office; a NULL would silently drop the row from both sides
    with pytest.raises(sqlite3.IntegrityError):
        _insert(con, "camara_deputy_term", in_office=None)


def test_deputy_term_rejects_a_status_row_twice(con):
    _term(con, "2023-02-01T12:05", "Exercício")
    with pytest.raises(sqlite3.IntegrityError):
        _term(con, "2023-02-01T12:05", "Exercício")


@pytest.mark.parametrize(("table", "sql"), [
    ("camara_vote_cast", "SELECT camara_vote_id FROM camara_vote_cast WHERE party_abbr = 'PL'"),
    ("senate_vote_cast", "SELECT senate_vote_code FROM senate_vote_cast WHERE party_abbr = 'PL'"),
])
def test_party_page_query_uses_the_party_index(con, table, sql):
    # party pages list every vote of one party; a (vote, party) index forces a full scan
    plan = " ".join(r["detail"] for r in con.execute(f"EXPLAIN QUERY PLAN {sql}"))
    assert f"SEARCH {table} USING COVERING INDEX ix_{table}_party (party_abbr=?)" in plan, plan


def test_camara_vote_last_filed_columns_are_not_named_as_the_voted_item(con):
    # ultimaApresentacaoProposicao is the last document filed (2192459-636: a Requerimento de
    # Informação), never the item voted: an "item_*" name would invite showing it as such.
    cols = {r["name"] for r in con.execute("PRAGMA table_info(camara_vote)")}
    assert {"last_filed_proposition_id", "last_filed_description"} <= cols
    assert not {c for c in cols if c.startswith("item_")}
