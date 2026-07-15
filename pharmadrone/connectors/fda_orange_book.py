"""Official FDA Orange Book product, patent and exclusivity connector.

The Orange Book is regulatory lifecycle context. Patent and exclusivity rows are
reported facts, not evidence of infringement, freedom to operate, product
failure, customer demand or commercial intent.
"""
from __future__ import annotations

import csv
from datetime import datetime
from io import BytesIO, StringIO
import os
from typing import Any
from zipfile import BadZipFile, ZipFile

import httpx

from .base import ConnectorResult, USER_AGENT, describe_error, record

NAME = "FDA Orange Book"
DATA_PAGE = "https://www.fda.gov/drugs/drug-approvals-and-databases/orange-book-data-files"
DEFAULT_ARCHIVE_URL = "https://www.fda.gov/media/76860/download?attachment="
REQUIRED_FILES = {"products.txt", "patent.txt", "exclusivity.txt"}


def _clean(value: Any) -> str:
    return " ".join(str(value or "").replace("\ufeff", "").split()).strip()


def _date(value: Any) -> str:
    text = _clean(value)
    for fmt in ("%b %d, %Y", "%b %d %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def _rows(raw: bytes) -> list[dict[str, str]]:
    text = raw.decode("latin-1").replace("\r\n", "\n")
    return [{_clean(k).casefold(): _clean(v) for k, v in row.items()} for row in csv.DictReader(StringIO(text), delimiter="~")]


def _value(row: dict[str, str], *names: str) -> str:
    for name in names:
        if row.get(name.casefold()):
            return row[name.casefold()]
    return ""


def parse_archive(payload: bytes, *, max_results: int = 5000, term: str = "") -> ConnectorResult:
    try:
        with ZipFile(BytesIO(payload)) as archive:
            names = {name.rsplit("/", 1)[-1].casefold(): name for name in archive.namelist() if not name.endswith("/")}
            missing = REQUIRED_FILES - set(names)
            if missing:
                return ConnectorResult(NAME, term or "Orange Book", ok=False, error=f"missing expected file(s): {', '.join(sorted(missing))}")
            products = _rows(archive.read(names["products.txt"]))
            patents = _rows(archive.read(names["patent.txt"]))
            exclusivities = _rows(archive.read(names["exclusivity.txt"]))
    except (BadZipFile, UnicodeError, csv.Error) as exc:
        return ConnectorResult(NAME, term or "Orange Book", ok=False, error=f"invalid Orange Book archive: {exc}")

    def key(row: dict[str, str]) -> tuple[str, str]:
        return (_value(row, "Appl_No", "Appl No"), _value(row, "Product_No", "Product No"))

    patent_map: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in patents:
        patent_map.setdefault(key(item), []).append({
            "patent_number": _value(item, "Patent_No", "Patent No"),
            "expiry_date": _date(_value(item, "Patent_Expire_Date_Text", "Patent Expire Date")),
            "drug_substance": _value(item, "Drug_Substance_Flag", "Drug Substance Flag"),
            "drug_product": _value(item, "Drug_Product_Flag", "Drug Product Flag"),
            "use_code": _value(item, "Patent_Use_Code", "Patent Use Code"),
            "delist_requested": _value(item, "Delist_Flag", "Patent Delist Request Flag"),
            "submission_date": _date(_value(item, "Submission_Date", "Patent Submission Date")),
        })
    exclusivity_map: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in exclusivities:
        exclusivity_map.setdefault(key(item), []).append({
            "code": _value(item, "Exclusivity_Code", "Exclusivity Code"),
            "expiry_date": _date(_value(item, "Exclusivity_Date", "Exclusivity Date")),
        })

    needle = _clean(term).casefold()
    records = []
    rejected = 0
    for item in products:
        application, product_number = key(item)
        ingredient = _value(item, "Ingredient")
        trade_name = _value(item, "Trade_Name", "Trade Name")
        applicant = _value(item, "Applicant_Full_Name", "Applicant Full Name", "Applicant")
        searchable = " ".join((application, ingredient, trade_name, applicant)).casefold()
        if needle and needle not in searchable:
            continue
        if not application or not product_number or not trade_name:
            rejected += 1
            continue
        item_key = (application, product_number)
        product_patents = patent_map.get(item_key, [])
        product_exclusivities = exclusivity_map.get(item_key, [])
        source_id = f"{application}-{product_number}"
        records.append(record(
            "fda_orange_book_product", NAME, source_id, f"FDA Orange Book: {trade_name}", DATA_PAGE,
            f"Official FDA Orange Book product. Trade name: {trade_name}. Active ingredient: {ingredient}. "
            f"Applicant: {applicant}. Application: {application}; product: {product_number}. "
            f"Patents listed: {len(product_patents)}. Unexpired exclusivity entries listed: {len(product_exclusivities)}.",
            source_category="regulatory",
            entities={
                "company": applicant or None, "product": trade_name, "molecule": ingredient or None,
                "source_event_id": source_id, "application_number": application, "product_number": product_number,
                "application_type": _value(item, "Appl_Type", "New Drug Application Type"),
                "dosage_form_route": _value(item, "DF;Route", "Dosage Form; Route of Administration"),
                "strength": _value(item, "Strength"), "therapeutic_equivalence_code": _value(item, "TE_Code", "TE Code"),
                "approval_date": _date(_value(item, "Approval_Date", "Approval Date")),
                "reference_listed_drug": _value(item, "RLD"), "reference_standard": _value(item, "RS"),
                "market_category": _value(item, "Type"), "patents": product_patents,
                "exclusivities": product_exclusivities, "regulator": "FDA", "region": "United States",
                "official_source_url": DATA_PAGE, "direct_problem_evidence": False,
            },
        ))
        if len(records) >= max(0, int(max_results)):
            break
    return ConnectorResult(
        NAME, term or "Orange Book", ok=True, count=len(records), records=records,
        warnings=["Regulatory lifecycle context only; patent listings are not legal advice and do not establish freedom to operate or commercial need."],
        stats={"products_in_archive": len(products), "patents_in_archive": len(patents),
               "exclusivities_in_archive": len(exclusivities), "accepted_records": len(records),
               "rejected_records": rejected, "dataset_url": DATA_PAGE},
    )


def fetch(*, max_results: int = 5000) -> ConnectorResult:
    url = os.getenv("FDA_ORANGE_BOOK_ARCHIVE_URL", DEFAULT_ARCHIVE_URL).strip() or DEFAULT_ARCHIVE_URL
    try:
        with httpx.Client(timeout=90, follow_redirects=True, headers={"User-Agent": USER_AGENT, "Accept": "application/zip"}) as client:
            response = client.get(url)
            response.raise_for_status()
        result = parse_archive(response.content, max_results=max_results)
        result.stats.setdefault("archive_url", url)
        return result
    except Exception as exc:
        return ConnectorResult(NAME, "Orange Book", ok=False, error=describe_error(exc), stats={"archive_url": url})


def search(term: str, max_results: int = 10) -> ConnectorResult:
    result = fetch(max_results=5000)
    if not result.ok:
        result.query = term
        return result
    matching = [item for item in result.records if term.casefold() in f"{item.get('title')} {item.get('raw_text')}".casefold()][:max_results]
    result.query, result.records, result.count = term, matching, len(matching)
    return result
