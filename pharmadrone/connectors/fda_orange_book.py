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

from .base import ConnectorResult, USER_AGENT, describe_error, get_json, record

NAME = "FDA Orange Book"
DATA_PAGE = "https://www.fda.gov/drugs/drug-approvals-and-databases/orange-book-data-files"
DEFAULT_ARCHIVE_URL = "https://www.fda.gov/media/76860/download?attachment="
DRUGSFDA_API = "https://api.fda.gov/drug/drugsfda.json"
DRUGSFDA_PAGE = "https://www.fda.gov/drugs/drug-approvals-and-databases/drugsfda-data-files"
REQUIRED_FILES = {"products.txt", "patent.txt", "exclusivity.txt"}


def _clean(value: Any) -> str:
    return " ".join(str(value or "").replace("\ufeff", "").split()).strip()


def _date(value: Any) -> str:
    text = _clean(value)
    for fmt in ("%b %d, %Y", "%b %d %Y", "%Y-%m-%d", "%Y%m%d"):
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


def parse_drugsfda_payload(payload: dict[str, Any], *, max_results: int = 5000) -> ConnectorResult:
    """Parse the official daily Drugs@FDA API as a product-only fallback.

    This deliberately leaves patent and exclusivity arrays empty. Missing
    Orange Book lifecycle fields are never inferred from Drugs@FDA.
    """
    applications = payload.get("results") or []
    if not isinstance(applications, list):
        return ConnectorResult(NAME, "Drugs@FDA fallback", ok=False, error="response results were not a list")
    records = []
    rejected = 0
    for application in applications:
        app_number = _clean(application.get("application_number"))
        sponsor = _clean(application.get("sponsor_name"))
        submissions = application.get("submissions") or []
        approval = max([
            _date(item.get("submission_status_date")) for item in submissions
            if item.get("submission_type") == "ORIG" and item.get("submission_status") == "AP"
        ] + [""])
        for product in application.get("products") or []:
            product_number = _clean(product.get("product_number"))
            trade_name = _clean(product.get("brand_name"))
            if not app_number or not product_number or not trade_name:
                rejected += 1
                continue
            ingredients = product.get("active_ingredients") or []
            ingredient = "; ".join(_clean(item.get("name")) for item in ingredients if _clean(item.get("name")))
            strength = "; ".join(_clean(item.get("strength")) for item in ingredients if _clean(item.get("strength")))
            normalised_application = app_number.removeprefix("ANDA").removeprefix("NDA")
            source_id = f"{normalised_application}-{product_number}"
            records.append(record(
                "fda_orange_book_product", NAME, source_id, f"FDA drug product: {trade_name}", DRUGSFDA_PAGE,
                f"Official Drugs@FDA product fallback. Trade name: {trade_name}. Active ingredient: {ingredient}. "
                f"Sponsor: {sponsor}. Application: {app_number}; product: {product_number}. "
                "Orange Book patent and exclusivity fields were unavailable and remain empty.",
                source_category="regulatory",
                entities={
                    "company": sponsor or None, "product": trade_name, "molecule": ingredient or None,
                    "source_event_id": source_id, "application_number": normalised_application, "product_number": product_number,
                    "application_type": "ANDA" if app_number.startswith("ANDA") else "NDA" if app_number.startswith("NDA") else "",
                    "dosage_form_route": "; ".join(filter(None, (_clean(product.get("dosage_form")), _clean(product.get("route"))))),
                    "strength": strength, "therapeutic_equivalence_code": _clean(product.get("te_code")),
                    "approval_date": approval, "reference_listed_drug": _clean(product.get("reference_drug")),
                    "reference_standard": _clean(product.get("reference_standard")),
                    "market_category": _clean(product.get("marketing_status")), "patents": [], "exclusivities": [],
                    "regulator": "FDA", "region": "United States", "official_source_url": DRUGSFDA_PAGE,
                    "dataset_mode": "Drugs@FDA product fallback", "direct_problem_evidence": False,
                },
            ))
            if len(records) >= max(0, int(max_results)):
                break
        if len(records) >= max(0, int(max_results)):
            break
    meta = payload.get("meta") or {}
    return ConnectorResult(
        NAME, "Drugs@FDA fallback", ok=True, count=len(records), records=records,
        warnings=["FDA Orange Book archive unavailable; official Drugs@FDA product facts loaded without patent or exclusivity inference."],
        stats={"accepted_records": len(records), "rejected_records": rejected,
               "feed_last_updated": _clean(meta.get("last_updated")), "dataset_mode": "Drugs@FDA product fallback",
               "dataset_url": DRUGSFDA_PAGE},
    )


def fetch_drugsfda_fallback(*, max_results: int = 5000) -> ConnectorResult:
    combined: list[dict[str, Any]] = []
    last_updated = ""
    page_size = min(1000, max(1, int(max_results)))
    for skip in range(0, max(0, int(max_results)), page_size):
        payload = get_json(DRUGSFDA_API, {"limit": min(page_size, max_results - skip), "skip": skip})
        last_updated = _clean((payload.get("meta") or {}).get("last_updated")) or last_updated
        combined.extend(payload.get("results") or [])
        if len(payload.get("results") or []) < min(page_size, max_results - skip):
            break
    result = parse_drugsfda_payload({"meta": {"last_updated": last_updated}, "results": combined}, max_results=max_results)
    result.stats["applications_retrieved"] = len(combined)
    return result


def fetch(*, max_results: int = 5000) -> ConnectorResult:
    url = os.getenv("FDA_ORANGE_BOOK_ARCHIVE_URL", DEFAULT_ARCHIVE_URL).strip() or DEFAULT_ARCHIVE_URL
    try:
        with httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": USER_AGENT, "Accept": "application/zip"}) as client:
            response = client.get(url)
            response.raise_for_status()
        if not response.content.startswith(b"PK"):
            raise ValueError("FDA returned a web challenge instead of the Orange Book ZIP")
        result = parse_archive(response.content, max_results=max_results)
        if not result.ok:
            raise ValueError(result.error or "invalid Orange Book archive")
        result.stats.update({"archive_url": url, "dataset_mode": "Orange Book archive"})
        return result
    except Exception as exc:
        archive_error = describe_error(exc)
        try:
            fallback = fetch_drugsfda_fallback(max_results=max_results)
            fallback.stats.update({"archive_url": url, "archive_error": archive_error})
            return fallback
        except Exception as fallback_exc:
            return ConnectorResult(
                NAME, "Orange Book", ok=False,
                error=f"Orange Book archive failed ({archive_error}); Drugs@FDA fallback failed ({describe_error(fallback_exc)})",
                stats={"archive_url": url, "fallback_url": DRUGSFDA_API},
            )


def search(term: str, max_results: int = 10) -> ConnectorResult:
    result = fetch(max_results=5000)
    if not result.ok:
        result.query = term
        return result
    matching = [item for item in result.records if term.casefold() in f"{item.get('title')} {item.get('raw_text')}".casefold()][:max_results]
    result.query, result.records, result.count = term, matching, len(matching)
    return result
