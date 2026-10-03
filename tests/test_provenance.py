"""reset_source: deletes a source's provenance with foreign keys off, so it must refuse when a table
it does not own still points at those rows."""

from __future__ import annotations

import sqlite3

import pytest

from elosys.db import connect, create_schema
from elosys.provenance import get_source, record_parse, reset_source

T = "2026-10-02T00:00:00Z"
SOCIAL = "TSE - rede_social_candidato"


@pytest.fixture
def con(tmp_path):
    path = tmp_path / "t.db"
    create_schema(path)
    c = connect(path, write=True)
    yield c
    c.close()


def _parse(con: sqlite3.Connection, source: str) -> int:
    sid = get_source(con, name=source, agency="TSE", type="csv", base_url="https://example.org")
    cur = con.execute(
        "INSERT INTO collection (source_id, url, accessed_at, payload_sha256, size_bytes) "
        "VALUES (?, ?, ?, ?, 1)", (sid, f"https://example.org/{source}", T, source))
    return record_parse(con, collection_id=int(cur.lastrowid), parser_name="t", parser_version="1",
                        rows_extracted=1, rows_rejected=0)


def _social_media(con: sqlite3.Connection, parse_id: int) -> None:
    con.execute("INSERT INTO social_media (tse_candidacy_id, year, platform, url, provenance_id, "
                "collected_at) VALUES ('1', 2024, 'x', 'https://x.com/a', ?, ?)", (parse_id, T))


def _social_account(con: sqlite3.Connection, parse_id: int) -> None:
    con.execute("INSERT INTO social_account (network, handle, declared_provenance_id, first_seen_at) "
                "VALUES ('x', 'a', ?, ?)", (parse_id, T))


def _count(con: sqlite3.Connection, table: str) -> int:
    return con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # noqa: S608


def test_reset_refuses_while_another_crawlers_table_points_at_the_source(con):
    pid = _parse(con, SOCIAL)
    _social_media(con, pid)
    _social_account(con, pid)  # owned by the X crawler, not by tse-social
    con.commit()
    with pytest.raises(RuntimeError, match=r"social_account\.declared_provenance_id"):
        reset_source(con, SOCIAL, ["social_media"])
    con.rollback()
    assert (_count(con, "social_media"), _count(con, "parse"), _count(con, "collection")) == (1, 1, 1)


def test_reset_deletes_own_rows_and_keeps_other_sources(con):
    pid = _parse(con, SOCIAL)
    other = _parse(con, "X/Twitter")
    _social_media(con, pid)
    _social_account(con, other)  # a foreign row on ANOTHER source's parse does not block
    con.commit()
    reset_source(con, SOCIAL, ["social_media"])
    assert _count(con, "social_media") == 0
    assert [r[0] for r in con.execute("SELECT id FROM parse")] == [other]
    assert _count(con, "collection") == 1
    assert con.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []
