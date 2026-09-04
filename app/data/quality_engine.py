"""
Deterministic Data Quality Engine.

Ingests raw CSVs for one source (payment / settlement / invoice) and
produces:
  - a list of clean, normalized, typed records (Decimal amounts, date
    objects, trimmed/uppercased IDs)
  - a DataQualityReport with counts and a full list of issues
  - a list of REJECTED raw rows, each with the reason it was rejected
    (never silently dropped)

This module does not use an LLM. Every decision here is a plain
if/else or regex -- validation of money and dates is not something an
LLM should be doing "creatively."
"""
import csv
import re
from decimal import Decimal, InvalidOperation
from datetime import datetime, date
from dataclasses import asdict
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from app.models.schemas import ValidationIssue, DataQualityReport

MIN_REASONABLE_DATE = date(2000, 1, 1)
MAX_REASONABLE_DATE = date(2030, 12, 31)

DATE_FORMATS_TO_TRY = ["%Y-%m-%d", "%d/%m/%Y", "%m-%d-%Y", "%d-%b-%Y"]

AMOUNT_CLEAN_RE = re.compile(r"[^\d.\-]")


def normalize_id(raw: str) -> str:
    return raw.strip().upper()


def parse_amount(raw: str):
    """Returns (Decimal, was_normalized: bool) or raises ValueError."""
    if raw is None:
        raise ValueError("missing amount")
    original = raw
    cleaned = raw.strip()
    if cleaned == "" or cleaned.upper() in ("N/A", "NA", "NULL"):
        raise ValueError("missing/placeholder amount")

    # strip currency symbols, commas, trailing currency codes (e.g. "1234 INR")
    cleaned = cleaned.replace(",", "")
    cleaned = re.sub(r"[₹$]", "", cleaned)
    cleaned = re.sub(r"\s*[A-Za-z]{3}$", "", cleaned)  # trailing "INR"/"USD"
    cleaned = cleaned.strip()

    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(f"unparseable amount: {original!r}")

    if value <= 0:
        raise ValueError(f"non-positive amount: {value}")

    was_normalized = cleaned != original.strip()
    return value, was_normalized


def parse_date(raw: str):
    """Returns (date, was_normalized: bool) or raises ValueError."""
    if raw is None or raw.strip() == "":
        raise ValueError("missing date")
    cleaned = raw.strip()

    for i, fmt in enumerate(DATE_FORMATS_TO_TRY):
        try:
            parsed = datetime.strptime(cleaned, fmt).date()
            if not (MIN_REASONABLE_DATE <= parsed <= MAX_REASONABLE_DATE):
                raise ValueError(f"impossible/out-of-range date: {cleaned}")
            was_normalized = (i != 0)  # format 0 is the canonical ISO format
            return parsed, was_normalized
        except ValueError as e:
            if "impossible/out-of-range" in str(e):
                raise
            continue  # try next format

    raise ValueError(f"unparseable date: {raw!r}")


REQUIRED_FIELDS = {
    "payment": ["transaction_id", "order_id", "customer_id", "amount",
                "currency", "transaction_date", "payment_method", "status"],
    "settlement": ["settlement_id", "transaction_id", "settled_amount",
                   "settlement_date", "fee", "currency", "bank_reference",
                   "settlement_status"],
    "invoice": ["invoice_id", "order_id", "expected_amount", "invoice_date",
                "customer_id", "invoice_status"],
}
AMOUNT_FIELDS = {
    "payment": ["amount"],
    "settlement": ["settled_amount", "fee"],
    "invoice": ["expected_amount"],
}
DATE_FIELDS = {
    "payment": "transaction_date",
    "settlement": "settlement_date",
    "invoice": "invoice_date",
}
ID_FIELDS = {
    "payment": ["transaction_id", "order_id", "customer_id"],
    "settlement": ["settlement_id", "transaction_id", "bank_reference"],
    "invoice": ["invoice_id", "order_id", "customer_id"],
}
PRIMARY_KEY = {
    "payment": "transaction_id",
    "settlement": "settlement_id",
    "invoice": "invoice_id",
}


def process_source(rows: list, source: str):
    """
    rows: list of dicts as read straight from csv.DictReader (all strings).
    Returns (clean_records: list[dict], rejected: list[dict], report: DataQualityReport)
    """
    issues = []
    clean_records = []
    rejected = []
    seen_keys = set()
    duplicate_count = 0
    normalization_actions = 0

    required = REQUIRED_FIELDS[source]
    amount_fields = AMOUNT_FIELDS[source]
    date_field = DATE_FIELDS[source]
    id_fields = ID_FIELDS[source]
    pk_field = PRIMARY_KEY[source]

    for row_idx, row in enumerate(rows):
        row_id_for_reporting = row.get(pk_field, f"<row {row_idx}>")
        row_issues = []

        # missing required fields
        missing = [f for f in required if not row.get(f) or not row[f].strip()]
        if missing:
            row_issues.append(("MISSING_FIELD", f"missing/blank field(s): {missing}"))

        # normalize + validate IDs
        normalized_row = dict(row)
        for f in id_fields:
            if row.get(f):
                norm = normalize_id(row[f])
                if norm != row[f]:
                    normalization_actions += 1
                normalized_row[f] = norm

        # duplicate detection on primary key (post-normalization)
        pk_value = normalized_row.get(pk_field)
        if pk_value:
            dedup_key = (pk_value, tuple(normalized_row.get(f, "") for f in required))
            if dedup_key in seen_keys:
                duplicate_count += 1
                row_issues.append(("DUPLICATE_ID", f"exact duplicate row for {pk_field}={pk_value}"))
            seen_keys.add(dedup_key)

        # amounts
        parsed_amounts = {}
        for f in amount_fields:
            try:
                value, was_norm = parse_amount(row.get(f))
                parsed_amounts[f] = value
                if was_norm:
                    normalization_actions += 1
            except ValueError as e:
                row_issues.append(("INVALID_AMOUNT", f"{f}: {e}"))

        # date
        parsed_date = None
        try:
            parsed_date, was_norm = parse_date(row.get(date_field))
            if was_norm:
                normalization_actions += 1
        except ValueError as e:
            row_issues.append(("MALFORMED_DATE", f"{date_field}: {e}"))

        if row_issues:
            for issue_type, detail in row_issues:
                issues.append(ValidationIssue(
                    record_id=str(pk_value or row_id_for_reporting),
                    source=source, issue_type=issue_type, detail=detail,
                ))
            # DUPLICATE_ID alone doesn't invalidate the record's data, but every
            # other issue type means we can't safely use this row
            hard_failures = [t for t, _ in row_issues if t != "DUPLICATE_ID"]
            if hard_failures:
                rejected.append({**row, "_rejection_reasons": hard_failures})
                continue

        normalized_row.update({k: str(v) for k, v in parsed_amounts.items()})
        normalized_row[date_field] = parsed_date.isoformat()
        clean_records.append(normalized_row)

    report = DataQualityReport(
        source=source,
        total_records=len(rows),
        valid_records=len(clean_records),
        invalid_records=len(rejected),
        duplicate_records=duplicate_count,
        normalization_actions=normalization_actions,
        issues=issues,
    )
    return clean_records, rejected, report


def load_and_process(path: str, source: str):
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    return process_source(rows, source)


if __name__ == "__main__":
    base = os.path.join(os.path.dirname(__file__), "..", "..", "data", "generated")
    for source, fname in [
        ("payment", "payments_raw.csv"),
        ("settlement", "settlements_raw.csv"),
        ("invoice", "invoices_raw.csv"),
    ]:
        clean, rejected, report = load_and_process(os.path.join(base, fname), source)
        print(f"\n=== {source} ===")
        for k, v in asdict(report).items():
            if k != "issues":
                print(f"  {k}: {v}")
        print(f"  sample issues: {[ (i.issue_type, i.detail) for i in report.issues[:3] ]}")
