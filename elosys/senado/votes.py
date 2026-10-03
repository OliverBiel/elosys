"""Crawler: Senado Federal roll-call votes (API /dadosabertos/votacao) -> senate_vote(_cast), senate_senator.

Monthly windows; every API response is one collection, parsed straight from tmp_dir and deleted.
Only nominal PLENARY votes are stored (see ``drop_reason``), every cast keeps its raw code next to
the normalized one (see ``VOTE_CODES``), and each distinct senator is identified through
/senador/{code}.json against politician_history (see ``SenatorIndex``). People are never created.
Party/leadership orientation comes from the voting-panel feed /plenario/votacao/orientacaoBancada,
which has no codigoSessaoVotacao: see ``link_panel_votes`` for the join.

Rewrite-only: each run resets this source's tables and collections (that reset is committed),
then writes everything in ONE transaction committed after the people-unchanged check. Any
failure rolls it back: the tables end up empty, never half-filled (a run that died in 2023
would otherwise look like a complete dataset with no votes after that).
"""

from __future__ import annotations

import calendar
import functools
import inspect
import json
import sqlite3
import time
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

from curl_cffi import CurlError, requests

from ..camara.vote_kind import CLASSIFIER_VERSION, classify_senate, coverage_report
from ..log import RowCounter, get_logger, step
from ..provenance import get_source, record_collection, record_parse, reset_source
from ..util import normalize_name, now_utc

log = get_logger("elosys.senado.votes")

PARSER_NAME = "senado.votes"
PARSER_VERSION = f"1.1+kind{CLASSIFIER_VERSION}"

API_BASE = "https://legis.senado.leg.br/dadosabertos"
VOTES_URL = API_BASE + "/votacao?dataInicio={start}&dataFim={end}"
SENATOR_URL = API_BASE + "/senador/{code}.json"
ORIENTATION_URL = API_BASE + "/plenario/votacao/orientacaoBancada/{start}/{end}"

YEARS = tuple(range(2019, 2027))

DELAY_SECONDS = 0.34   # ~3 req/s
_RETRIES = 5
_TIMEOUT = 120
_DEFAULT_RETRY_AFTER = 30

SOURCE = dict(
    name="Senado Federal - API Dados Abertos (votações nominais)",
    agency="Senado Federal",
    type="api",
    base_url=API_BASE + "/votacao",
    legal_basis=(
        "Dados abertos publicados pelo Senado Federal (Lei 12.527/2011 - Lei de Acesso à "
        "Informação, art. 8). Votações nominais do Plenário e cadastro do senador (nome "
        "completo, data de nascimento)."
    ),
    notes="API REST /dadosabertos/votacao (janelas mensais) e /dadosabertos/senador/{codigo}.json.",
)

_OWNED_TABLES = ["senate_vote_orientation", "senate_vote_cast", "senate_vote", "senate_senator"]

# Senado plenary session types that deliberate, from the official list
# https://legis.senado.leg.br/dadosabertos/dados/ListaTiposSessao.json (SiglaCasa SF).
# Committee votes are a different endpoint (/votacaoComissao); a vote outside these types
# (e.g. PJS = Jovem Senador simulation) is dropped and listed in the report.
PLENARY_SESSION_TYPES = {
    "DOR": "Sessão Deliberativa Ordinária",
    "DEX": "Sessão Deliberativa Extraordinária",
    "SDR": "Sessão Deliberativa Remota",
}

# Meanings (read 2026-10-02):
# https://legis.senado.leg.br/dadosabertos/dados/ListaTiposComparecimento.json
# 'Votou' appears only in votacaoSecreta='S' votes (557/557, 2019-2026), hence 'secret'.

# Kept 'other' with the raw code (counted in unknown_vote_codes): 'NA' is the official "Dispositivo
# não citado" (an absence: 35/60 checked show LN/LAF leave on the vote page); 'MERC' is not in the
# list, closest is 'MER' "Presente no Parlamento do Mercosul".
VOTE_CODES: dict[str, tuple[str, str]] = {
    "Sim": ("yes", "Sim"),
    "Não": ("no", "Não"),
    "Abstenção": ("abstention", "Abstenção"),
    "Obstrução": ("obstruction", "Obstrução"),
    "P-OD": ("obstruction", "Presente – Obstrução Declarada"),
    "Votou": ("secret", "Votou (votação secreta)"),
    # API P-NRV; the vote page sometimes shows NCom (182/1,843 checked): the UI must say "não
    # registrou voto", never "presente" (report p_nrv_entries).
    "P-NRV": ("present_not_voted", "Presente – Não registrou voto"),
    "AP": ("absent_justified", "Atividade parlamentar"),
    "LS": ("absent_justified", "Licença saúde"),
    "LP": ("absent_justified", "Licença Particular"),
    "LG": ("absent_justified", "Licença à gestante"),
    "LAP": ("absent_justified", "Licença paternidade ou ao adotante"),
    "MIS": ("absent_justified", "Missão da Casa no País/exterior"),
    "REP": ("absent_justified", "Representação em solenidade internac./nac./reg."),
    "NCom": ("absent", "Não Compareceu"),
    "Presidente (art. 51 RISF)": ("presiding", "Presidiu votação (lista oficial: PR)"),
}

SENATE_OFFICES = ("SENADOR", "1º SUPLENTE", "2º SUPLENTE")
MATCH_BASIS = "nome+nascimento"
UNMATCHED_REASONS = ("no_detail", "no_full_name", "no_birth_date", "homonyms",
                     "name_found_birth_differs", "history_without_birth", "not_in_history")

SENATE_SEATS = 81   # CF art. 46: three senators per state and the Distrito Federal
# Cast-list rows that say the senator was away; a list without any of them names only voters.
ABSENCE_VOTES = ("absent", "absent_justified", "other")
# siglaPartidoParlamentar of a senator without party: not a bench, kept out of the coverage pairs.
# 'Sem registro' = mid party switch (Marcos do Val 2019-08, Kátia Abreu 2020-03).
NO_PARTY_ABBRS = ("S/Partido", "Sem registro")
ORIENTED_VALUES = ("yes", "no", "obstruction", "free")   # 'none' (panel null) / 'other' are not

_TMP_PATTERNS = ("votacao_*.json", "orientacao_*.json", "senador_*.json")


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


def _collect(con: sqlite3.Connection, source_id: int, url: str, path: Path, notes: str) -> tuple[object, int]:
    status, ctype = _fetch(url, path)
    payload = json.loads(path.read_bytes())
    collection_id, _ = record_collection(con, source_id=source_id, url=url, file=path,
                                         http_status=status, content_type=ctype, notes=notes)
    return payload, collection_id


def _finish_parse(con: sqlite3.Connection, parse_id: int, extracted: int, rejected: int) -> None:
    con.execute("UPDATE parse SET rows_extracted = ?, rows_rejected = ? WHERE id = ?",
                (extracted, rejected, parse_id))


# --------------------------------------------------------------------------- votes

def drop_reason(v: dict) -> str | None:
    """Why an API vote is not stored, or None to keep it.

    The plenary criterion is the SESSION (casaSessao 'SF' + a deliberative plenary session type),
    not informeLegislativo.siglaColegiado: that field belongs to the matter's tramitação entry,
    is NULL for 54/133 plenary votes of 2019 and says 'CCJ' for REQ 31/2023 - CCJ, a CCJ request
    voted in a DOR plenary session by all 81 senators.
    """
    if v.get("casaSessao") != "SF":
        return "not_senate_session"
    if v.get("siglaTipoSessao") not in PLENARY_SESSION_TYPES:
        return "session_type_not_deliberative_plenary"
    if not v.get("codigoSessaoVotacao"):
        return "no_vote_code"
    if not v.get("dataSessao"):
        return "no_date"
    if v.get("votacaoSecreta") not in ("S", "N"):
        return "unknown_secret_flag"
    if not v.get("votos"):
        # 5981 (2019-06-26, PLC 27/2017 destaque): a nominal vote published without any vote row
        return "no_votos"
    return None


def normalize_cast(raw: str) -> str:
    entry = VOTE_CODES.get(raw)
    return entry[0] if entry else "other"


@functools.cache
def _takes_is_secret(classifier) -> bool:
    return "is_secret" in inspect.signature(classifier).parameters


def _classify(v: dict):
    # is_secret (secret MSF/OFS = authority confirmation) is new in vote_kind; older ones lack it.
    kwargs = {"identification": v.get("identificacao")}
    if _takes_is_secret(classify_senate):
        kwargs["is_secret"] = v.get("votacaoSecreta") == "S"
    return classify_senate(v.get("descricaoVotacao"), **kwargs)


def _int_or_none(value) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


# --------------------------------------------------------------------------- identity

class SenatorIndex:
    """politician_history rows of senate offices, keyed by normalized full name and birth date.

    Read-only; never creates people. Match = normalized NomeCompletoParlamentar AND DataNascimento
    equal to a history row of exactly one person (any of SENADOR / 1º SUPLENTE / 2º SUPLENTE).
    No match: 'name_found_birth_differs' when a row with that name has another birth date, else
    'history_without_birth' when its rows have no birth date (60 senate-office rows, 2026-10).
    """

    def __init__(self, con: sqlite3.Connection):
        self.by_name_birth: dict[tuple[str, str], set[int]] = defaultdict(set)
        self.names: set[str] = set()
        self.names_without_birth: set[str] = set()
        placeholders = ", ".join("?" * len(SENATE_OFFICES))
        for person_id, full_name, birth in con.execute(
            f"SELECT person_id, full_name, birth_date FROM politician_history "  # noqa: S608
            f"WHERE office IN ({placeholders})", SENATE_OFFICES,
        ):
            name = normalize_name(full_name)
            if not name:
                continue
            if birth:
                self.names.add(name)
                self.by_name_birth[(name, birth[:10])].add(person_id)
            else:
                self.names_without_birth.add(name)

    def match(self, full_name: str | None, birth_date: str | None) -> tuple[int | None, str | None, str | None]:
        name = normalize_name(full_name)
        if not name:
            return None, None, "no_full_name"
        if not birth_date:
            return None, None, "no_birth_date"
        people = self.by_name_birth.get((name, birth_date[:10]), set())
        if len(people) == 1:
            return next(iter(people)), MATCH_BASIS, None
        if len(people) > 1:
            return None, None, "homonyms"
        if name in self.names:
            return None, None, "name_found_birth_differs"
        if name in self.names_without_birth:
            return None, None, "history_without_birth"
        return None, None, "not_in_history"


# --------------------------------------------------------------------------- orientation

ORIENTATION_VALUES = {"SIM": "yes", "NÃO": "no", "OBSTRUÇÃO": "obstruction", "LIVRE": "free"}

LEADERSHIP_LABELS = {
    "Governo": "government", "Maioria": "majority", "Minoria": "minority", "Oposição": "opposition",
    "Banc Fem": "other", "B.Feminina": "other",
}

# Panel label -> senate_vote_cast.party_abbr: panel truncations ('Republica', 'PROGRES') and renames
# in the TSE registry https://www.tse.jus.br/partidos/partidos-registrados-no-tse (2019: PRB ->
# REPUBLICANOS, PR -> PL, PPS -> CIDADANIA). Other labels are a party only when equal to a stored
# party_abbr; else kind 'other', bench_norm NULL, listed in the report.
PARTY_LABEL_ALIASES = {
    "Podemos": "PODE", "Republica": "REPUBLICANOS", "Republicanos": "REPUBLICANOS", "PRB": "REPUBLICANOS",
    "PROGRES": "PP", "Progressistas": "PP", "Cidadania": "CIDADANIA", "PPS": "CIDADANIA", "PR": "PL",
    "Patriota": "PATRIOTA",
}


def normalize_bench(label: str, cast_parties: set[str]) -> tuple[str, str | None]:
    """(bench_kind, bench_norm) for an orientacoesLideranca[].partido label."""
    if label in LEADERSHIP_LABELS:
        return LEADERSHIP_LABELS[label], None
    if label in PARTY_LABEL_ALIASES:
        return "party", PARTY_LABEL_ALIASES[label]
    if label in cast_parties:
        return "party", label
    if label.lower().startswith(("bloco", "bl ")):
        return "bloc", None
    return "other", None


def normalize_orientation(raw: str | None) -> str:
    if raw is None or raw == "":
        return "none"
    return ORIENTATION_VALUES.get(raw, "other")


def _panel_tallies(o: dict) -> set[tuple[int, int]]:
    rows = Counter(x.get("voto") for x in o.get("votosParlamentar") or [])
    out = {(rows["SIM"], rows["NÃO"])}
    if o.get("qtdVotosSim") is not None and o.get("qtdVotosNao") is not None:
        out.add((int(o["qtdVotosSim"]), int(o["qtdVotosNao"])))
    return out


def _same_day_or_next(panel_ts: str | None, session_date: str) -> bool:
    # A session past midnight: /votacao dates the vote by its session, the panel by the vote time
    # (14 sequence-linked votes, e.g. PEC 32/2022 session 2022-12-20, voted 2022-12-21).
    if not panel_ts:
        return False
    d, s = date.fromisoformat(panel_ts[:10]), date.fromisoformat(session_date)
    return d in (s, s + timedelta(days=1))


def _is_congress_session(o: dict) -> bool:
    text = f" {o.get('tipoSessao') or ''} {o.get('descricaoSessao') or ''} "
    return "Conjunta" in text or "Congresso" in text or " CN " in text


def link_panel_votes(panel: list[dict], stored: list[dict]) -> list[tuple[dict, int | None, str]]:
    """Link panel votes (orientation feed) to stored votes -> [(panel vote, senate_vote_code, basis/reason)].

    The feed has no codigoSessaoVotacao. 'sequencial': same sequencialVotacao, panel date = session date
    or the next day, and stored Sim/Não tally (casts; API totals for secret votes) equal to the panel's.
    'placar': only for stored votes WITHOUT sequencialVotacao (177 of 2020's remote votes): same date
    window and tally, exactly one candidate, claimed by exactly one panel vote. Else unlinked + reason.
    """
    by_seq = {v["sequence"]: v for v in stored if v.get("sequence") is not None}
    no_seq = [v for v in stored if v.get("sequence") is None]
    out: list[tuple[dict, int | None, str]] = []
    pending: list[tuple[dict, list[dict]]] = []
    for o in panel:
        tallies = _panel_tallies(o)
        v = by_seq.get(_int_or_none(o.get("sequencialVotacao")))
        if v is not None:
            if _same_day_or_next(o.get("dataInicioVotacao"), v["date"]) and v["tally"] in tallies:
                out.append((o, v["senate_vote_code"], "sequencial"))
            else:
                out.append((o, None, "sequence_match_but_date_or_tally_differs"))
            continue
        cands = [c for c in no_seq if _same_day_or_next(o.get("dataInicioVotacao"), c["date"])
                 and c["tally"] in tallies]
        pending.append((o, cands))
    claims = Counter(cands[0]["senate_vote_code"] for _, cands in pending if len(cands) == 1)
    for o, cands in pending:
        if len(cands) == 1 and claims[cands[0]["senate_vote_code"]] == 1:
            out.append((o, cands[0]["senate_vote_code"], "placar"))
        elif cands:
            out.append((o, None, "ambiguous_tally"))
        elif _is_congress_session(o):
            out.append((o, None, "congress_session"))
        else:
            out.append((o, None, "no_stored_vote"))
    return out


def _crawl_orientations(con: sqlite3.Connection, source_id: int, tmp: Path, now: str, years: list[int],
                        stored: list[dict]) -> dict:
    cast_parties = {r[0] for r in con.execute(
        "SELECT DISTINCT party_abbr FROM senate_vote_cast WHERE party_abbr IS NOT NULL")}
    link_counts: Counter[str] = Counter()     # per panel vote: link basis or unlinked reason
    row_rejects: Counter[str] = Counter()     # per orientation row of a linked panel vote
    unlinked: list[dict] = []
    labels: dict[str, Counter] = defaultdict(Counter)
    values: Counter[str] = Counter()
    unknown_labels: Counter[str] = Counter()
    rows_unlinked = panel_votes = 0
    linked_codes: dict[int, object] = {}
    with step(log, "orientations (monthly windows)"):
        for start, end in _months(years, date.today()):
            url = ORIENTATION_URL.format(start=f"{start:%Y%m%d}", end=f"{end:%Y%m%d}")
            path = tmp / f"orientacao_{start:%Y_%m}.json"
            payload, cid = _collect(con, source_id, url, path,
                                    notes=f"Senado API /plenario/votacao/orientacaoBancada {start:%Y-%m}.")
            if not isinstance(payload, dict):
                raise RuntimeError(f"unexpected orientacaoBancada payload for {start:%Y-%m}")
            panel = payload.get("votacoes") or []
            panel_votes += len(panel)
            parse_id = record_parse(con, collection_id=cid, parser_name=PARSER_NAME,
                                    parser_version=PARSER_VERSION, rows_extracted=0, rows_rejected=0)
            inserted = rejected = 0
            for o, code, basis in link_panel_votes(panel, stored):
                rows = o.get("orientacoesLideranca") or []
                link_counts[basis] += 1
                if code is None:
                    rows_unlinked += len(rows)
                    rejected += len(rows)
                    unlinked.append({"panel_vote_code": o.get("codigoVotacaoSve"),
                                     "sequence": o.get("sequencialVotacao"), "at": o.get("dataInicioVotacao"),
                                     "matter": o.get("descricaoMateria"), "session": o.get("descricaoSessao"),
                                     "orientations": len(rows), "reason": basis})
                    continue
                if code in linked_codes:
                    raise RuntimeError(f"vote {code} linked to panel votes {linked_codes[code]} and "
                                       f"{o.get('codigoVotacaoSve')}")
                linked_codes[code] = o.get("codigoVotacaoSve")
                for x in rows:
                    label = (x.get("partido") or "").strip()
                    if not label:
                        row_rejects["orientation_without_label"] += 1
                        rejected += 1
                        continue
                    kind, norm = normalize_bench(label, cast_parties)
                    raw = x.get("voto") or ""
                    cur = con.execute(
                        "INSERT INTO senate_vote_orientation (senate_vote_code, panel_vote_code, link_basis, "
                        " bench_label, bench_norm, bench_kind, orientation_raw, orientation, oriented_at, "
                        " provenance_id, collected_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT (senate_vote_code, bench_label) DO NOTHING",
                        (code, int(o["codigoVotacaoSve"]), basis, label, norm, kind, raw,
                         normalize_orientation(raw), x.get("dataHora"), parse_id, now),
                    )
                    if cur.rowcount != 1:
                        row_rejects["duplicate_label_in_vote"] += 1
                        rejected += 1
                        continue
                    inserted += 1
                    labels[kind][label] += 1
                    values[raw or "(null)"] += 1
                    if kind == "other" and label not in LEADERSHIP_LABELS:
                        unknown_labels[label] += 1
            _finish_parse(con, parse_id, inserted, rejected)
            stored_rows = con.execute("SELECT count(*) FROM senate_vote_orientation WHERE provenance_id = ?",
                                      (parse_id,)).fetchone()[0]
            if stored_rows != inserted:
                raise RuntimeError(f"{start:%Y-%m}: orientation parse {parse_id} counted {inserted}, "
                                   f"{stored_rows} stored")
            path.unlink(missing_ok=True)
    return {
        "panel_votes_seen": panel_votes,
        "link": dict(link_counts),
        "orientation_rows_rejected": dict(row_rejects),
        "orientation_rows": con.execute("SELECT count(*) FROM senate_vote_orientation").fetchone()[0],
        "orientation_rows_unlinked_not_inserted": rows_unlinked,
        "unlinked_panel_votes": unlinked,
        "labels_by_kind": {k: dict(c.most_common()) for k, c in sorted(labels.items())},
        "unknown_labels": dict(unknown_labels.most_common()),
        "values": dict(values.most_common()),
        "coverage_per_year": _orientation_coverage(con),
    }


def _orientation_coverage(con: sqlite3.Connection) -> dict:
    """Per year: share of stored votes with >= 1 real orientation (ORIENTED_VALUES), and of distinct
    (vote, cast party) pairs whose party has its own real orientation (bench_norm = party_abbr),
    overall and in oriented votes. Pairs of senators without party (NULL, NO_PARTY_ABBRS) are not
    benches: left out of the pairs, counted in pairs_excluded_without_party."""
    marks = ", ".join("?" * len(ORIENTED_VALUES))
    no_party = ", ".join("?" * len(NO_PARTY_ABBRS))
    out = {}
    for y, votes, oriented, pairs, pairs_oriented, matched, excluded in con.execute(
        f"""WITH oriented AS (SELECT DISTINCT senate_vote_code FROM senate_vote_orientation
                             WHERE orientation IN ({marks})),
           pairs AS (
               SELECT DISTINCT c.senate_vote_code, substr(v.date, 1, 4) AS y, c.party_abbr,
                      coalesce(c.party_abbr NOT IN ({no_party}), 0) AS has_party
               FROM senate_vote_cast c JOIN senate_vote v USING (senate_vote_code))
           SELECT y,
                  (SELECT count(*) FROM senate_vote WHERE substr(date, 1, 4) = y),
                  (SELECT count(*) FROM senate_vote WHERE substr(date, 1, 4) = y
                     AND senate_vote_code IN oriented),
                  sum(has_party),
                  sum(has_party AND p.senate_vote_code IN oriented),
                  sum(has_party AND EXISTS (
                      SELECT 1 FROM senate_vote_orientation o
                      WHERE o.senate_vote_code = p.senate_vote_code AND o.bench_norm = p.party_abbr
                        AND o.orientation IN ({marks}))),
                  sum(NOT has_party)
           FROM pairs p GROUP BY y ORDER BY y""",  # noqa: S608
        (*ORIENTED_VALUES, *NO_PARTY_ABBRS, *ORIENTED_VALUES),
    ):
        out[y] = {
            "votes": votes, "votes_with_orientation": oriented,
            "votes_with_orientation_pct": round(100.0 * oriented / votes, 1) if votes else 0.0,
            "vote_party_pairs": pairs, "pairs_excluded_without_party": excluded,
            "pairs_with_party_orientation": matched,
            "pairs_with_party_orientation_pct": round(100.0 * matched / pairs, 1) if pairs else 0.0,
            "pairs_in_oriented_votes": pairs_oriented,
            "pairs_matched_in_oriented_votes_pct":
                round(100.0 * matched / pairs_oriented, 1) if pairs_oriented else 0.0,
        }
    return out


# --------------------------------------------------------------------------- run

def _months(years: list[int], today: date) -> list[tuple[date, date]]:
    out = []
    for y in years:
        for m in range(1, 13):
            start = date(y, m, 1)
            if start > today:
                break
            out.append((start, date(y, m, calendar.monthrange(y, m)[1])))
    return out


def _validate_years(years: list[int] | None) -> list[int]:
    if years is None:
        return list(YEARS)
    if not years:
        raise ValueError("senado-votes: empty years list (omit it for all years)")
    bad = [y for y in years if y not in YEARS]
    if bad:
        raise ValueError(f"senado-votes covers {YEARS[0]}..{YEARS[-1]}; unknown years: {bad}")
    return sorted(set(years))


def _count_check(vote: dict, casts: Counter) -> dict | None:
    """Compare API totals with the stored casts. Totals exist only for secret votes (where casts say
    who voted, not how), so there the check is sum(totals) == 'secret' casts."""
    yes, no, abst = vote["yes_count"], vote["no_count"], vote["abstention_count"]
    if yes is None and no is None and abst is None:
        return None
    if vote["is_secret"]:
        expected = {"secret": (yes or 0) + (no or 0) + (abst or 0)}
    else:
        expected = {k: n for k, n in (("yes", yes), ("no", no), ("abstention", abst)) if n is not None}
    got = {k: casts[k] for k in expected}
    if got == expected:
        return {}
    return {"senate_vote_code": vote["senate_vote_code"], "date": vote["date"],
            "identification": vote["identification"], "api_totals": expected, "casts": got}


def run(con: sqlite3.Connection, *, years: list[int] | None = None,
        tmp_dir: str | Path = "dados_tmp") -> dict:
    """Crawl Senado plenary roll-call votes of `years` (2019..2026, default all) in monthly windows.
    Bad `years` raise before anything is reset; a failure later leaves the tables empty."""
    years = _validate_years(years)
    tmp = Path(tmp_dir) / "senado_votes"
    with step(log, "reset senate_vote / senate_vote_cast / senate_senator"):
        reset_source(con, SOURCE["name"], _OWNED_TABLES)
    try:
        return _crawl(con, years, tmp)
    except BaseException:
        con.rollback()
        raise
    finally:
        for pattern in _TMP_PATTERNS:
            for leftover in tmp.glob(pattern):
                leftover.unlink(missing_ok=True)


def _crawl(con: sqlite3.Connection, years: list[int], tmp: Path) -> dict:
    source_id = get_source(con, **SOURCE)
    people_before = con.execute("SELECT count(*) FROM people").fetchone()[0]
    now = now_utc()

    api_votes = 0
    dropped: Counter[str] = Counter()
    dropped_list: list[dict] = []
    body_audit: Counter[str] = Counter()
    cast_rejected: Counter[str] = Counter()
    raw_codes: Counter[str] = Counter()
    other_codes: Counter[str] = Counter()
    other_code_descriptions: dict[str, set] = defaultdict(set)
    kinds = []
    mismatches: list[dict] = []
    secret_checked = open_without_totals = 0
    sequence: dict[int, list[int]] = defaultdict(list)
    stored_votes: list[dict] = []

    with step(log, f"votes {years[0]}..{years[-1]} (monthly windows)"):
        for start, end in _months(years, date.today()):
            url = VOTES_URL.format(start=start.isoformat(), end=end.isoformat())
            path = tmp / f"votacao_{start:%Y_%m}.json"
            payload, cid = _collect(con, source_id, url, path,
                                    notes=f"Senado API /votacao {start:%Y-%m}.")
            if not isinstance(payload, list):
                raise RuntimeError(f"unexpected /votacao payload for {start:%Y-%m}: {type(payload).__name__}")
            parse_id = record_parse(con, collection_id=cid, parser_name=PARSER_NAME,
                                    parser_version=PARSER_VERSION, rows_extracted=0, rows_rejected=0)
            inserted = rejected = 0
            for v in payload:
                api_votes += 1
                body = (v.get("informeLegislativo") or {}).get("siglaColegiado")
                reason = drop_reason(v)
                body_audit[f"{'kept' if reason is None else 'dropped'}_body_{body or 'NULL'}"] += 1
                if reason is None:
                    vote = {
                        "senate_vote_code": int(v["codigoSessaoVotacao"]),
                        "date": v["dataSessao"][:10],
                        "identification": v.get("identificacao"),
                        "is_secret": 1 if v["votacaoSecreta"] == "S" else 0,
                        "yes_count": _int_or_none(v.get("totalVotosSim")),
                        "no_count": _int_or_none(v.get("totalVotosNao")),
                        "abstention_count": _int_or_none(v.get("totalVotosAbstencao")),
                    }
                    k = _classify(v)
                    cur = con.execute(
                        "INSERT INTO senate_vote (senate_vote_code, session_code, house, body, date, matter_code, "
                        " identification, description, ementa, result_raw, is_secret, yes_count, no_count, "
                        " abstention_count, kind, kind_basis, meaning_code, provenance_id, collected_at, "
                        " informe_text) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT (senate_vote_code) DO NOTHING",
                        (vote["senate_vote_code"], _int_or_none(v.get("codigoSessao")), v.get("casaSessao"), body,
                         vote["date"], _int_or_none(v.get("codigoMateria")), vote["identification"],
                         v.get("descricaoVotacao"), v.get("ementa"), v.get("resultadoVotacao"),
                         vote["is_secret"], vote["yes_count"], vote["no_count"], vote["abstention_count"],
                         k.kind, k.basis, k.meaning_code, parse_id, now,
                         (v.get("informeLegislativo") or {}).get("texto")),
                    )
                    if cur.rowcount != 1:
                        reason = "duplicate_vote_code"
                if reason is not None:
                    dropped[reason] += 1
                    rejected += 1
                    dropped_list.append({"senate_vote_code": v.get("codigoSessaoVotacao"),
                                         "date": v.get("dataSessao"), "session_type": v.get("siglaTipoSessao"),
                                         "identification": v.get("identificacao"), "reason": reason})
                    continue
                inserted += 1
                kinds.append(k)
                if v.get("sequencialVotacao") is not None:
                    sequence[start.year].append(int(v["sequencialVotacao"]))
                stored_votes.append(vote)
                vote["sequence"] = _int_or_none(v.get("sequencialVotacao"))

                casts: Counter[str] = Counter()
                for x in v["votos"]:
                    code, raw = x.get("codigoParlamentar"), x.get("siglaVotoParlamentar")
                    if code is None or not raw:
                        cast_rejected["no_senator_code" if code is None else "no_vote_code"] += 1
                        rejected += 1
                        continue
                    normalized = normalize_cast(raw)
                    cur = con.execute(
                        "INSERT INTO senate_vote_cast (senate_vote_code, senator_code, name, party_abbr, state, "
                        " vote_raw, vote, provenance_id, collected_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT (senate_vote_code, senator_code) DO NOTHING",
                        (vote["senate_vote_code"], int(code), x.get("nomeParlamentar"),
                         x.get("siglaPartidoParlamentar"), x.get("siglaUFParlamentar"), raw, normalized,
                         parse_id, now),
                    )
                    if cur.rowcount != 1:
                        cast_rejected["duplicate_senator_in_vote"] += 1
                        rejected += 1
                        continue
                    inserted += 1
                    casts[normalized] += 1
                    raw_codes[raw] += 1
                    if normalized == "other":
                        other_codes[raw] += 1
                        if x.get("descricaoVotoParlamentar"):
                            other_code_descriptions[raw].add(x["descricaoVotoParlamentar"])

                vote["tally"] = ((vote["yes_count"], vote["no_count"]) if vote["is_secret"]
                                 else (casts["yes"], casts["no"]))
                check = _count_check(vote, casts)
                if check is None:
                    open_without_totals += 1
                else:
                    secret_checked += 1
                    if check:
                        mismatches.append(check)

            _finish_parse(con, parse_id, inserted, rejected)
            stored = con.execute(
                "SELECT (SELECT count(*) FROM senate_vote WHERE provenance_id = :p)"
                " + (SELECT count(*) FROM senate_vote_cast WHERE provenance_id = :p)", {"p": parse_id},
            ).fetchone()[0]
            if stored != inserted:
                raise RuntimeError(f"{start:%Y-%m}: parse {parse_id} counted {inserted} rows, {stored} stored")
            path.unlink(missing_ok=True)

    orientations = _crawl_orientations(con, source_id, tmp, now, years, stored_votes)
    senators = _crawl_senators(con, source_id, tmp, now)

    people_after = con.execute("SELECT count(*) FROM people").fetchone()[0]
    if people_after != people_before:
        raise RuntimeError(f"people changed: {people_before} -> {people_after}")
    con.commit()
    return _report(con, years, api_votes, dropped, dropped_list, body_audit, cast_rejected, raw_codes,
                   other_codes, other_code_descriptions, kinds, mismatches, secret_checked,
                   open_without_totals, sequence, senators, orientations)


def _crawl_senators(con: sqlite3.Connection, source_id: int, tmp: Path, now: str) -> dict:
    codes = [r[0] for r in con.execute("SELECT DISTINCT senator_code FROM senate_vote_cast ORDER BY 1")]
    index = SenatorIndex(con)
    reasons: Counter[str] = Counter()
    unmatched: list[dict] = []
    matched = 0
    rc = RowCounter(log, "senators", every=50)
    with step(log, f"senators ({len(codes)})"):
        for code in codes:
            rc.tick()
            url = SENATOR_URL.format(code=code)
            path = tmp / f"senador_{code}.json"
            payload, cid = _collect(con, source_id, url, path, notes=f"Senado API /senador/{code}.json.")
            p = ((payload or {}).get("DetalheParlamentar") or {}).get("Parlamentar") or {}
            ident = p.get("IdentificacaoParlamentar") or {}
            full_name = (ident.get("NomeCompletoParlamentar") or "").strip() or None
            birth = ((p.get("DadosBasicosParlamentar") or {}).get("DataNascimento") or "").strip()[:10] or None
            if p:
                person_id, basis, reason = index.match(full_name, birth)
            else:
                person_id, basis, reason = None, None, "no_detail"
            parse_id = record_parse(con, collection_id=cid, parser_name=PARSER_NAME,
                                    parser_version=PARSER_VERSION, rows_extracted=1, rows_rejected=0)
            con.execute(
                "INSERT INTO senate_senator (senator_code, parliamentary_name, full_name, birth_date, person_id, "
                " match_basis, unmatched_reason, provenance_id, collected_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (code, ident.get("NomeParlamentar"), full_name, birth, person_id, basis, reason, parse_id, now),
            )
            if basis:
                matched += 1
            else:
                reasons[reason] += 1
                name = con.execute("SELECT name FROM senate_vote_cast WHERE senator_code = ? LIMIT 1",
                                   (code,)).fetchone()[0]
                unmatched.append({"senator_code": code, "name": full_name or name, "birth_date": birth,
                                  "reason": reason})
            path.unlink(missing_ok=True)
        rc.done()
    return {
        "total": len(codes),
        "matched": matched,
        "match_rate_pct": round(100.0 * matched / len(codes), 1) if codes else 0.0,
        "unmatched_by_reason": {r: reasons[r] for r in UNMATCHED_REASONS if reasons[r]},
        "unmatched_list": unmatched,
    }


def _sequence_report(sequence: dict[int, list[int]]) -> dict:
    out = {}
    for year, seqs in sorted(sequence.items()):
        s = sorted(set(seqs))
        out[str(year)] = {"with_sequence": len(s), "min": s[0], "max": s[-1],
                          "numbers_not_in_api": s[-1] - s[0] + 1 - len(s)}
    return out


def _presence_report(con: sqlite3.Connection) -> dict:
    """Votes whose cast list is not the full roster: < 81 rows and no absence row (only voters
    listed, e.g. 5957-5959 in Apr 2019), or > 81 rows (5999: a senator and his substitute)."""
    marks = ", ".join("?" * len(ABSENCE_VOTES))
    rows = con.execute(
        f"SELECT v.senate_vote_code, v.date, count(*), sum(c.vote IN ({marks})) "  # noqa: S608
        "FROM senate_vote v JOIN senate_vote_cast c USING (senate_vote_code) "
        "GROUP BY v.senate_vote_code ORDER BY v.date, v.senate_vote_code", ABSENCE_VOTES,
    ).fetchall()
    return {
        "votes_listing_only_voters": [{"senate_vote_code": r[0], "date": r[1], "casts": r[2]}
                                      for r in rows if r[2] < SENATE_SEATS and not r[3]],
        "votes_with_more_than_81": [{"senate_vote_code": r[0], "date": r[1], "casts": r[2]}
                                    for r in rows if r[2] > SENATE_SEATS],
    }


def _report(con, years, api_votes, dropped, dropped_list, body_audit, cast_rejected, raw_codes, other_codes,
            other_code_descriptions, kinds, mismatches, secret_checked, open_without_totals, sequence,
            senators, orientations) -> dict:
    per_month = {r[0]: r[1] for r in con.execute(
        "SELECT substr(date, 1, 7), count(*) FROM senate_vote GROUP BY 1 ORDER BY 1")}
    per_year = {r[0]: r[1] for r in con.execute(
        "SELECT substr(date, 1, 4), count(*) FROM senate_vote GROUP BY 1 ORDER BY 1")}
    votes = con.execute("SELECT count(*) FROM senate_vote").fetchone()[0]
    casts = con.execute("SELECT count(*) FROM senate_vote_cast").fetchone()[0]
    secret = con.execute("SELECT count(*) FROM senate_vote WHERE is_secret = 1").fetchone()[0]
    by_vote = {r[0]: r[1] for r in con.execute(
        "SELECT vote, count(*) FROM senate_vote_cast GROUP BY 1 ORDER BY 2 DESC")}
    report = {
        "parser_version": PARSER_VERSION,
        "years": years,
        "api_votes_seen": api_votes,
        "votes": votes,
        "casts": casts,
        "votes_per_year": per_year,
        "votes_per_month": per_month,
        "secret_votes": secret,
        "filter": {
            "rule": "casaSessao == 'SF' AND siglaTipoSessao IN " + str(sorted(PLENARY_SESSION_TYPES))
                    + " AND votos not empty",
            "dropped": dict(dropped),
            "dropped_list": dropped_list,
            "by_body": dict(sorted(body_audit.items())),
            "kept_with_body_not_PLEN": sum(n for k, n in body_audit.items()
                                           if k.startswith("kept_") and k != "kept_body_PLEN"),
        },
        "casts_by_vote": by_vote,
        "p_nrv_entries": con.execute(
            "SELECT count(*) FROM senate_vote_cast WHERE vote_raw = 'P-NRV'").fetchone()[0],
        **_presence_report(con),
        "casts_rejected": dict(cast_rejected),
        "vote_codes": dict(raw_codes.most_common()),
        "unknown_vote_codes": {c: {"casts": n, "api_description": sorted(other_code_descriptions[c])}
                               for c, n in other_codes.most_common()},
        "kinds": coverage_report(kinds),
        "count_check": {
            "secret_votes_checked": secret_checked,
            "mismatches": mismatches,
            "open_votes_totals_not_published": open_without_totals,
            "note": "Open votes have totalVotosSim/Nao/Abstencao NULL in the API (stored NULL, never 0); "
                    "their counts come from votos[]. Secret votes: sum(totals) vs 'Votou' casts.",
        },
        "sve_sequence": _sequence_report(sequence),
        "senators": senators,
        "orientations": orientations,
        "notes": [
            "Completeness checked 2026-10-02: /votacao returned the same codigoSessaoVotacao set as the "
            "deprecated /plenario/lista/votacao (monthly, 2019 and 2024) and the static "
            "/dados/ListaVotacoes{ano}.json (every year 2019-2026). Clerk results "
            "/plenario/resultado/mes (independent Ordem do Dia system) matched 2024-07 (14/14), "
            "2024-12 (35/35) and 2025-09 (8/8, same Sim/Não tallies); it has no data for 2019.",
            "Low yearly counts are mostly real: the Senado decides most matters by symbolic vote. "
            "But the panel feed (orientations.unlinked_panel_votes, reason no_stored_vote) lists a "
            "few Senado nominal votes that /votacao does not publish (2019-02-12 requerimento de "
            "retirada de pauta, 2019-04-10 Requerimento 80/2019 verificação, 2022-04-06 Mensagem "
            "86/2021). They are not stored: the panel feed has no senator codes, only names.",
            "sve_sequence.numbers_not_in_api is not a missing-vote count: in 2019 sequencialVotacao is "
            "neither chronological nor contiguous; from 2022 on it is nearly contiguous.",
            "p_nrv_entries: P-NRV is stored as present_not_voted, but the official vote page shows "
            "NCom for 182 of 1,843 P-NRV entries checked (160 votes; worst 2020 remote sessions, "
            "58/116). Display it as 'não registrou voto', never as 'presente'.",
            "votes_listing_only_voters: the cast list names only who voted (no absence codes), so a "
            "senator missing from it is 'desconhecido', not 'ausente'. votes_with_more_than_81: "
            "both a senator and his substitute are listed (5999: Eduardo Gomes NCom, Siqueira "
            "Campos Votou).",
            "party_abbr is the senator's party at vote time under the name /votacao publishes now: "
            "renamed parties show the current sigla (April 2019 votes already say PL, not PR).",
        ],
    }
    log.info("done: %s votes (%s secret), %s casts, %s/%s senators matched, dropped %s",
             votes, secret, casts, senators["matched"], senators["total"], dict(dropped))
    return report
