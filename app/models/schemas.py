"""
Core data models for LedgerPilot.

All monetary amounts use Decimal, never float -- floating point
arithmetic on money produces silent rounding errors that are exactly
the kind of thing a finance-control tool must not introduce itself.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from datetime import date
from typing import Optional


@dataclass
class PaymentTransaction:
    transaction_id: str
    order_id: str
    customer_id: str
    amount: Decimal
    currency: str
    transaction_date: date
    payment_method: str
    status: str  # e.g. captured, failed, refunded


@dataclass
class SettlementRecord:
    settlement_id: str
    transaction_id: Optional[str]  # reference_id into PaymentTransaction; may be missing/malformed
    settled_amount: Decimal
    settlement_date: date
    fee: Decimal
    currency: str
    bank_reference: str
    settlement_status: str  # e.g. settled, pending, reversed


@dataclass
class InvoiceRecord:
    invoice_id: str
    order_id: str
    expected_amount: Decimal
    invoice_date: date
    customer_id: str
    invoice_status: str  # e.g. issued, paid, cancelled


@dataclass
class ValidationIssue:
    record_id: str
    source: str  # "payment" | "settlement" | "invoice"
    issue_type: str  # e.g. MISSING_FIELD, INVALID_AMOUNT, DUPLICATE_ID, MALFORMED_DATE
    detail: str


@dataclass
class DataQualityReport:
    source: str
    total_records: int
    valid_records: int
    invalid_records: int
    duplicate_records: int
    normalization_actions: int
    issues: list = field(default_factory=list)  # list[ValidationIssue]
