"""Crawler: Câmara dos Deputados roll-call votes (bulk CSVs) -> camara_vote and children.

Only nominal PLENÁRIO votes are stored: siglaOrgao == 'PLEN' and the id present in votacoesVotos
(~96% of votacoes are symbolic, with no vote rows). Every CSV row ends up counted in the report as
inserted, rejected (bad data, with a reason) or dropped (out of scope by design, with a reason).

Rewrite-only: each run resets this source's tables and collections (that reset is committed), then
writes everything in ONE transaction committed at the end. Any failure (a missing year file, the
/blocos API down) rolls it back: the tables end up empty, never half-filled (votes without
party_bench rows would read as "sem orientação identificada" everywhere).
"""

from __future__ import annotations

import csv
import json
import sqlite3
import time
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from curl_cffi import CurlError, requests

from ..log import get_logger, human_bytes, step
from ..provenance import (
    download,
    get_source,
    record_collection,
    record_file,
    record_parse,
    reset_source,
    sha256_file,
)
from ..util import now_utc
from .benches import (
    API_BLOC_WINDOWS,
    BENCHES_VERSION,
    CURATED_BLOCS,
    Bloc,
    legislature_of,
    map_party_to_bench,
    measure_coverage,
    normalize_bench,
    parse_blocs,
)
from .vote_kind import CLASSIFIER_VERSION, classify_camara, coverage_report

log = get_logger("elosys.camara.votes")

PARSER_NAME = "camara.votes"
PARSER_VERSION = f"1.0+kind{CLASSIFIER_VERSION}"
PARTY_BENCH_PARSER_NAME = "camara.votes.party_bench"
PARTY_BENCH_PARSER_VERSION = f"{PARSER_VERSION}+benches{BENCHES_VERSION}"
# Last day any cited bloc composition covers: later votes can only be bloc_unresolved for bloc rows.
LAST_WINDOW_END = max(w.end_date for w in (*CURATED_BLOCS, *API_BLOC_WINDOWS.values()))
_BLOC_BASES = ("federation", "bloc_api", "bloc_curated", "bloc_label")

FILE_URL = "https://dadosabertos.camara.leg.br/arquivos/{dataset}/csv/{dataset}-{year}.csv"
API_URL = "https://dadosabertos.camara.leg.br/api/v2"
SUPPORTED_YEARS = tuple(range(2019, 2027))

_LEGAL_BASIS = (
    "Lei 12.527/2011 (Lei de Acesso a Informacao) e CF art. 37 (publicidade): dados abertos "
    "publicados pela propria Camara dos Deputados."
)
SOURCE = dict(
    name="Camara dos Deputados - Votacoes (arquivos)",
    agency="Camara dos Deputados",
    type="csv",
    base_url="https://dadosabertos.camara.leg.br/arquivos/",
    legal_basis=_LEGAL_BASIS,
    notes=(
        "Arquivos anuais votacoes, votacoesVotos, votacoesOrientacoes, votacoesProposicoes, "
        "eventosPresencaDeputados e proposicoesTemas. Arquivos regerados diariamente: o sha256 muda "
        "a cada coleta mesmo sem dados novos. Somente votacoes nominais do Plenario (PLEN)."
    ),
)
# Its own source row (source.name is unique), wiped by the same reset as SOURCE.
API_SOURCE = dict(
    name="Camara dos Deputados - API Blocos",
    agency="Camara dos Deputados",
    type="api",
    base_url=f"{API_URL}/blocos",
    legal_basis=_LEGAL_BASIS,
    notes=(
        "/blocos?idLegislatura= e /blocos/{id}/partidos: composicao de blocos e federacoes, usada "
        "so para ligar partido -> bancada que orientou (camara_vote_party_bench.match_evidence)."
    ),
)

csv.field_size_limit(1 << 24)

_OWNED_TABLES = [
    "camara_vote_party_bench", "camara_vote_orientation", "camara_vote_cast", "camara_session_presence",
    "camara_proposition_theme", "camara_vote_proposition", "camara_vote",
]

# Columns this parser reads, checked against each file header before parsing.
_REQUIRED = {
    "votacoes": (
        "id", "data", "dataHoraRegistro", "siglaOrgao", "idEvento", "aprovacao", "votosSim", "votosNao",
        "votosOutros", "descricao", "ultimaAberturaVotacao_descricao", "ultimaApresentacaoProposicao_descricao",
        "ultimaApresentacaoProposicao_idProposicao",
    ),
    "votacoesVotos": (
        "idVotacao", "voto", "deputado_id", "deputado_nome", "deputado_siglaPartido", "deputado_siglaUf",
        "deputado_idLegislatura",
    ),
    "votacoesOrientacoes": ("idVotacao", "siglaBancada", "orientacao"),
    "votacoesProposicoes": (
        "idVotacao", "proposicao_id", "proposicao_titulo", "proposicao_ementa", "proposicao_siglaTipo",
        "proposicao_numero", "proposicao_ano",
    ),
    "eventosPresencaDeputados": ("idEvento", "idDeputado"),
    "proposicoesTemas": ("uriProposicao", "codTema", "tema"),
}

# Empty voto = secret ballot: the file says who voted, not how. Never read it as Não/Abstenção.
# "Artigo 17" = the deputy presiding the session (RICD art. 17), not an absence or abstention.
_VOTE = {
    "Sim": "yes", "Não": "no", "Abstenção": "abstention", "Obstrução": "obstruction",
    "Artigo 17": "presiding", "": "secret",
}
_ORIENTATION = {"Sim": "yes", "Não": "no", "Obstrução": "obstruction", "Liberado": "free", "": "none"}

_API_DELAY = 0.35  # ~3 req/s
_API_RETRIES = 4
_API_DEFAULT_RETRY_AFTER = 30


@dataclass
class _Tally:
    rows: int = 0
    inserted: int = 0
    rejected: Counter = field(default_factory=Counter)
    dropped: Counter = field(default_factory=Counter)

    def as_dict(self) -> dict:
        return {"rows": self.rows, "inserted": self.inserted,
                "rejected": dict(self.rejected), "dropped": dict(self.dropped)}

    def check(self, what: str) -> None:
        accounted = self.inserted + sum(self.rejected.values()) + sum(self.dropped.values())
        if accounted != self.rows:
            raise RuntimeError(f"{what}: {self.rows} rows read but {accounted} accounted for")


@dataclass
class _File:
    path: Path
    collection_id: int
    file_id: int
    parse_id: int
    keep: bool


def _int(v: str | None) -> int | None:
    v = (v or "").strip()
    try:
        return int(v)
    except ValueError:
        return None


def _text(v: str | None) -> str | None:
    return v if v not in (None, "") else None


def _iso_or_none(v: str) -> str | None:
    try:
        return date.fromisoformat(v.strip()[:10]).isoformat()
    except ValueError:
        return None


# --- I/O ----------------------------------------------------------------------------------


def _fetch_json(url: str, dest: Path) -> tuple[int, str | None]:
    """GET a Câmara API URL as JSON, politely (~3 req/s). 429 (honours Retry-After), 5xx and network
    errors are retried; any other non-200 status raises at once."""
    last: Exception | str | None = None
    for attempt in range(_API_RETRIES):
        final = attempt == _API_RETRIES - 1
        time.sleep(_API_DELAY)
        try:
            r = requests.get(url, headers={"Accept": "application/json"}, impersonate="chrome", timeout=60)
        except (CurlError, OSError) as e:
            last = e
            if not final:
                time.sleep(5 * (2 ** attempt))
            continue
        status = r.status_code
        if status == 429 or status >= 500:
            last = f"HTTP {status}"
            if not final:
                time.sleep(_int(r.headers.get("Retry-After")) or (
                    _API_DEFAULT_RETRY_AFTER if status == 429 else 5 * (2 ** attempt)))
            continue
        if status != 200:
            raise RuntimeError(f"HTTP {status} for {url}")
        dest.write_bytes(r.content)
        return status, r.headers.get("Content-Type")
    raise RuntimeError(f"failed to fetch {url}: {last}")


def _open_file(ctx: _Ctx, dataset: str, year: int) -> _File:
    con = ctx.con
    url = FILE_URL.format(dataset=dataset, year=year)
    path = ctx.tmp_dir / f"{dataset}-{year}.csv"
    if path.exists():
        status, ctype, keep = None, "text/csv", True
        notes = "Arquivo fornecido localmente; a URL e a canonica."
        log.info("  using local file %s", path)
    else:
        log.info("  download %s", url)
        # Never at the final path until complete: a leftover there would be read as user-supplied.
        part = path.with_name(path.name + ".part")
        try:
            status, ctype = download(url, part)
            part.replace(path)
        finally:
            part.unlink(missing_ok=True)
        ctx.downloaded.append(path)
        keep = False
        notes = "Arquivo regerado diariamente pela Camara (sha256 muda a cada coleta)."
    collection_id, is_new = record_collection(
        con, source_id=ctx.source_id, url=url, file=path, http_status=status, content_type=ctype, notes=notes)
    sha, size = sha256_file(path)
    file_id = record_file(con, collection_id=collection_id, filename=path.name, sha256=sha, size=size)
    parse_id = record_parse(con, collection_id=collection_id, collection_file_id=file_id,
                            parser_name=PARSER_NAME, parser_version=PARSER_VERSION,
                            rows_extracted=0, rows_rejected=0)
    log.info("  %s  collection %d  (%s)", "new" if is_new else "already recorded", collection_id,
             human_bytes(size))
    return _File(path, collection_id, file_id, parse_id, keep)


def _close_file(f: _File) -> None:
    if not f.keep:
        f.path.unlink(missing_ok=True)


def _rows(path: Path, dataset: str, tally: _Tally) -> Iterator[dict[str, str]]:
    """Rows by header name. Rows with a wrong field count are rejected as 'malformed_row'."""
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh, delimiter=";")
        header = reader.fieldnames or []
        missing = [c for c in _REQUIRED[dataset] if c not in header]
        if missing:
            raise ValueError(f"{path.name}: unexpected header, missing columns {missing}; got {header}")
        for row in reader:
            tally.rows += 1
            if None in row or any(v is None for v in row.values()):
                tally.rejected["malformed_row"] += 1
                continue
            yield row


def _insert(con: sqlite3.Connection, table: str, conflict: Sequence[str], rows: list[dict],
            parse_id: int, tally: _Tally) -> int:
    """INSERT ... ON CONFLICT DO NOTHING (never OR IGNORE: it would hide CHECK/NOT NULL violations)."""
    if rows:
        cols = list(rows[0])
        con.executemany(
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(':' + c for c in cols)}) "  # noqa: S608
            f"ON CONFLICT ({', '.join(conflict)}) DO NOTHING", rows)
    inserted = con.execute(f"SELECT count(*) FROM {table} WHERE provenance_id = ?",  # noqa: S608
                           (parse_id,)).fetchone()[0]
    if len(rows) > inserted:
        # Rows deduplicated within the file can only collide with another year's file.
        tally.dropped["already_stored_from_other_file"] += len(rows) - inserted
    tally.inserted += inserted
    con.execute("UPDATE parse SET rows_extracted = ?, rows_rejected = ? WHERE id = ?",
                (inserted, sum(tally.rejected.values()), parse_id))
    return inserted


# --- run ----------------------------------------------------------------------------------


@dataclass
class _Ctx:
    con: sqlite3.Connection
    source_id: int
    tmp_dir: Path
    stored_events: set[int] = field(default_factory=set)
    vote_year: dict[str, int] = field(default_factory=dict)
    orientation_file: dict[int, _File] = field(default_factory=dict)
    kinds: list = field(default_factory=list)
    mixed_secret: list[str] = field(default_factory=list)
    ambiguous_type: list[str] = field(default_factory=list)
    conflicting_orientations: list[str] = field(default_factory=list)
    downloaded: list[Path] = field(default_factory=list)


def run(con: sqlite3.Connection, *, years: list[int] | None = None, tmp_dir: str | Path = "dados_tmp") -> dict:
    years = years or list(SUPPORTED_YEARS)
    unknown = [y for y in years if y not in SUPPORTED_YEARS]
    if unknown:
        raise ValueError(f"camara-votes supports {SUPPORTED_YEARS[0]}..{SUPPORTED_YEARS[-1]}, not {unknown}")
    tmp = Path(tmp_dir)
    tmp.mkdir(parents=True, exist_ok=True)

    log.info("rewrite-only: camara_vote* will contain exactly these years: %s", ", ".join(map(str, years)))
    with step(log, "reset camara votes"):
        reset_source(con, SOURCE["name"], _OWNED_TABLES)
        reset_source(con, API_SOURCE["name"], [])

    ctx = _Ctx(con, 0, tmp)
    try:
        report = _crawl(ctx, years)
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        for path in ctx.downloaded:
            path.unlink(missing_ok=True)
    log.info("done: %s votacoes nominais PLEN, %s votos, count check %s divergencias (%s secretas fora)",
             f"{report['tables']['camara_vote']:,}", f"{report['tables']['camara_vote_cast']:,}",
             len(report["count_check"]["mismatches"]), report["secret_votes"])
    return report


def _crawl(ctx: _Ctx, years: list[int]) -> dict:
    con = ctx.con
    ctx.source_id = get_source(con, **SOURCE)
    report: dict = {"parser_version": PARSER_VERSION, "years": {}}
    for year in years:
        with step(log, f"camara votacoes {year}"):
            report["years"][year] = _ingest_year(ctx, year)

    with step(log, "blocos (API) + bancada de cada partido"):
        blocs, report["blocs"] = _fetch_blocs(ctx)
        report["bench"] = _fill_party_bench(ctx, blocs)

    with step(log, "temas das proposicoes"):
        report["themes"] = _ingest_themes(ctx)

    report["tables"] = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]  # noqa: S608
                        for t in reversed(_OWNED_TABLES)}
    secret_ids = [r[0] for r in con.execute(
        "SELECT camara_vote_id FROM camara_vote WHERE is_secret = 1 ORDER BY camara_vote_id")]
    report["secret_votes"] = len(secret_ids)
    report["secret_vote_ids"] = secret_ids
    report["mixed_secret_vote_ids"] = ctx.mixed_secret
    report["ambiguous_proposition_type_vote_ids"] = ctx.ambiguous_type
    report["conflicting_orientation_ids"] = ctx.conflicting_orientations
    report["votes_after_last_window_end"] = _votes_after_last_window_end(con)
    report["count_check"] = count_check(con)
    report["presence_check"] = _presence_check(con)
    report["kind"] = coverage_report(ctx.kinds)
    report["theme_coverage_pct"] = _theme_coverage(con)
    return report


def _votes_after_last_window_end(con: sqlite3.Connection) -> dict:
    """Votes dated after every cited bloc window: their bloc rows cannot resolve until benches.py
    gets newer windows (re-crawl checklist)."""
    ids = [r[0] for r in con.execute(
        "SELECT camara_vote_id FROM camara_vote WHERE date > ? ORDER BY date, camara_vote_id", (LAST_WINDOW_END,))]
    if ids:
        log.warning("%d votes after %s, the last day of any cited bloc window: extend benches.py "
                    "(CURATED_BLOCS / API_BLOC_WINDOWS), e.g. %s", len(ids), LAST_WINDOW_END, ids[:5])
    return {"last_window_end": LAST_WINDOW_END, "count": len(ids), "ids": ids}


def _ingest_year(ctx: _Ctx, year: int) -> dict:
    con = ctx.con
    files: dict[str, _Tally] = {}

    # votacoes: keep PLEN rows in memory until votacoesVotos says which are nominal.
    t = files["votacoes"] = _Tally()
    fv = _open_file(ctx, "votacoes", year)
    all_ids: set[str] = set()
    plen: dict[str, dict] = {}
    plen_rejected: set[str] = set()
    for row in _rows(fv.path, "votacoes", t):
        vid = row["id"].strip()
        if not vid:
            t.rejected["missing_id"] += 1
            continue
        if vid in all_ids:
            t.rejected["duplicate"] += 1
            continue
        all_ids.add(vid)
        if row["siglaOrgao"].strip() != "PLEN":
            t.dropped["not_plen"] += 1
            continue
        vote, reason = _vote_row(row, fv.parse_id)
        if reason:
            t.rejected[reason] += 1
            plen_rejected.add(vid)
            continue
        plen[vid] = vote
    _close_file(fv)

    tc = files["votacoesVotos"] = _Tally()
    fc = _open_file(ctx, "votacoesVotos", year)
    casts: list[dict] = []
    seen: set[tuple[str, int]] = set()
    nominal: set[str] = set()
    for row in _rows(fc.path, "votacoesVotos", tc):
        vid = row["idVotacao"].strip()
        if vid in plen_rejected:
            tc.dropped["vote_rejected"] += 1
            continue
        if vid not in plen:
            tc.dropped["not_plen" if vid in all_ids else "vote_not_in_votacoes"] += 1
            continue
        nominal.add(vid)
        cast, reason = _cast_row(row, plen[vid], fc.parse_id)
        if reason:
            tc.rejected[reason] += 1
            continue
        key = (vid, cast["camara_deputy_id"])
        if key in seen:
            tc.rejected["duplicate"] += 1
            continue
        seen.add(key)
        casts.append(cast)

    t.dropped["symbolic"] += len(plen) - len(nominal)
    vote_rows = [v for vid, v in plen.items() if vid in nominal]
    stored = {v["camara_vote_id"]: v for v in vote_rows}
    files["votacoesProposicoes"], prop_types = _ingest_propositions(ctx, year, stored)
    by_vote: dict[str, list[str]] = {}
    for c in casts:
        by_vote.setdefault(c["camara_vote_id"], []).append(c["vote"])
    results = {}
    mixed, ambiguous = set(), set()
    for v in vote_rows:
        kinds = by_vote.get(v["camara_vote_id"], [])
        if "secret" in kinds:
            v["is_secret"] = 1
            if any(k != "secret" for k in kinds):
                mixed.add(v["camara_vote_id"])
        # Only an unambiguous type helps the classifier: several types (e.g. PEC + PL) -> None.
        types = prop_types.get(v["camara_vote_id"], set())
        if len(types) > 1:
            ambiguous.add(v["camara_vote_id"])
        result = classify_camara(v["description"], v["opening_description"],
                                 yes_count=v["yes_count"], no_count=v["no_count"],
                                 proposition_type=next(iter(types)) if len(types) == 1 else None)
        v["kind"], v["meaning_code"], v["kind_basis"] = result.kind, result.meaning_code, result.basis
        results[v["camara_vote_id"]] = result
    _insert(con, "camara_vote", ("camara_vote_id",), vote_rows, fv.parse_id, t)
    _insert(con, "camara_vote_cast", ("camara_vote_id", "camara_deputy_id"), casts, fc.parse_id, tc)
    _close_file(fc)
    # A vote id repeated in another year's file is kept from the first file only: count it once.
    mine = [r[0] for r in con.execute(
        "SELECT camara_vote_id FROM camara_vote WHERE provenance_id = ? ORDER BY id", (fv.parse_id,))]
    for vid in mine:
        ctx.vote_year[vid] = year
        ctx.kinds.append(results[vid])
    ctx.mixed_secret.extend(vid for vid in mine if vid in mixed)
    ctx.ambiguous_type.extend(vid for vid in mine if vid in ambiguous)
    ctx.stored_events.update(v["event_id"] for v in vote_rows if v["event_id"] is not None)

    files["votacoesOrientacoes"] = _ingest_orientations(ctx, year, stored)
    files["eventosPresencaDeputados"] = _ingest_presence(ctx, year)

    for name, tally in files.items():
        tally.check(f"{name}-{year}")
    log.info("  %s votacoes nominais PLEN em %d", f"{len(stored):,}", year)
    return {"plen_nominal_votes": len(stored), "files": {k: v.as_dict() for k, v in files.items()}}


def _vote_row(row: dict[str, str], parse_id: int) -> tuple[dict, str | None]:
    iso = _iso_or_none(row["data"])
    if not iso:
        return {}, "invalid_date"
    aprov = row["aprovacao"].strip()
    if aprov not in ("0", "1", ""):
        return {}, "invalid_aprovacao"
    last_filed = _int(row["ultimaApresentacaoProposicao_idProposicao"])
    return {
        "camara_vote_id": row["id"].strip(),
        "event_id": _int(row["idEvento"]),
        "date": iso,
        "registered_at": _text(row["dataHoraRegistro"]),  # Brasília local time, verbatim
        "legislature": legislature_of(iso),
        "approved": int(aprov) if aprov else None,
        "yes_count": _int(row["votosSim"]),
        "no_count": _int(row["votosNao"]),
        "other_count": _int(row["votosOutros"]),
        "description": _text(row["descricao"]),
        "opening_description": _text(row["ultimaAberturaVotacao_descricao"]),
        # NOT the item voted: the last document filed in the proposition before the vote ('0' = none).
        "last_filed_proposition_id": last_filed if last_filed else None,
        "last_filed_description": _text(row["ultimaApresentacaoProposicao_descricao"]),
        "is_secret": 0,
        "kind": None, "kind_basis": None, "meaning_code": None,
        "provenance_id": parse_id,
        "collected_at": now_utc(),
    }, None


def _cast_row(row: dict[str, str], vote: dict, parse_id: int) -> tuple[dict, str | None]:
    dep = _int(row["deputado_id"])
    if dep is None:
        return {}, "invalid_deputy_id"
    # Fires on 0 real PLEN rows 2019-2026: a nonzero count means the source changed or
    # legislature_of broke (the re-stamped 1548586-161 rows are a CVT vote, dropped earlier).
    leg = _int(row["deputado_idLegislatura"])
    if leg is None:
        return {}, "invalid_legislature"
    if leg != vote["legislature"]:
        return {}, "legislature_mismatch"
    raw = row["voto"]
    norm = _VOTE.get(raw.strip())
    if norm is None:
        return {}, "unknown_vote_value"
    return {
        "camara_vote_id": vote["camara_vote_id"],
        "camara_deputy_id": dep,
        "name": _text(row["deputado_nome"]),
        "party_abbr": _text(row["deputado_siglaPartido"].strip()),
        "state": _text(row["deputado_siglaUf"].strip()),
        "vote_raw": raw,
        "vote": norm,
        "provenance_id": parse_id,
        "collected_at": now_utc(),
    }, None


def _ingest_orientations(ctx: _Ctx, year: int, stored: dict[str, dict]) -> _Tally:
    t = _Tally()
    f = _open_file(ctx, "votacoesOrientacoes", year)
    rows: list[dict] = []
    seen: dict[tuple[str, str], str] = {}
    now = now_utc()
    for row in _rows(f.path, "votacoesOrientacoes", t):
        vid = row["idVotacao"].strip()
        if vid not in stored:
            t.dropped["vote_not_stored"] += 1
            continue
        label = row["siglaBancada"].strip()
        if not label:
            t.rejected["empty_bench_label"] += 1
            continue
        raw = row["orientacao"]
        key = (vid, label)
        if key in seen:
            # Same bench twice in one vote: keep the first; a different value is a conflict.
            if seen[key] == raw:
                t.rejected["duplicate"] += 1
            else:
                t.rejected["conflicting_duplicate"] += 1
                ctx.conflicting_orientations.append(f"{vid} {label}")
            continue
        seen[key] = raw
        kind, norm = normalize_bench(label)
        rows.append({
            "camara_vote_id": vid, "bench_label": label, "bench_kind": kind, "bench_norm": norm or None,
            "orientation_raw": raw, "orientation": _ORIENTATION.get(raw.strip(), "other"),
            "provenance_id": f.parse_id, "collected_at": now,
        })
    _insert(ctx.con, "camara_vote_orientation", ("camara_vote_id", "bench_label"), rows, f.parse_id, t)
    ctx.orientation_file[year] = f
    _close_file(f)
    return t


def _ingest_propositions(ctx: _Ctx, year: int, stored: dict[str, dict]) -> tuple[_Tally, dict[str, set[str]]]:
    """-> tally, and the distinct siglaTipo of each vote's propositions (for the classifier)."""
    t = _Tally()
    types: dict[str, set[str]] = {}
    f = _open_file(ctx, "votacoesProposicoes", year)
    rows: list[dict] = []
    seen: set[tuple[str, int]] = set()
    now = now_utc()
    for row in _rows(f.path, "votacoesProposicoes", t):
        vid = row["idVotacao"].strip()
        if vid not in stored:
            t.dropped["vote_not_stored"] += 1
            continue
        pid = _int(row["proposicao_id"])
        if pid is None:
            t.rejected["invalid_proposition_id"] += 1
            continue
        if (vid, pid) in seen:
            t.rejected["duplicate"] += 1
            continue
        seen.add((vid, pid))
        if row["proposicao_siglaTipo"].strip():
            types.setdefault(vid, set()).add(row["proposicao_siglaTipo"].strip())
        rows.append({
            "camara_vote_id": vid, "proposition_id": pid,
            "type_abbr": _text(row["proposicao_siglaTipo"].strip()),
            "number": _int(row["proposicao_numero"]), "year": _int(row["proposicao_ano"]),
            "title": _text(row["proposicao_titulo"]), "ementa": _text(row["proposicao_ementa"]),
            "provenance_id": f.parse_id, "collected_at": now,
        })
    _insert(ctx.con, "camara_vote_proposition", ("camara_vote_id", "proposition_id"), rows, f.parse_id, t)
    _close_file(f)
    return t, types


def _ingest_presence(ctx: _Ctx, year: int) -> _Tally:
    t = _Tally()
    f = _open_file(ctx, "eventosPresencaDeputados", year)
    rows: list[dict] = []
    seen: set[tuple[int, int]] = set()
    now = now_utc()
    for row in _rows(f.path, "eventosPresencaDeputados", t):
        ev, dep = _int(row["idEvento"]), _int(row["idDeputado"])
        if ev is None or dep is None:
            t.rejected["invalid_id"] += 1
            continue
        if ev not in ctx.stored_events:
            t.dropped["event_not_stored"] += 1
            continue
        if (ev, dep) in seen:
            t.rejected["duplicate"] += 1
            continue
        seen.add((ev, dep))
        rows.append({"event_id": ev, "camara_deputy_id": dep, "provenance_id": f.parse_id, "collected_at": now})
    _insert(ctx.con, "camara_session_presence", ("event_id", "camara_deputy_id"), rows, f.parse_id, t)
    _close_file(f)
    return t


# --- blocs and party -> bench -------------------------------------------------------------


def _api_get(ctx: _Ctx, source_id: int, url: str, name: str) -> object:
    path = ctx.tmp_dir / name
    status, ctype = _fetch_json(url, path)
    try:
        record_collection(ctx.con, source_id=source_id, url=url, file=path, http_status=status,
                          content_type=ctype, notes="API de dados abertos da Camara (JSON).")
        return json.loads(path.read_bytes())
    finally:
        path.unlink(missing_ok=True)


def _fetch_blocs(ctx: _Ctx) -> tuple[list[Bloc], dict]:
    """/blocos + /blocos/{id}/partidos for each legislature of the stored votes (one collection each)."""
    source_id = get_source(ctx.con, **API_SOURCE)
    legs = [r[0] for r in ctx.con.execute("SELECT DISTINCT legislature FROM camara_vote ORDER BY 1")]
    blocs: list[Bloc] = []
    calls = 0
    for leg in legs:
        listing = _api_get(ctx, source_id, f"{API_URL}/blocos?idLegislatura={leg}&itens=100", f"blocos-{leg}.json")
        calls += 1
        links = {lk.get("rel"): lk.get("href") for lk in (listing.get("links") or [])}  # type: ignore[union-attr]
        if links.get("next"):
            raise RuntimeError(f"/blocos?idLegislatura={leg} has more than one page; paginate before trusting it")
        parties: dict[str, object] = {}
        for item in listing.get("dados") or []:  # type: ignore[union-attr]
            bid = str(item.get("id"))
            parties[bid] = _api_get(ctx, source_id, f"{API_URL}/blocos/{bid}/partidos", f"bloco-{bid}.json")
            calls += 1
        blocs.extend(parse_blocs(listing, parties))
    return blocs, {"legislatures": legs, "api_calls": calls, "blocs": len(blocs),
                   "federations": sum(b.is_federation for b in blocs)}


def _fill_party_bench(ctx: _Ctx, blocs: list[Bloc]) -> dict:
    con = ctx.con
    labels: dict[str, list[str]] = {}
    for vid, label in con.execute("SELECT camara_vote_id, bench_label FROM camara_vote_orientation ORDER BY id"):
        labels.setdefault(vid, []).append(label)
    dates = dict(con.execute("SELECT camara_vote_id, date FROM camara_vote"))
    no_party = con.execute("SELECT count(*) FROM camara_vote_cast WHERE party_abbr IS NULL").fetchone()[0]

    parse_ids: dict[int, int] = {}
    for year, f in ctx.orientation_file.items():
        parse_ids[year] = record_parse(con, collection_id=f.collection_id, collection_file_id=f.file_id,
                                       parser_name=PARTY_BENCH_PARSER_NAME, parser_version=PARTY_BENCH_PARSER_VERSION,
                                       rows_extracted=0, rows_rejected=0)
    rows: dict[int, list[dict]] = {y: [] for y in parse_ids}
    results = []
    evidence_kind: Counter = Counter()
    now = now_utc()
    for vid, party in con.execute(
            "SELECT DISTINCT camara_vote_id, party_abbr FROM camara_vote_cast WHERE party_abbr IS NOT NULL "
            "ORDER BY camara_vote_id, party_abbr"):
        iso = dates[vid]
        res = map_party_to_bench(party, labels.get(vid, []), blocs, iso)
        label, basis, reason = res.bench_label, res.match_basis, res.unmatched_reason
        evidence = getattr(res, "match_evidence", None)
        if basis in _BLOC_BASES:
            if not evidence:
                raise RuntimeError(f"{vid} {party}: {basis} match without evidence")
            # bloc_label rows and federations absent from /blocos are evidenced by the label itself.
            evidence_kind[f"{basis}:{'url' if evidence.startswith('http') else 'label'}"] += 1
        results.append((label, basis, reason))
        year = ctx.vote_year[vid]
        rows[year].append({
            "camara_vote_id": vid, "party_abbr": party, "bench_label": label, "match_basis": basis,
            "match_evidence": evidence if basis != "party" else None, "unmatched_reason": reason,
            "provenance_id": parse_ids[year], "collected_at": now,
        })
    for year, year_rows in rows.items():
        _insert(con, "camara_vote_party_bench", ("camara_vote_id", "party_abbr"), year_rows,
                parse_ids[year], _Tally())
    out = measure_coverage(results)
    out["evidence"] = dict(evidence_kind)
    out["casts_without_party"] = no_party
    return out


# --- themes -------------------------------------------------------------------------------


def _ingest_themes(ctx: _Ctx) -> dict:
    con = ctx.con
    props = {r[0] for r in con.execute("SELECT DISTINCT proposition_id FROM camara_vote_proposition")}
    years = [r[0] for r in con.execute(
        "SELECT DISTINCT year FROM camara_vote_proposition WHERE year IS NOT NULL ORDER BY year")]
    no_year = con.execute("SELECT count(DISTINCT proposition_id) FROM camara_vote_proposition "
                          "WHERE year IS NULL").fetchone()[0]
    files: dict[int, dict] = {}
    for year in years:
        t = _Tally()
        f = _open_file(ctx, "proposicoesTemas", year)
        rows: list[dict] = []
        seen: set[tuple[int, int]] = set()
        now = now_utc()
        for row in _rows(f.path, "proposicoesTemas", t):
            pid = _int(row["uriProposicao"].rstrip("/").rsplit("/", 1)[-1])
            code = _int(row["codTema"])
            if pid is None or code is None:
                t.rejected["invalid_id"] += 1
                continue
            if pid not in props:
                t.dropped["proposition_not_stored"] += 1
                continue
            if (pid, code) in seen:
                t.rejected["duplicate"] += 1
                continue
            seen.add((pid, code))
            rows.append({"proposition_id": pid, "theme_code": code, "theme": _text(row["tema"]),
                         "provenance_id": f.parse_id, "collected_at": now})
        _insert(con, "camara_proposition_theme", ("proposition_id", "theme_code"), rows, f.parse_id, t)
        _close_file(f)
        t.check(f"proposicoesTemas-{year}")
        files[year] = t.as_dict()
    return {"years": years, "propositions_without_year": no_year, "files": files}


# --- checks -------------------------------------------------------------------------------


def count_check(con: sqlite3.Connection) -> dict:
    """Stored Sim/Não casts vs votosSim/votosNao per non-secret vote; mismatches listed by id."""
    rows = con.execute(
        "SELECT v.camara_vote_id, v.yes_count, v.no_count, "
        "       coalesce(sum(c.vote = 'yes'), 0) AS yes, coalesce(sum(c.vote = 'no'), 0) AS no "
        "FROM camara_vote v LEFT JOIN camara_vote_cast c ON c.camara_vote_id = v.camara_vote_id "
        "WHERE v.is_secret = 0 GROUP BY v.camara_vote_id ORDER BY v.camara_vote_id").fetchall()
    mismatches = [
        {"camara_vote_id": r["camara_vote_id"], "yes": [r["yes_count"], r["yes"]], "no": [r["no_count"], r["no"]]}
        for r in rows if r["yes_count"] != r["yes"] or r["no_count"] != r["no"]
    ]
    return {"checked": len(rows), "mismatches": mismatches, "ok": not mismatches}


def _presence_check(con: sqlite3.Connection) -> dict:
    """Absence is only inferable if every voter is in the session's presence list (100% in 2026)."""
    no_presence = con.execute(
        "SELECT count(*) FROM camara_vote v WHERE v.event_id IS NULL OR NOT EXISTS "
        "(SELECT 1 FROM camara_session_presence p WHERE p.event_id = v.event_id)").fetchone()[0]
    casts_missing = con.execute(
        "SELECT count(*) FROM camara_vote_cast c JOIN camara_vote v ON v.camara_vote_id = c.camara_vote_id "
        "WHERE NOT EXISTS (SELECT 1 FROM camara_session_presence p "
        "                  WHERE p.event_id = v.event_id AND p.camara_deputy_id = c.camara_deputy_id)").fetchone()[0]
    return {"votes_without_presence_list": no_presence, "casts_not_in_presence_list": casts_missing}


def _theme_coverage(con: sqlite3.Connection) -> float:
    total = con.execute("SELECT count(*) FROM camara_vote").fetchone()[0]
    themed = con.execute(
        "SELECT count(DISTINCT p.camara_vote_id) FROM camara_vote_proposition p "
        "JOIN camara_proposition_theme t ON t.proposition_id = p.proposition_id").fetchone()[0]
    return round(100 * themed / total, 1) if total else 0.0
