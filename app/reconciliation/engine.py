"""
Deterministic Reconciliation Engine.

No LLM anywhere in this file. Matching, money math, and exception
classification are all plain Python + Decimal. The AI Controller (later)
reads the OUTPUT of this engine as evidence -- it never recomputes or
overrides anything decided here.

Matching strategy, tried in order per payment transaction:
  1. MATCH_EXACT_ID    -- settlement.transaction_id == payment.transaction_id (post-normalization)
  2. MATCH_ORDER_ID    -- no exact txn ref match, but a settlement's bank_reference
                          or the invoice order_id resolves unambiguously
  3. MATCH_AMOUNT_DATE -- no ID match at all, but exactly one settlement has the
                          same amount (within tolerance) and a close date
  4. PROBABLE_MATCH    -- a fuzzy candidate exists but with lower confidence
                          (e.g. id partially corrupted, matched via edit distance)
  5. UNMATCHED         -- nothing found

Every match carries a `method` and a `confidence` (0-1, deterministic
formula -- not learned, not LLM-assigned).
"""
from decimal import Decimal
from datetime import date, datetime
from dataclasses import dataclass, field, asdict
from difflib import SequenceMatcher
import json
import os

AMOUNT_TOLERANCE = Decimal("0.01")
CLOSE_DATE_WINDOW_DAYS = 3
STANDARD_FEE_RATE = Decimal("0.02")
FEE_TOLERANCE_RATE = Decimal("0.005")  # +/- 0.5% of amount is "normal" fee variance


@dataclass
class MatchResult:
    transaction_id: str
    order_id: str
    method: str  # MATCH_EXACT_ID | MATCH_ORDER_ID | MATCH_AMOUNT_DATE | PROBABLE_MATCH | UNMATCHED
    confidence: float
    settlement_id: str = None
    invoice_id: str = None
    reason: str = ""  # human-readable explanation of why this match/non-match was decided
    amount_difference: str = None  # str(Decimal) -- kept as string for JSON safety
    fee_difference: str = None
    date_difference_days: int = None


@dataclass
class Exception_:  # trailing underscore: "Exception" is a builtin
    exception_id: str
    reference_id: str  # transaction_id or order_id
    category: str
    affected_amount: str
    evidence: dict
    severity: str
    confidence: float
    status: str
    created_at: str


def _to_decimal(s) -> Decimal:
    return s if isinstance(s, Decimal) else Decimal(str(s))


def _to_date(s) -> date:
    return s if isinstance(s, date) else datetime.strptime(str(s), "%Y-%m-%d").date()


def _id_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = curr
    return prev[-1]


class ReconciliationEngine:
    def __init__(self, payments: list, settlements: list, invoices: list):
        """Each argument is a list of normalized dicts, as produced by quality_engine.py."""
        self.payments = payments
        self.settlements = settlements
        self.invoices = invoices

        self.settlements_by_ref = {}
        for s in settlements:
            self.settlements_by_ref.setdefault(s["transaction_id"], []).append(s)

        self.invoices_by_order = {inv["order_id"]: inv for inv in invoices}

        self._exception_seq = 0

    def _next_exception_id(self) -> str:
        self._exception_seq += 1
        return f"EXC{self._exception_seq:06d}"

    def match_payment(self, payment: dict, claimed_settlement_ids: set = None) -> MatchResult:
        """
        claimed_settlement_ids: settlement_ids already consumed by an exact-ID
        match for some OTHER transaction. Excluded from every fallback path so
        a settlement already legitimately claimed can't also be grabbed by an
        unrelated transaction's fuzzy/numeric matching -- without this, a
        genuinely settlement-less transaction can "steal" someone else's
        settlement just because the numbers happen to land close, which
        silently erases real MISSING_SETTLEMENT cases.
        """
        claimed_settlement_ids = claimed_settlement_ids or set()
        available_settlements = [
            s for s in self.settlements if s["settlement_id"] not in claimed_settlement_ids
        ]

        txn_id = payment["transaction_id"]
        order_id = payment["order_id"]
        amount = _to_decimal(payment["amount"])
        txn_date = _to_date(payment["transaction_date"])

        # 1. exact transaction ID match
        candidates = self.settlements_by_ref.get(txn_id)
        if candidates:
            settlement = candidates[0]  # duplicate handling occurs separately
            return MatchResult(
                transaction_id=txn_id, order_id=order_id,
                method="MATCH_EXACT_ID", confidence=0.99,
                settlement_id=settlement["settlement_id"],
                invoice_id=self.invoices_by_order.get(order_id, {}).get("invoice_id"),
                reason=(f"Settlement {settlement['settlement_id']}'s reference field exactly "
                        f"matches transaction_id {txn_id} after normalization."),
            )

        # 2. order-based match: invoice exists for this order, and some settlement's
        #    bank_reference or amount uniquely lines up with this order's invoice
        invoice = self.invoices_by_order.get(order_id)
        if invoice:
            expected = _to_decimal(invoice["expected_amount"])
            same_amount_settlements = [
                s for s in available_settlements
                if abs(_to_decimal(s["settled_amount"]) - expected) <= AMOUNT_TOLERANCE * 50  # loose, fee-aware band
            ]
            if len(same_amount_settlements) == 1:
                s = same_amount_settlements[0]
                return MatchResult(
                    transaction_id=txn_id, order_id=order_id,
                    method="MATCH_ORDER_ID", confidence=0.85,
                    settlement_id=s["settlement_id"], invoice_id=invoice["invoice_id"],
                    reason=(f"No exact transaction reference match, but invoice {invoice['invoice_id']} "
                            f"for order {order_id} uniquely identifies settlement {s['settlement_id']} "
                            f"by expected amount."),
                )

        # 3 & 4. Numeric-fingerprint matching for when the reference ID itself is
        # unusable (corrupted or entirely garbled -- e.g. INVALID_REFERENCE).
        # IMPORTANT: this domain's IDs are SEQUENTIAL ("TXN10001", "TXN10002"...),
        # so string/edit-distance similarity between two totally UNRELATED ids is
        # naturally tiny (often 1-2 characters), making ID similarity useless as a
        # discriminator here. The reliable signal is the money itself: the net
        # settlement amount is a near-continuous value, so requiring it to land
        # within a tight absolute band of (amount - standard fee) is what actually
        # distinguishes "this is probably the same transaction" from "this just
        # happens to have a nearby ID."
        expected_net = amount - (amount * STANDARD_FEE_RATE)
        tight_tolerance = max(Decimal("5.00"), amount * Decimal("0.005"))  # ~0.5%, floor Rs 5
        candidates = [
            s for s in available_settlements
            if abs(_to_decimal(s["settled_amount"]) - expected_net) <= tight_tolerance
            and abs((_to_date(s["settlement_date"]) - txn_date).days) <= CLOSE_DATE_WINDOW_DAYS
        ]

        if len(candidates) == 1:
            return MatchResult(
                transaction_id=txn_id, order_id=order_id,
                method="MATCH_AMOUNT_DATE", confidence=0.85,
                settlement_id=candidates[0]["settlement_id"],
                invoice_id=self.invoices_by_order.get(order_id, {}).get("invoice_id"),
                reason=(f"Reference did not match any settlement directly, but exactly one "
                        f"settlement ({candidates[0]['settlement_id']}) has a settled amount "
                        f"within Rs {tight_tolerance} of the expected net (Rs {expected_net}) "
                        f"and a date within {CLOSE_DATE_WINDOW_DAYS} days -- a unique numeric "
                        f"fingerprint match."),
            )

        if len(candidates) > 1:
            # multiple numeric matches -- use ID edit distance only as a
            # tie-breaker among an already-plausible pool, never as the
            # primary signal
            best = min(candidates, key=lambda s: _levenshtein(txn_id, s["transaction_id"]))
            return MatchResult(
                transaction_id=txn_id, order_id=order_id,
                method="PROBABLE_MATCH", confidence=0.55,
                settlement_id=best["settlement_id"],
                invoice_id=self.invoices_by_order.get(order_id, {}).get("invoice_id"),
                reason=(f"{len(candidates)} settlements matched the numeric fingerprint "
                        f"ambiguously; {best['settlement_id']} was selected as the closest by "
                        f"reference-ID edit distance, used only as a tie-breaker, not the "
                        f"primary signal. Confidence is intentionally low."),
            )

        # widen slightly (looser amount band) as a last resort for genuinely
        # corrupted-reference cases where the settled amount also drifted a
        # little from the textbook 2% fee -- still requires date agreement
        loose_candidates = [
            s for s in available_settlements
            if abs(_to_decimal(s["settled_amount"]) - amount) <= (amount * Decimal("0.15"))
            and abs((_to_date(s["settlement_date"]) - txn_date).days) <= CLOSE_DATE_WINDOW_DAYS
        ]
        if len(loose_candidates) == 1:
            return MatchResult(
                transaction_id=txn_id, order_id=order_id,
                method="PROBABLE_MATCH", confidence=0.6,
                settlement_id=loose_candidates[0]["settlement_id"],
                invoice_id=self.invoices_by_order.get(order_id, {}).get("invoice_id"),
                reason=(f"No tight numeric fingerprint match, but exactly one settlement "
                        f"({loose_candidates[0]['settlement_id']}) falls within a looser 15% "
                        f"amount band and the date window -- a lower-confidence fallback for "
                        f"transactions where both the reference AND the fee appear off."),
            )

        # 5. nothing found
        return MatchResult(
            transaction_id=txn_id, order_id=order_id,
            method="UNMATCHED", confidence=0.0,
            invoice_id=self.invoices_by_order.get(order_id, {}).get("invoice_id"),
            reason=(f"No settlement found with a matching reference, a unique order-based "
                    f"identification, or a numeric fingerprint within tolerance. "
                    f"{len(available_settlements)} settlements were checked."),
        )

    def _settlement_by_id(self, settlement_id):
        for s in self.settlements:
            if s["settlement_id"] == settlement_id:
                return s
        return None

    def classify_exceptions_for_match(self, payment: dict, match: MatchResult) -> list:
        """Given a matched (or unmatched) payment, deterministically produce
        zero or more Exception_ records. Pure rule-based -- no LLM."""
        exceptions = []
        txn_id = payment["transaction_id"]
        amount = _to_decimal(payment["amount"])
        currency = payment["currency"]
        txn_date = _to_date(payment["transaction_date"])

        # MISSING_INVOICE is independent of settlement matching -- a payment
        # can reconcile perfectly against its settlement and still have no
        # invoice on file, so this check must not live inside the UNMATCHED
        # branch only.
        if match.invoice_id is None:
            exceptions.append(Exception_(
                exception_id=self._next_exception_id(), reference_id=txn_id,
                category="MISSING_INVOICE", affected_amount=str(amount),
                evidence={"payment": payment}, severity="MEDIUM", confidence=0.9,
                status="OPEN", created_at=datetime.utcnow().isoformat(),
            ))

        if match.method == "UNMATCHED":
            exceptions.append(Exception_(
                exception_id=self._next_exception_id(), reference_id=txn_id,
                category="MISSING_SETTLEMENT", affected_amount=str(amount),
                evidence={"payment": payment}, severity="HIGH", confidence=0.9,
                status="OPEN", created_at=datetime.utcnow().isoformat(),
            ))
            return exceptions

        settlement = self._settlement_by_id(match.settlement_id) if match.settlement_id else None
        if settlement is None:
            return exceptions

        settled = _to_decimal(settlement["settled_amount"])
        fee = _to_decimal(settlement["fee"])
        settlement_date_val = _to_date(settlement["settlement_date"])
        settlement_currency = settlement["currency"]

        expected_net = amount - fee
        amount_diff = (settled - expected_net).copy_abs()
        date_diff = abs((settlement_date_val - txn_date).days)
        expected_fee = (amount * STANDARD_FEE_RATE)
        fee_diff = (fee - expected_fee).copy_abs()

        def add(category, severity, confidence, extra_evidence=None):
            exceptions.append(Exception_(
                exception_id=self._next_exception_id(), reference_id=txn_id,
                category=category, affected_amount=str(amount_diff),
                evidence={
                    "payment_amount": str(amount), "settled_amount": str(settled),
                    "fee": str(fee), "expected_fee": str(expected_fee),
                    "transaction_date": str(txn_date), "settlement_date": str(settlement_date_val),
                    **(extra_evidence or {}),
                },
                severity=severity, confidence=confidence,
                status="OPEN", created_at=datetime.utcnow().isoformat(),
            ))

        if currency != settlement_currency:
            add("CURRENCY_MISMATCH", "HIGH", 0.95)

        if settled < expected_net * Decimal("0.9") and settled > 0:
            add("PARTIAL_SETTLEMENT", "HIGH", 0.85)
        elif amount_diff > AMOUNT_TOLERANCE and fee_diff <= (amount * FEE_TOLERANCE_RATE):
            # the gap is explained by the fee itself being roughly standard --
            # still flag, but lower severity, evidence shows the fee lines up
            add("AMOUNT_MISMATCH", "LOW", 0.5)
        elif amount_diff > AMOUNT_TOLERANCE:
            add("AMOUNT_MISMATCH", "HIGH", 0.9)

        if fee_diff > (amount * FEE_TOLERANCE_RATE):
            add("UNEXPECTED_FEE", "MEDIUM", 0.8)

        if date_diff > CLOSE_DATE_WINDOW_DAYS:
            add("DATE_MISMATCH", "LOW", 0.7)

        # Any resolution method other than MATCH_EXACT_ID / MATCH_ORDER_ID means
        # the settlement's own reference field did NOT plainly identify this
        # transaction -- we only found it via a money/date fingerprint. That is
        # itself evidence the reference field is unreliable and worth a human
        # glancing at, even though the underlying match is probably correct.
        if match.method == "PROBABLE_MATCH":
            add("INVALID_REFERENCE", "MEDIUM", round(1 - match.confidence, 2))
        elif match.method == "MATCH_AMOUNT_DATE":
            add("INVALID_REFERENCE", "LOW", 0.6)

        return exceptions

    def detect_duplicates(self) -> list:
        """Exact duplicate transaction rows, and possible-duplicate transactions
        (same order/customer/amount within a short window, different txn_id)."""
        exceptions = []
        seen = {}
        for p in self.payments:
            key = (p["transaction_id"], p["order_id"], p["amount"])
            seen.setdefault(key, []).append(p)
        for key, group in seen.items():
            if len(group) > 1:
                exceptions.append(Exception_(
                    exception_id=self._next_exception_id(), reference_id=key[0],
                    category="DUPLICATE_TRANSACTION", affected_amount=group[0]["amount"],
                    evidence={"duplicate_count": len(group), "rows": group},
                    severity="HIGH", confidence=0.97,
                    status="OPEN", created_at=datetime.utcnow().isoformat(),
                ))

        by_order_customer_amount = {}
        for p in self.payments:
            key = (p["order_id"], p["customer_id"], p["amount"])
            by_order_customer_amount.setdefault(key, []).append(p)
        for key, group in by_order_customer_amount.items():
            distinct_txns = {g["transaction_id"] for g in group}
            if len(distinct_txns) > 1:
                dates = sorted(_to_date(g["transaction_date"]) for g in group)
                if (dates[-1] - dates[0]).days <= 2:
                    exceptions.append(Exception_(
                        exception_id=self._next_exception_id(),
                        reference_id="/".join(sorted(distinct_txns)),
                        category="POSSIBLE_DUPLICATE", affected_amount=group[0]["amount"],
                        evidence={"transaction_ids": list(distinct_txns), "order_id": key[0]},
                        severity="MEDIUM", confidence=0.65,
                        status="OPEN", created_at=datetime.utcnow().isoformat(),
                    ))
        return exceptions

    def run(self):
        """Full pass: match every payment, classify exceptions, detect duplicates.

        Two-phase to prevent an already-claimed settlement from being grabbed
        by an unrelated transaction's fallback matching (see match_payment's
        docstring for why that matters):
          Phase 1: resolve every payment that has a genuine MATCH_EXACT_ID.
          Phase 2: for everything else, run fallback matching against only
                   the settlements NOT claimed in phase 1.

        Deduplication note: only rows that are FULL-RECORD duplicates (same
        transaction_id AND order_id AND amount) are collapsed here as
        "already seen" -- that's the genuine DUPLICATE_TRANSACTION case,
        which detect_duplicates() reports separately below. Deduplicating
        on transaction_id ALONE is wrong: the synthetic ID-corruption
        process can accidentally make two truly DIFFERENT transactions
        collide on the same corrupted ID (confirmed to happen ~9 times in
        the current dataset). Treating that as "already processed" would
        silently drop a real transaction from matching, classification,
        and every downstream count -- a correctness bug, not a cosmetic one.
        """
        matches = []
        all_exceptions = []
        seen_signatures = set()

        deduped_payments = []
        for p in self.payments:
            signature = (p["transaction_id"], p["order_id"], p["amount"])
            if signature in seen_signatures:
                continue  # exact full-record duplicates are handled by detect_duplicates()
            seen_signatures.add(signature)
            deduped_payments.append(p)

        claimed_settlement_ids = set()
        phase1_results = {}
        remaining_payments = []
        for p in deduped_payments:
            candidates = self.settlements_by_ref.get(p["transaction_id"])
            if candidates:
                settlement = candidates[0]
                claimed_settlement_ids.add(settlement["settlement_id"])
                phase1_results[p["transaction_id"]] = p
            else:
                remaining_payments.append(p)

        for p in deduped_payments:
            match = self.match_payment(p, claimed_settlement_ids)
            if p["transaction_id"] not in phase1_results and match.settlement_id:
                claimed_settlement_ids.add(match.settlement_id)
            matches.append(match)
            all_exceptions.extend(self.classify_exceptions_for_match(p, match))

        all_exceptions.extend(self.detect_duplicates())

        # invoices with no payment at all
        payment_orders = {p["order_id"] for p in self.payments}
        for inv in self.invoices:
            if inv["order_id"] not in payment_orders:
                all_exceptions.append(Exception_(
                    exception_id=self._next_exception_id(), reference_id=inv["order_id"],
                    category="MISSING_PAYMENT", affected_amount=inv["expected_amount"],
                    evidence={"invoice": inv}, severity="HIGH", confidence=0.9,
                    status="OPEN", created_at=datetime.utcnow().isoformat(),
                ))

        return matches, all_exceptions
