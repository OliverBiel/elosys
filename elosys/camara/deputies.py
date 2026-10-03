"""Crawler: Câmara dos Deputados API /deputados -> camara_deputy + camara_deputy_term.

Legislatures 56 (2019-02-01..2023-01-31) and 57 (2023-02-01..2027-01-31), the ones that
cover the roll-call votes (2019-2026). Per distinct deputy: /deputados/{id} (CPF, civil
name, birth date -- deputados.csv has the cpf column empty) and /deputados/{id}/historico
(one row per status change: 'Exercício', 'Licença'...). Every API response is one collection; the raw
JSON only lives in tmp_dir while it is parsed.

Rewrite-only: each run resets this source's tables and collections (that reset is committed),
then writes everything in ONE transaction committed after the final row-count check. Any
failure rolls it back: the tables end up empty, never half-filled (a partial roster would
silently turn every missing deputy into "never in office").

Downstream note (absences): status changes are often stamped T00:00 or at a time that does
not match the session clock, so on the day a deputy's status changes the term rows can say
"out of office" while the deputy actually voted. A cast vote always wins, and nobody is
asserted "ausente" in a vote held on a day their status changed.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections import Counter
from datetime import date
from pathlib import Path

from curl_cffi import CurlError, requests

from ..log import RowCounter, get_logger, step
from ..provenance import get_source, record_collection, record_parse, reset_source
from ..util import cpf_is_valid, digits_only, iso_date, normalize_name, now_utc

log = get_logger("elosys.camara.deputies")

PARSER_NAME = "camara.deputies"
PARSER_VERSION = "1.0"

API_BASE = "https://dadosabertos.camara.leg.br/api/v2"
# ordenarPor=id: stable order while paginating (the roster repeats a deputy once per
# name/party variant, e.g. 1,073 rows for 613 distinct deputies in legislature 56).
ROSTER_URL = API_BASE + "/deputados?idLegislatura={leg}&itens=100&ordem=ASC&ordenarPor=id"
DETAIL_URL = API_BASE + "/deputados/{id}"
HISTORY_URL = API_BASE + "/deputados/{id}/historico"

LEGISLATURES = (56, 57)
# CF art. 44: 4-year legislatures starting Feb 1st. Status rows before the start are dropped;
# the last row of a finished legislature is closed at the end (the API's FIM_MANDATO stamp).
LEGISLATURE_START = {56: "2019-02-01", 57: "2023-02-01"}
LEGISLATURE_END = {56: "2023-01-31", 57: "2027-01-31"}
LEGISLATURE_END_AT = {leg: f"{end}T23:59" for leg, end in LEGISLATURE_END.items()}

# Seats per state for the 2018 and 2022 elections (LC 78/1993; the 2013 TSE redistribution was
# struck down by the STF). Only used for the in-office sanity check in the report.
SEATS_PER_UF = {
    "SP": 70, "MG": 53, "RJ": 46, "BA": 39, "RS": 31, "PR": 30, "PE": 25, "CE": 22, "MA": 18,
    "GO": 17, "PA": 17, "SC": 16, "PB": 12, "ES": 10, "PI": 10, "AL": 9, "AC": 8, "AM": 8,
    "AP": 8, "DF": 8, "MS": 8, "MT": 8, "RN": 8, "RO": 8, "RR": 8, "SE": 8, "TO": 8,
}
TOTAL_SEATS = sum(SEATS_PER_UF.values())

DELAY_SECONDS = 0.34   # ~3 req/s
_RETRIES = 5
_TIMEOUT = 60
_DEFAULT_RETRY_AFTER = 30

SOURCE = dict(
    name="Câmara dos Deputados - API Dados Abertos (deputados)",
    agency="Câmara dos Deputados",
    type="api",
    base_url=API_BASE + "/deputados",
    legal_basis=(
        "Dados abertos publicados pela Câmara dos Deputados (Lei 12.527/2011 - Lei de Acesso a "
        "Informação, art. 8). Cadastro do deputado (CPF, nome civil, data "
        "de nascimento) e histórico de exercício do mandato."
    ),
    notes="API REST v2 /deputados, /deputados/{id}, /deputados/{id}/historico.",
)

_OWNED_TABLES = ["camara_deputy_term", "camara_deputy"]

UNMATCHED_REASONS = ("no_cpf", "cpf_invalid", "cpf_untrusted", "cpf_not_in_people",
                     "cpf_ambiguous", "cpf_match_but_birth_and_name_differ")
MATCH_BASES = ("cpf+nascimento", "cpf+nome")


# --------------------------------------------------------------------------- HTTP

def _http_get(url: str):
    return requests.get(url, headers={"Accept": "application/json"}, impersonate="chrome",
                        timeout=_TIMEOUT)


def _fetch(url: str, dest: Path) -> tuple[int, str | None]:
    """GET url -> dest. Polite (~3 req/s), honors Retry-After on 429, retries 5xx/network.
    Any other non-200 status raises at once (and aborts the run)."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    last_error: Exception | str | None = None
    for attempt in range(_RETRIES):
        last = attempt == _RETRIES - 1
        try:
            r = _http_get(url)
        except (CurlError, OSError) as e:
            last_error = e
            if not last:
                time.sleep(5 * (2 ** attempt))
            continue
        status = r.status_code
        if status == 429:
            last_error = "HTTP 429"
            retry_after = str(r.headers.get("Retry-After") or "")
            if not last:
                time.sleep(int(retry_after) if retry_after.isdigit() else _DEFAULT_RETRY_AFTER)
            continue
        if status >= 500:
            last_error = f"HTTP {status}"
            if not last:
                time.sleep(5 * (2 ** attempt))
            continue
        if status != 200:
            raise RuntimeError(f"HTTP {status} for {url}")
        dest.write_bytes(r.content)
        time.sleep(DELAY_SECONDS)
        return status, r.headers.get("Content-Type")
    raise RuntimeError(f"failed to download {url}: {last_error}")


def _collect(con: sqlite3.Connection, source_id: int, url: str, path: Path, notes: str) -> tuple[dict, int]:
    """Download url and record it as a collection -> (parsed JSON, collection id).
    The tmp file stays until _parsed() records the parse and deletes it (run() removes
    leftovers when it fails in between)."""
    status, ctype = _fetch(url, path)
    payload = json.loads(path.read_bytes())
    collection_id, _ = record_collection(con, source_id=source_id, url=url, file=path,
                                         http_status=status, content_type=ctype, notes=notes)
    return payload, collection_id


def _parsed(con: sqlite3.Connection, collection_id: int, path: Path, rows: int, rejected: int = 0) -> int:
    parse_id = record_parse(con, collection_id=collection_id, parser_name=PARSER_NAME,
                            parser_version=PARSER_VERSION, rows_extracted=rows, rows_rejected=rejected)
    path.unlink(missing_ok=True)
    return parse_id


# --------------------------------------------------------------------------- identity

def _iso(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip()
    return iso_date(v) if "/" in v else v[:10]


def match_person(con: sqlite3.Connection, cpf_raw: str | None, birth_date: str | None,
                 civil_name: str | None) -> tuple[int | None, str | None, str | None]:
    """(person_id, match_basis, unmatched_reason). Read-only; never creates people.

    The Câmara CPF must hit exactly one people row with cpf_trusted=1 AND be corroborated by
    that person's TSE records (politician_history): same birth date -> 'cpf+nascimento';
    else same normalized civil name -> 'cpf+nome'. A CPF present only on untrusted people
    rows is 'cpf_untrusted'. Any history row agreeing is enough (a person has one row per
    candidacy; TSE birth dates are occasionally mistyped in one of them).
    """
    cpf = digits_only(cpf_raw)
    if not cpf:
        return None, None, "no_cpf"
    if not cpf_is_valid(cpf):
        return None, None, "cpf_invalid"
    people = [r[0] for r in con.execute("SELECT id FROM people WHERE cpf = ? AND cpf_trusted = 1", (cpf,))]
    if not people:
        untrusted = con.execute("SELECT 1 FROM people WHERE cpf = ? LIMIT 1", (cpf,)).fetchone()
        return None, None, "cpf_untrusted" if untrusted else "cpf_not_in_people"
    if len(people) > 1:
        return None, None, "cpf_ambiguous"
    person_id = people[0]
    history = con.execute("SELECT birth_date, full_name FROM politician_history WHERE person_id = ?",
                          (person_id,)).fetchall()
    birth = _iso(birth_date)
    if birth and any(_iso(h[0]) == birth for h in history):
        return person_id, "cpf+nascimento", None
    name = normalize_name(civil_name)
    if name and any(normalize_name(h[1]) == name for h in history):
        return person_id, "cpf+nome", None
    return None, None, "cpf_match_but_birth_and_name_differ"


# --------------------------------------------------------------------------- terms

def in_office(situation: str | None, status_description: str | None) -> int:
    """1 when the status row puts the deputy in office (able to vote), else 0.

    Checked against every Plenário roll-call cast 2019-2026 (status at the cast's time):
    'Exercício' -> 1,243,460 casts; 'CONVOCADO' with "Entrada - Reassunção / DIVERSO -
    Convocação Eleito ou Suplente" -> 2,556 casts (a suplente called back, in office until the
    next row, e.g. deputy 209189 2019-10-30T16:14..2021-09-17); every other CONVOCADO
    ("Aguardando Convocação", "Convocação Eleito ou Suplente") -> 0. 'Licença' 42,
    'SUPLENCIA' 7, 'VACANCIA' 3 casts are same-day boundary noise -> 0; all others 0.
    """
    if situation == "Exercício":
        return 1
    return int(situation == "CONVOCADO" and "Entrada - Reassun" in (status_description or ""))


def build_terms(events: list[dict], legislatures=LEGISLATURES,
                today: str | None = None) -> tuple[list[dict], Counter]:
    """One camara_deputy_term row per status row of /deputados/{id}/historico.

    `situacao` is the deputy's state AFTER that row. Values seen in the real API (2026-10):
    'Exercício', 'Licença', 'Afastado', 'CONVOCADO', 'SUPLENCIA', 'VACANCIA', 'SUSPENSO',
    'FIM_MANDATO', NULL. `in_office` (see in_office()) says whether the deputy could vote or
    be absent: 'Exercício', plus 'CONVOCADO' rows whose description is a "Reassunção".
    Party/name changes repeat the current state (e.g. 'Exercício' + "Alteração de partido").

    - started_at = dataHora verbatim (Brasília local time, minute precision, no offset);
      ended_at = started_at of the NEXT status row of the same legislature. The last row of a
      FINISHED legislature (end < `today`, ISO date, default today) ends at its last minute,
      'YYYY-01-31T23:59'; NULL only for the last row of the ongoing legislature.
      Full timestamps matter: a Licença can start mid-day and FIM_MANDATO is stamped
      2023-01-31T23:59, so absence logic compares session datetimes, not dates.
    - Rows with situacao NULL are artefacts and are skipped ('skipped_artefact_rows'): the
      API emits "Nome no início da legislatura / Partido no início da legislatura" at
      2023-02-01T00:00 for EVERY legislature the deputy ever served (deputy 74646 has them
      tagged 48, 49, 50, 51, 56, 57), so the leg-56 one lands AFTER that legislature's
      FIM_MANDATO and would otherwise reopen it.
    - Rows dated before the legislature starts are skipped ('skipped_pre_legislature_rows'):
      22 in the real API (2026-10), e.g. deputy 74140 has a leg-57 'Exercício' "Alteração de
      partido" at 2022-03-31T00:00 although sworn in on 2026-06-30; kept, it would put him in
      office (and "ausente") from 2023-02-01 and 40 deputies in BA's 39 seats.
    - Several status rows can share one dataHora (deputy 74141, 2023-12-07T16:36: 'CONVOCADO'
      "Convocação Eleito ou Suplente" then 'Exercício' "Entrada - Reassunção"). The API lists a
      legislature's rows in chronological order, so the LAST of the tie is the resulting state;
      it is the one kept ('collapsed_same_timestamp_rows' counts the dropped ones).
    - Legislatures outside `legislatures` are ignored ('skipped_other_legislature_rows').
    - 'exercising_at_end_of_finished_legislature' counts finished legislatures whose last row
      is still in office (no FIM_MANDATO): a source gap, surfaced, not patched.

    Real example (deputy 204379, Acácio Favacho, legislature 56) -> 3 rows:
      2019-02-01T11:45 Exercício   "Entrada - Posse de Eleito Titular"  ended 2022-04-11T15:52
      2022-04-11T15:52 Exercício   "Alteração de partido"               ended 2023-01-31T23:59
      2023-01-31T23:59 FIM_MANDATO "Término da Legislatura"             ended 2023-01-31T23:59
    Leave for a ministry and return (deputy 107283, Gleisi Hoffmann, legislature 57):
      2023-02-01T12:05 Exercício "Entrada - Posse de Eleito Titular"    ended 2025-03-10T19:40
      2025-03-10T19:40 Licença   "Saída - ... - Ministro de Estado"      ended 2026-04-06T10:16
      2026-04-06T10:16 Exercício "Entrada - Reassunção"                 ended NULL (ongoing)
    -> not "ausente" in votes between 2025-03-10T19:40 and 2026-04-06T10:16.
    """
    today = today or date.today().isoformat()
    stats: Counter[str] = Counter()
    by_leg: dict[int, list[dict]] = {}
    for e in events:
        leg = e.get("idLegislatura")
        if leg not in legislatures:
            stats["skipped_other_legislature_rows"] += 1
        elif e.get("situacao") is None:
            stats["skipped_artefact_rows"] += 1
        elif e["dataHora"] < LEGISLATURE_START[leg]:
            stats["skipped_pre_legislature_rows"] += 1
        else:
            by_leg.setdefault(leg, []).append(e)

    rows: list[dict] = []
    for leg in sorted(by_leg):
        latest: dict[str, dict] = {}
        for e in sorted(by_leg[leg], key=lambda e: e["dataHora"]):  # stable: API order on ties
            if e["dataHora"] in latest:
                stats["collapsed_same_timestamp_rows"] += 1
            latest[e["dataHora"]] = e
        ordered = [latest[k] for k in sorted(latest)]
        finished = LEGISLATURE_END[leg] < today
        for i, e in enumerate(ordered):
            nxt = ordered[i + 1]["dataHora"] if i + 1 < len(ordered) else None
            rows.append({
                "legislature": leg,
                "started_at": e["dataHora"],
                "start_date": e["dataHora"][:10],
                "ended_at": nxt or (LEGISLATURE_END_AT[leg] if finished else None),
                "situation": e["situacao"],
                "status_description": e.get("descricaoStatus"),
                "in_office": in_office(e["situacao"], e.get("descricaoStatus")),
                "state": e.get("siglaUf"),   # not stored: only feeds seat_check()
            })
        if finished and rows[-1]["in_office"]:
            stats["exercising_at_end_of_finished_legislature"] += 1
    return rows, stats


def seat_check(terms: list[dict], today: str) -> dict:
    """Sanity check of the in-office intervals: on the 15th of every month at 12:00 of each
    legislature (up to `today`), the deputies in office must fit the 513 seats and each
    state's must fit its seats (SEATS_PER_UF; state = the status row's siglaUf).
    `terms` are build_terms() rows plus 'camara_deputy_id'. Violations are reported only."""
    by_leg: dict[int, list[dict]] = {}
    for t in terms:
        if t["in_office"]:
            by_leg.setdefault(t["legislature"], []).append(t)
    instants = max_in_office = 0
    violations: list[dict] = []
    for leg in LEGISLATURES:
        y, m = int(LEGISLATURE_START[leg][:4]), int(LEGISLATURE_START[leg][5:7])
        while (at := f"{y}-{m:02d}-15T12:00") < LEGISLATURE_END[leg] and at[:10] <= today:
            seated = {t["camara_deputy_id"]: t["state"] for t in by_leg.get(leg, [])
                      if t["started_at"] <= at and (t["ended_at"] is None or at < t["ended_at"])}
            instants += 1
            max_in_office = max(max_in_office, len(seated))
            if len(seated) > TOTAL_SEATS:
                violations.append({"at": at, "legislature": leg, "state": None,
                                   "in_office": len(seated), "seats": TOTAL_SEATS})
            for uf, n in sorted(Counter(seated.values()).items(), key=lambda kv: str(kv[0])):
                if n > SEATS_PER_UF.get(uf, 0):
                    violations.append({"at": at, "legislature": leg, "state": uf,
                                       "in_office": n, "seats": SEATS_PER_UF.get(uf, 0)})
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return {"sample_instants": instants, "max_in_office": max_in_office, "violations": violations}


# --------------------------------------------------------------------------- run

_TMP_PATTERNS = ("roster_*.json", "deputado_*.json", "historico_*.json")


def run(con: sqlite3.Connection, *, years: list[int] | None = None,
        tmp_dir: str | Path = "dados_tmp", today: date | None = None) -> dict:
    """Crawl legislatures 56 and 57. `years` is accepted for the CLI contract and ignored:
    the API is per legislature and a deputy's identity/terms are not per year. `today`
    (default: the real date) decides which legislatures are finished."""
    tmp = Path(tmp_dir) / "camara_deputies"
    with step(log, "reset camara_deputy / camara_deputy_term"):
        reset_source(con, SOURCE["name"], _OWNED_TABLES)
    try:
        return _crawl(con, years, tmp, (today or date.today()).isoformat())
    except BaseException:
        con.rollback()
        raise
    finally:
        for pattern in _TMP_PATTERNS:
            for leftover in tmp.glob(pattern):
                leftover.unlink(missing_ok=True)


def _crawl(con: sqlite3.Connection, years: list[int] | None, tmp: Path, today: str) -> dict:
    source_id = get_source(con, **SOURCE)
    now = now_utc()

    roster: dict[int, set[int]] = {}
    roster_name: dict[int, str] = {}
    with step(log, "roster"):
        for leg in LEGISLATURES:
            url: str | None = ROSTER_URL.format(leg=leg)
            seen: set[str] = set()
            page = 0
            while url:
                if url in seen:
                    raise RuntimeError(f"roster pagination loops back to {url}")
                seen.add(url)
                page += 1
                path = tmp / f"roster_{leg}_{page}.json"
                payload, cid = _collect(con, source_id, url, path,
                                   notes=f"Câmara API roster, legislatura {leg}, página {page}.")
                items = payload.get("dados") or []
                for d in items:
                    roster.setdefault(int(d["id"]), set()).add(leg)
                    roster_name.setdefault(int(d["id"]), d.get("nome"))
                _parsed(con, cid, path, rows=len(items))
                url = next((lk["href"] for lk in payload.get("links") or [] if lk.get("rel") == "next"), None)
    per_leg = {str(leg): sum(1 for legs in roster.values() if leg in legs) for leg in LEGISLATURES}
    log.info("roster: %s distinct deputies (%s)", len(roster), per_leg)

    bases: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    unmatched_list: list[dict] = []
    all_terms: list[dict] = []
    term_stats: Counter[str] = Counter()
    rc = RowCounter(log, "deputies", every=50)
    with step(log, f"deputies ({len(roster)})"):
        for did in sorted(roster):
            rc.tick()
            path = tmp / f"deputado_{did}.json"
            payload, cid = _collect(con, source_id, DETAIL_URL.format(id=did), path,
                               notes=f"Câmara API /deputados/{did}.")
            d = payload.get("dados")
            if not d:
                raise RuntimeError(f"/deputados/{did} returned no dados")
            civil = (d.get("nomeCivil") or "").strip() or None
            parl = ((d.get("ultimoStatus") or {}).get("nomeEleitoral") or "").strip() or roster_name.get(did)
            cpf = digits_only(d.get("cpf"))
            birth = _iso(d.get("dataNascimento"))
            person_id, basis, reason = match_person(con, cpf, birth, civil)
            parse_id = _parsed(con, cid, path, rows=1)
            con.execute(
                "INSERT INTO camara_deputy (camara_deputy_id, parliamentary_name, civil_name, cpf, birth_date, "
                " birth_state, person_id, match_basis, unmatched_reason, provenance_id, collected_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (did, parl, civil, cpf, birth, d.get("ufNascimento"), person_id, basis, reason, parse_id, now),
            )
            if basis:
                bases[basis] += 1
            else:
                reasons[reason] += 1
                unmatched_list.append({"camara_deputy_id": did, "name": parl, "reason": reason})

            path = tmp / f"historico_{did}.json"
            payload, cid = _collect(con, source_id, HISTORY_URL.format(id=did), path,
                               notes=f"Câmara API /deputados/{did}/historico.")
            terms, stats = build_terms(payload.get("dados") or [], today=today)
            term_stats.update(stats)
            rejected = (stats["skipped_artefact_rows"] + stats["skipped_pre_legislature_rows"]
                        + stats["collapsed_same_timestamp_rows"])
            parse_id = _parsed(con, cid, path, rows=len(terms), rejected=rejected)
            for t in terms:
                cur = con.execute(
                    "INSERT INTO camara_deputy_term (camara_deputy_id, legislature, started_at, start_date, "
                    " ended_at, situation, status_description, in_office, provenance_id, collected_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (camara_deputy_id, legislature, started_at) DO NOTHING",
                    (did, t["legislature"], t["started_at"], t["start_date"], t["ended_at"], t["situation"],
                     t["status_description"], t["in_office"], parse_id, now),
                )
                # build_terms collapses ties, so a conflict here is a bug: never drop rows silently
                if cur.rowcount != 1:
                    raise RuntimeError(f"camara_deputy_term not inserted: deputy {did} {t}")
                all_terms.append({**t, "camara_deputy_id": did})
        rc.done()

    stored = con.execute("SELECT count(*) FROM camara_deputy").fetchone()[0]
    stored_terms = con.execute("SELECT count(*) FROM camara_deputy_term").fetchone()[0]
    if stored != len(roster) or stored_terms != len(all_terms):
        raise RuntimeError(f"row count mismatch: deputies {stored}/{len(roster)}, "
                           f"terms {stored_terms}/{len(all_terms)}")
    con.commit()

    terms_per_leg = {str(r[0]): r[1] for r in con.execute(
        "SELECT legislature, count(*) FROM camara_deputy_term GROUP BY legislature ORDER BY legislature")}
    no_terms = con.execute(
        "SELECT count(*) FROM camara_deputy d WHERE NOT EXISTS "
        "(SELECT 1 FROM camara_deputy_term t WHERE t.camara_deputy_id = d.camara_deputy_id)").fetchone()[0]
    seats = seat_check(all_terms, today)
    if seats["violations"]:
        log.warning("seat check: %s violations, e.g. %s", len(seats["violations"]), seats["violations"][:3])
    report = {
        "legislatures": list(LEGISLATURES),
        "years_ignored": years,
        "deputies": stored,
        "deputies_per_legislature": per_leg,
        "matched": {b: bases[b] for b in MATCH_BASES},
        "unmatched": {r: reasons[r] for r in UNMATCHED_REASONS},
        "unmatched_list": unmatched_list,
        "terms": stored_terms,
        "terms_per_legislature": terms_per_leg,
        "terms_in_office": sum(t["in_office"] for t in all_terms),
        "deputies_without_terms": no_terms,
        "terms_exercising_at_end_of_finished_legislature":
            term_stats["exercising_at_end_of_finished_legislature"],
        "skipped_artefact_rows": term_stats["skipped_artefact_rows"],
        "skipped_pre_legislature_rows": term_stats["skipped_pre_legislature_rows"],
        "skipped_other_legislature_rows": term_stats["skipped_other_legislature_rows"],
        "collapsed_same_timestamp_rows": term_stats["collapsed_same_timestamp_rows"],
        "seat_check": seats,
    }
    log.info("done: %s deputies (%s matched: %s; unmatched: %s), %s terms",
             stored, sum(bases.values()), dict(bases), dict(reasons), stored_terms)
    return report
