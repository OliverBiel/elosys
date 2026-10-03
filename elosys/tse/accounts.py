"""Crawler: TSE campaign finance (prestação de contas eleitorais) -> campaign_org,"""

from __future__ import annotations

import csv
import io
import sqlite3
import zipfile
from pathlib import Path

from ..identity import resolve_person
from ..log import RowCounter, get_logger, human_bytes, step
from ..provenance import (
    download,
    get_source,
    record_collection,
    record_file,
    record_parse,
    reset_source,
    sha256_bytes,
)
from ..util import brl_to_cents, clean_tse, digits_only, iso_date, normalize_name, now_utc

log = get_logger("elosys.tse.accounts")

PARSER_NAME = "tse.accounts"
PARSER_VERSION = "4.0"

URL_TEMPLATE = (
    "https://cdn.tse.jus.br/estatistica/sead/odsele/prestacao_contas/"
    "prestacao_de_contas_eleitorais_candidatos_{year}.zip"
)
SUPPORTED_YEARS = (2018, 2020, 2022, 2024, 2026)

SOURCE = dict(
    name="TSE - prestacao_contas",
    agency="Tribunal Superior Eleitoral",
    type="csv",
    base_url="https://cdn.tse.jus.br/estatistica/sead/odsele/prestacao_contas/",
    legal_basis=(
        "Brazilian electoral open data. Campaign finance reporting, including every "
        "donation received, is public by law (Lei 9.504/1997 arts. 28-32; TSE "
        "open-data resolutions). The campaign CNPJ is a public registry identifier "
        "(Receita natureza jurídica 409-4)."
    ),
    notes="Donor/supplier home address and personal contact are not published here and are "
          "not ingested; only name, CPF/CNPJ, state/municipality and the transaction itself.",
)

csv.field_size_limit(1 << 24)

# Children before parents: reset_source empties these in order with foreign_keys=ON (only the
# source's parse/collection rows go with FKs off, after a check that no other table uses them).
_OWNED_TABLES = ["campaign_donation", "campaign_expense_payment", "campaign_expense", "campaign_org"]
_LEDGER_FLUSH_EVERY = 200_000

_ORG_COLUMNS = (
    "company_id", "person_id", "cnpj", "tse_candidacy_id", "accountant_id", "year",
    "candidate_cpf", "candidate_name", "normalized_name", "office", "party_abbr",
    "state", "provenance_id", "collected_at",
)

_DON_COLUMNS = (
    "cnpj", "tse_candidacy_id", "year", "tse_receipt_id", "receipt_number", "document_id",
    "receipt_date", "amount_cents", "source", "origin", "species",
    "donor_cpf_cnpj", "donor_name", "donor_name_rfb", "donor_cnae", "donor_state",
    "donor_municipality", "donor_tse_candidacy_id", "donor_party_abbr",
    "donor_person_id", "donor_company_id", "provenance_id", "collected_at",
)

_EXP_COLUMNS = (
    "cnpj", "tse_candidacy_id", "year", "tse_expense_id", "document_type", "document_number",
    "expense_date", "amount_cents", "origin", "description",
    "supplier_cpf_cnpj", "supplier_name", "supplier_name_rfb", "supplier_type", "supplier_cnae",
    "supplier_state", "supplier_municipality", "supplier_tse_candidacy_id", "supplier_party_abbr",
    "supplier_person_id", "supplier_company_id", "provenance_id", "collected_at",
)

_PAY_COLUMNS = (
    "tse_expense_id", "tse_installment_id", "accountant_id", "year", "state",
    "document_type", "document_number", "payment_date", "amount_cents",
    "source", "origin", "nature", "species", "description", "provenance_id", "collected_at",
)


def _g(row: dict, *names: str) -> str | None:
    for n in names:
        if n in row and row[n] is not None:
            return clean_tse(row[n])
    return None


def _digits(v: str | None, n: int) -> str | None:
    d = digits_only(v)
    return d if d and len(d) == n else None


def _members(zf: zipfile.ZipFile, prefix: str) -> list[str]:
    names = [
        n for n in zf.namelist()
        if n.lower().endswith(".csv")
        and n.lower().startswith(prefix)
        and "doador_originario" not in n.lower()
    ]
    brasil = [n for n in names if "_brasil" in n.lower()]
    return sorted(brasil or names)


def run(con: sqlite3.Connection, *, years: list[int] | None = None,
        tmp_dir: str | Path = "dados_tmp") -> dict:
    years = years or list(SUPPORTED_YEARS)
    unknown = [y for y in years if y not in SUPPORTED_YEARS]
    if unknown:
        raise ValueError(f"no prestação de contas file for: {unknown}")

    log.info("rewrite-only: campaign_org/campaign_donation/campaign_expense/"
             "campaign_expense_payment will contain exactly these years: %s",
             ", ".join(map(str, years)))
    with step(log, "reset campaign_org + campaign_donation + campaign_expense(_payment)"):
        reset_source(con, SOURCE["name"], _OWNED_TABLES)
        con.execute("DELETE FROM companies WHERE kind IN ('campaign', 'donor', 'supplier')")
        con.commit()

    ph_rows = con.execute("SELECT count(*) FROM politician_history").fetchone()[0]
    if ph_rows == 0:
        log.warning("politician_history is empty — run `elosys tse-candidates` first "
                    "for identity to link up; continuing with CPF-only matching")
    rejected_cpf = {r["cpf"] for r in con.execute("SELECT cpf FROM rejected_cpf")}
    cpf_to_person = dict(con.execute("SELECT cpf, id FROM people WHERE cpf IS NOT NULL"))

    source_id = get_source(con, **SOURCE)
    report: dict = {"years": {}, "orgs": 0, "companies": 0, "donations": 0, "expenses": 0,
                     "payments": 0}
    company_cache: dict[str, int] = {}

    for year in years:
        report["years"][year] = _ingest_year(
            con, year, Path(tmp_dir), source_id, rejected_cpf, cpf_to_person, company_cache)

    report["orgs"] = con.execute("SELECT count(*) FROM campaign_org").fetchone()[0]
    report["companies"] = con.execute(
        "SELECT count(*) FROM companies WHERE kind IN ('campaign', 'donor', 'supplier')"
    ).fetchone()[0]
    report["orgs_linked_to_person"] = con.execute(
        "SELECT count(*) FROM campaign_org WHERE person_id IS NOT NULL").fetchone()[0]
    report["donations"] = con.execute("SELECT count(*) FROM campaign_donation").fetchone()[0]
    report["donations_linked_to_org"] = con.execute(
        "SELECT count(*) FROM campaign_donation WHERE campaign_org_id IS NOT NULL").fetchone()[0]
    report["donations_from_known_politician"] = con.execute(
        "SELECT count(*) FROM campaign_donation WHERE donor_person_id IS NOT NULL").fetchone()[0]
    report["total_donations_cents"] = con.execute(
        "SELECT coalesce(sum(amount_cents), 0) FROM campaign_donation").fetchone()[0]
    report["expenses"] = con.execute("SELECT count(*) FROM campaign_expense").fetchone()[0]
    report["expenses_linked_to_org"] = con.execute(
        "SELECT count(*) FROM campaign_expense WHERE campaign_org_id IS NOT NULL").fetchone()[0]
    report["expenses_to_known_politician"] = con.execute(
        "SELECT count(*) FROM campaign_expense WHERE supplier_person_id IS NOT NULL").fetchone()[0]
    report["total_expenses_cents"] = con.execute(
        "SELECT coalesce(sum(amount_cents), 0) FROM campaign_expense").fetchone()[0]
    report["payments"] = con.execute("SELECT count(*) FROM campaign_expense_payment").fetchone()[0]
    report["payments_linked_to_expense"] = con.execute(
        "SELECT count(*) FROM campaign_expense_payment WHERE campaign_expense_id IS NOT NULL"
    ).fetchone()[0]
    report["total_payments_cents"] = con.execute(
        "SELECT coalesce(sum(amount_cents), 0) FROM campaign_expense_payment").fetchone()[0]

    log.info("done: %s campaign CNPJs, %s companies, %s donations (R$ %s), "
             "%s expenses (R$ %s), %s payments (R$ %s)",
             f"{report['orgs']:,}", f"{report['companies']:,}", f"{report['donations']:,}",
             f"{report['total_donations_cents'] / 100:,.2f}", f"{report['expenses']:,}",
             f"{report['total_expenses_cents'] / 100:,.2f}", f"{report['payments']:,}",
             f"{report['total_payments_cents'] / 100:,.2f}")
    return report


def _ingest_year(con: sqlite3.Connection, year: int, tmp_dir: Path, source_id: int,
                 rejected_cpf: set[str], cpf_to_person: dict[str, int],
                 company_cache: dict[str, int]) -> dict:
    url = URL_TEMPLATE.format(year=year)
    zip_path = tmp_dir / f"prestacao_contas_candidatos_{year}.zip"

    with step(log, f"prestacao de contas - candidatos {year}"):
        if zip_path.exists():
            status, ctype, keep = None, "application/zip", True
            notes = "TSE CDN; file provided locally. URL is canonical."
            log.info("  using local file %s", zip_path)
        else:
            log.info("  download %s", url)
            status, ctype = download(url, zip_path)
            keep = False
            notes = "TSE CDN; the file may be re-published at the same URL."

        collection_id, is_new = record_collection(
            con, source_id=source_id, url=url, file=zip_path,
            http_status=status, content_type=ctype, notes=notes)
        size = con.execute("SELECT size_bytes FROM collection WHERE id = ?",
                           (collection_id,)).fetchone()[0]
        log.info("  %s  collection %d  (%s)",
                 "new" if is_new else "already recorded", collection_id, human_bytes(size))
        con.commit()

        ph_person = dict(con.execute(
            "SELECT tse_candidacy_id, person_id FROM politician_history WHERE year = ?", (year,)
        ).fetchall())

        counts = {"orgs": 0, "donations": 0, "expenses": 0, "payments": 0}
        with zipfile.ZipFile(zip_path) as zf:
            receitas = _members(zf, "receitas_candidatos_")
            despesas = _members(zf, "despesas_contratadas_candidatos_")
            pagas = _members(zf, "despesas_pagas_candidatos_")
            if not receitas:
                log.warning("  no receitas_candidatos file in the zip for %d", year)
                if not keep:
                    zip_path.unlink(missing_ok=True)
                return {"collection_id": collection_id, "new_orgs": 0, "new_donations": 0,
                        "new_expenses": 0, "new_payments": 0, "rows": 0}

            seen_org: set[tuple[str | None, str]] = set()
            orgs: list[dict] = []
            don_batch: list[dict] = []
            rc = RowCounter(log, f"receitas {year}")
            for name in receitas:
                data = zf.read(name)
                sha, fsize = sha256_bytes(data)
                file_id = record_file(con, collection_id=collection_id, filename=name,
                                      sha256=sha, size=fsize)
                parse_id = record_parse(con, collection_id=collection_id,
                                        collection_file_id=file_id, parser_name=PARSER_NAME,
                                        parser_version=PARSER_VERSION,
                                        rows_extracted=0, rows_rejected=0)
                _scan_receitas(data, year, seen_org, orgs, don_batch, counts, ph_person,
                               rejected_cpf, cpf_to_person, company_cache, con, parse_id, rc)
                con.execute("UPDATE parse SET rows_extracted = ? WHERE id = ?", (rc.n, parse_id))
            if don_batch:
                counts["donations"] += _write_donations(con, don_batch)
            rc.done()

            counts["orgs"] = _write_orgs(con, orgs)
            con.commit()
            with step(log, f"link donations {year} -> campaign_org"):
                _link_to_org(con, "campaign_donation", year)

            if despesas:
                exp_batch: list[dict] = []
                rc2 = RowCounter(log, f"despesas {year}")
                for name in despesas:
                    data = zf.read(name)
                    sha, fsize = sha256_bytes(data)
                    file_id = record_file(con, collection_id=collection_id, filename=name,
                                          sha256=sha, size=fsize)
                    parse_id = record_parse(con, collection_id=collection_id,
                                            collection_file_id=file_id, parser_name=PARSER_NAME,
                                            parser_version=PARSER_VERSION,
                                            rows_extracted=0, rows_rejected=0)
                    _scan_despesas(data, year, exp_batch, counts, ph_person, cpf_to_person,
                                  company_cache, con, parse_id, rc2)
                    con.execute("UPDATE parse SET rows_extracted = ? WHERE id = ?", (rc2.n, parse_id))
                if exp_batch:
                    counts["expenses"] += _write_expenses(con, exp_batch)
                rc2.done()
                con.commit()
                with step(log, f"link expenses {year} -> campaign_org"):
                    _link_to_org(con, "campaign_expense", year)
            else:
                log.warning("  no despesas_contratadas file in the zip for %d", year)

            if pagas:
                pay_batch: list[dict] = []
                rc3 = RowCounter(log, f"despesas pagas {year}")
                for name in pagas:
                    data = zf.read(name)
                    sha, fsize = sha256_bytes(data)
                    file_id = record_file(con, collection_id=collection_id, filename=name,
                                          sha256=sha, size=fsize)
                    parse_id = record_parse(con, collection_id=collection_id,
                                            collection_file_id=file_id, parser_name=PARSER_NAME,
                                            parser_version=PARSER_VERSION,
                                            rows_extracted=0, rows_rejected=0)
                    _scan_pagas(data, year, pay_batch, counts, con, parse_id, rc3)
                    con.execute("UPDATE parse SET rows_extracted = ? WHERE id = ?", (rc3.n, parse_id))
                if pay_batch:
                    counts["payments"] += _write_payments(con, pay_batch)
                rc3.done()
                con.commit()
                with step(log, f"link payments {year} -> campaign_expense"):
                    _link_payments_to_expense(con, year)
            else:
                log.warning("  no despesas_pagas file in the zip for %d", year)

        if not keep:
            zip_path.unlink(missing_ok=True)
        log.info("  %s campaign CNPJs, %s donations, %s expenses, %s payments for %d",
                 f"{counts['orgs']:,}", f"{counts['donations']:,}", f"{counts['expenses']:,}",
                 f"{counts['payments']:,}", year)
        return {"collection_id": collection_id, "new_orgs": counts["orgs"],
                "new_donations": counts["donations"], "new_expenses": counts["expenses"],
                "new_payments": counts["payments"], "rows": rc.n}


def _link_to_org(con: sqlite3.Connection, table: str, year: int) -> None:
    con.execute(
        f"UPDATE {table} SET campaign_org_id = ("  # noqa: S608
        "  SELECT co.id FROM campaign_org co"
        f"  WHERE co.year = {table}.year"
        f"    AND co.tse_candidacy_id = {table}.tse_candidacy_id"
        f"    AND co.cnpj = {table}.cnpj"
        f") WHERE campaign_org_id IS NULL AND year = ?", (year,))
    con.commit()


def _link_payments_to_expense(con: sqlite3.Connection, year: int) -> None:
    con.execute(
        "UPDATE campaign_expense_payment SET campaign_expense_id = ("
        "  SELECT ce.id FROM campaign_expense ce"
        "  WHERE ce.year = campaign_expense_payment.year"
        "    AND ce.tse_expense_id = campaign_expense_payment.tse_expense_id"
        ") WHERE campaign_expense_id IS NULL AND year = ?", (year,))
    con.commit()


def _scan_receitas(data: bytes, year: int, seen_org: set, orgs: list, don_batch: list,
                   counts: dict, ph_person: dict, rejected_cpf: set[str],
                   cpf_to_person: dict[str, int], company_cache: dict[str, int],
                   con: sqlite3.Connection, parse_id: int, rc: RowCounter) -> None:
    reader = csv.DictReader(
        io.TextIOWrapper(io.BytesIO(data), encoding="latin-1", newline=""), delimiter=";")
    now = now_utc()
    for row in reader:
        rc.tick()
        cnpj = _digits(_g(row, "NR_CNPJ_PRESTADOR_CONTA"), 14)
        sq = _g(row, "SQ_CANDIDATO")
        if not cnpj:
            continue

        org_key = (sq, cnpj)
        if org_key not in seen_org:
            seen_org.add(org_key)
            cpf = _digits(_g(row, "NR_CPF_CANDIDATO"), 11)
            if cpf in rejected_cpf:
                cpf = None
            name = _g(row, "NM_CANDIDATO")
            norm = normalize_name(name)
            person_id = ph_person.get(sq)
            if person_id is None:
                person_id, _ = resolve_person(con, cpf=cpf, voter_id=None, normalized_name=norm)
            orgs.append({
                "company_id": _get_company(con, cnpj, kind="campaign", cache=company_cache),
                "person_id": person_id,
                "cnpj": cnpj,
                "tse_candidacy_id": sq,
                "accountant_id": _g(row, "SQ_PRESTADOR_CONTAS"),
                "year": int(_g(row, "AA_ELEICAO") or year),
                "candidate_cpf": cpf,
                "candidate_name": name,
                "normalized_name": norm,
                "office": _g(row, "DS_CARGO"),
                "party_abbr": _g(row, "SG_PARTIDO"),
                "state": _g(row, "SG_UF"),
                "provenance_id": parse_id,
                "collected_at": now,
            })

        donor_raw = digits_only(_g(row, "NR_CPF_CNPJ_DOADOR"))
        donor_cpf = donor_raw if donor_raw and len(donor_raw) == 11 else None
        donor_cnpj = donor_raw if donor_raw and len(donor_raw) == 14 else None
        donor_candidacy = _g(row, "SQ_CANDIDATO_DOADOR")

        donor_person_id = None
        if donor_candidacy:
            donor_person_id = ph_person.get(donor_candidacy)
        elif donor_cpf:
            donor_person_id = cpf_to_person.get(donor_cpf)
        donor_company_id = (
            _get_company(con, donor_cnpj, kind="donor", cache=company_cache) if donor_cnpj else None
        )

        don_batch.append({
            "cnpj": cnpj,
            "tse_candidacy_id": sq,
            "year": int(_g(row, "AA_ELEICAO") or year),
            "tse_receipt_id": _g(row, "SQ_RECEITA"),
            "receipt_number": _g(row, "NR_RECIBO_DOACAO"),
            "document_id": _g(row, "NR_DOCUMENTO_DOACAO"),
            "receipt_date": iso_date(_g(row, "DT_RECEITA")),
            "amount_cents": brl_to_cents(_g(row, "VR_RECEITA")),
            "source": _g(row, "DS_FONTE_RECEITA"),
            "origin": _g(row, "DS_ORIGEM_RECEITA"),
            "species": _g(row, "DS_ESPECIE_RECEITA"),
            "donor_cpf_cnpj": donor_raw,
            "donor_name": _g(row, "NM_DOADOR"),
            "donor_name_rfb": _g(row, "NM_DOADOR_RFB"),
            "donor_cnae": _g(row, "DS_CNAE_DOADOR"),
            "donor_state": _g(row, "SG_UF_DOADOR"),
            "donor_municipality": _g(row, "NM_MUNICIPIO_DOADOR"),
            "donor_tse_candidacy_id": donor_candidacy,
            "donor_party_abbr": _g(row, "SG_PARTIDO_DOADOR"),
            "donor_person_id": donor_person_id,
            "donor_company_id": donor_company_id,
            "provenance_id": parse_id,
            "collected_at": now,
        })

        if len(don_batch) >= _LEDGER_FLUSH_EVERY:
            counts["donations"] += _write_donations(con, don_batch)
            don_batch.clear()


def _scan_despesas(data: bytes, year: int, exp_batch: list, counts: dict, ph_person: dict,
                   cpf_to_person: dict[str, int], company_cache: dict[str, int],
                   con: sqlite3.Connection, parse_id: int, rc: RowCounter) -> None:
    reader = csv.DictReader(
        io.TextIOWrapper(io.BytesIO(data), encoding="latin-1", newline=""), delimiter=";")
    now = now_utc()
    for row in reader:
        rc.tick()
        cnpj = _digits(_g(row, "NR_CNPJ_PRESTADOR_CONTA"), 14)
        sq = _g(row, "SQ_CANDIDATO")
        if not cnpj:
            continue

        supplier_raw = digits_only(_g(row, "NR_CPF_CNPJ_FORNECEDOR"))
        supplier_cpf = supplier_raw if supplier_raw and len(supplier_raw) == 11 else None
        supplier_cnpj = supplier_raw if supplier_raw and len(supplier_raw) == 14 else None
        supplier_candidacy = _g(row, "SQ_CANDIDATO_FORNECEDOR")

        supplier_person_id = None
        if supplier_candidacy:
            supplier_person_id = ph_person.get(supplier_candidacy)
        elif supplier_cpf:
            supplier_person_id = cpf_to_person.get(supplier_cpf)
        supplier_company_id = (
            _get_company(con, supplier_cnpj, kind="supplier", cache=company_cache)
            if supplier_cnpj else None
        )

        exp_batch.append({
            "cnpj": cnpj,
            "tse_candidacy_id": sq,
            "year": int(_g(row, "AA_ELEICAO") or year),
            "tse_expense_id": _g(row, "SQ_DESPESA"),
            "document_type": _g(row, "DS_TIPO_DOCUMENTO"),
            "document_number": _g(row, "NR_DOCUMENTO"),
            "expense_date": iso_date(_g(row, "DT_DESPESA")),
            "amount_cents": brl_to_cents(_g(row, "VR_DESPESA_CONTRATADA")),
            "origin": _g(row, "DS_ORIGEM_DESPESA"),
            "description": _g(row, "DS_DESPESA"),
            "supplier_cpf_cnpj": supplier_raw,
            "supplier_name": _g(row, "NM_FORNECEDOR"),
            "supplier_name_rfb": _g(row, "NM_FORNECEDOR_RFB"),
            "supplier_type": _g(row, "DS_TIPO_FORNECEDOR"),
            "supplier_cnae": _g(row, "DS_CNAE_FORNECEDOR"),
            "supplier_state": _g(row, "SG_UF_FORNECEDOR"),
            "supplier_municipality": _g(row, "NM_MUNICIPIO_FORNECEDOR"),
            "supplier_tse_candidacy_id": supplier_candidacy,
            "supplier_party_abbr": _g(row, "SG_PARTIDO_FORNECEDOR"),
            "supplier_person_id": supplier_person_id,
            "supplier_company_id": supplier_company_id,
            "provenance_id": parse_id,
            "collected_at": now,
        })

        if len(exp_batch) >= _LEDGER_FLUSH_EVERY:
            counts["expenses"] += _write_expenses(con, exp_batch)
            exp_batch.clear()


def _scan_pagas(data: bytes, year: int, pay_batch: list, counts: dict,
                con: sqlite3.Connection, parse_id: int, rc: RowCounter) -> None:
    reader = csv.DictReader(
        io.TextIOWrapper(io.BytesIO(data), encoding="latin-1", newline=""), delimiter=";")
    now = now_utc()
    for row in reader:
        rc.tick()
        expense_id = _g(row, "SQ_DESPESA")
        if not expense_id:
            continue
        pay_batch.append({
            "tse_expense_id": expense_id,
            "tse_installment_id": _g(row, "SQ_PARCELAMENTO_DESPESA"),
            "accountant_id": _g(row, "SQ_PRESTADOR_CONTAS"),
            "year": int(_g(row, "AA_ELEICAO") or year),
            "state": _g(row, "SG_UF"),
            "document_type": _g(row, "DS_TIPO_DOCUMENTO"),
            "document_number": _g(row, "NR_DOCUMENTO"),
            "payment_date": iso_date(_g(row, "DT_PAGTO_DESPESA")),
            "amount_cents": brl_to_cents(_g(row, "VR_PAGTO_DESPESA")),
            "source": _g(row, "DS_FONTE_DESPESA"),
            "origin": _g(row, "DS_ORIGEM_DESPESA"),
            "nature": _g(row, "DS_NATUREZA_DESPESA"),
            "species": _g(row, "DS_ESPECIE_RECURSO"),
            "description": _g(row, "DS_DESPESA"),
            "provenance_id": parse_id,
            "collected_at": now,
        })

        if len(pay_batch) >= _LEDGER_FLUSH_EVERY:
            counts["payments"] += _write_payments(con, pay_batch)
            pay_batch.clear()


def _get_company(con: sqlite3.Connection, cnpj: str, *, kind: str, cache: dict[str, int]) -> int:
    cached = cache.get(cnpj)
    if cached is not None:
        return cached
    row = con.execute("SELECT id FROM companies WHERE cnpj = ?", (cnpj,)).fetchone()
    if row:
        cache[cnpj] = row["id"]
        return row["id"]
    cur = con.execute(
        "INSERT INTO companies (cnpj, legal_name, kind, created_at) VALUES (?, NULL, ?, ?)",
        (cnpj, kind, now_utc()))
    cache[cnpj] = int(cur.lastrowid)
    return cache[cnpj]


def _write_orgs(con: sqlite3.Connection, orgs: list[dict]) -> int:
    if not orgs:
        return 0
    before = con.execute("SELECT count(*) FROM campaign_org").fetchone()[0]
    sql = (f"INSERT OR IGNORE INTO campaign_org ({', '.join(_ORG_COLUMNS)}) "
           f"VALUES ({', '.join(f':{c}' for c in _ORG_COLUMNS)})")
    con.executemany(sql, orgs)
    after = con.execute("SELECT count(*) FROM campaign_org").fetchone()[0]
    return after - before


def _write_donations(con: sqlite3.Connection, donations: list[dict]) -> int:
    before = con.execute("SELECT count(*) FROM campaign_donation").fetchone()[0]
    sql = (f"INSERT OR IGNORE INTO campaign_donation ({', '.join(_DON_COLUMNS)}) "
           f"VALUES ({', '.join(f':{c}' for c in _DON_COLUMNS)})")
    con.executemany(sql, donations)
    con.commit()
    after = con.execute("SELECT count(*) FROM campaign_donation").fetchone()[0]
    return after - before


def _write_expenses(con: sqlite3.Connection, expenses: list[dict]) -> int:
    before = con.execute("SELECT count(*) FROM campaign_expense").fetchone()[0]
    sql = (f"INSERT OR IGNORE INTO campaign_expense ({', '.join(_EXP_COLUMNS)}) "
           f"VALUES ({', '.join(f':{c}' for c in _EXP_COLUMNS)})")
    con.executemany(sql, expenses)
    con.commit()
    after = con.execute("SELECT count(*) FROM campaign_expense").fetchone()[0]
    return after - before


def _write_payments(con: sqlite3.Connection, payments: list[dict]) -> int:
    before = con.execute("SELECT count(*) FROM campaign_expense_payment").fetchone()[0]
    sql = (f"INSERT OR IGNORE INTO campaign_expense_payment ({', '.join(_PAY_COLUMNS)}) "
           f"VALUES ({', '.join(f':{c}' for c in _PAY_COLUMNS)})")
    con.executemany(sql, payments)
    con.commit()
    after = con.execute("SELECT count(*) FROM campaign_expense_payment").fetchone()[0]
    return after - before
