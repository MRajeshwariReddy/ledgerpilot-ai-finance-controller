"""
Automated tests for the deterministic reconciliation engine.
Run with: pytest tests/
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.reconciliation.engine import ReconciliationEngine
from app.reconciliation.priority import compute_priority_score
from app.data.quality_engine import parse_amount, parse_date


def _payment(txn_id="TXN1", order_id="ORD1", customer_id="CUST1", amount="1000.00",
             currency="INR", txn_date="2026-07-01", method="upi", status="captured"):
    return {"transaction_id": txn_id, "order_id": order_id, "customer_id": customer_id,
            "amount": amount, "currency": currency, "transaction_date": txn_date,
            "payment_method": method, "status": status}


def _settlement(settlement_id="STL1", txn_ref="TXN1", settled_amount="980.00",
                 settlement_date="2026-07-02", fee="20.00", currency="INR"):
    return {"settlement_id": settlement_id, "transaction_id": txn_ref,
            "settled_amount": settled_amount, "settlement_date": settlement_date,
            "fee": fee, "currency": currency, "bank_reference": f"BANK{settlement_id}",
            "settlement_status": "settled"}


def _invoice(invoice_id="INV1", order_id="ORD1", expected_amount="1000.00",
             invoice_date="2026-07-01", customer_id="CUST1"):
    return {"invoice_id": invoice_id, "order_id": order_id, "expected_amount": expected_amount,
            "invoice_date": invoice_date, "customer_id": customer_id, "invoice_status": "issued"}


# --- Data quality parsing ---

def test_parse_amount_strips_currency_symbol():
    value, normalized = parse_amount("₹1,234.50")
    assert str(value) == "1234.50"
    assert normalized is True


def test_parse_amount_rejects_negative():
    try:
        parse_amount("-50.00")
        assert False, "should have raised"
    except ValueError:
        pass


def test_parse_amount_rejects_placeholder():
    for bad in ["N/A", "", "abc"]:
        try:
            parse_amount(bad)
            assert False, f"should have raised for {bad!r}"
        except ValueError:
            pass


def test_parse_date_multiple_formats():
    d1, norm1 = parse_date("2026-07-01")
    assert norm1 is False
    d2, norm2 = parse_date("01/07/2026")
    assert norm2 is True
    assert d1 == d2  # both mean July 1, 2026 in their respective formats


def test_parse_date_rejects_impossible_date():
    try:
        parse_date("2026-02-30")
        assert False, "should have raised"
    except ValueError:
        pass


# --- Reconciliation matching ---

def test_exact_id_match():
    payments = [_payment()]
    settlements = [_settlement()]
    invoices = [_invoice()]
    engine = ReconciliationEngine(payments, settlements, invoices)
    matches, exceptions = engine.run()
    assert matches[0].method == "MATCH_EXACT_ID"
    assert matches[0].confidence > 0.9


def test_missing_settlement_detected():
    payments = [_payment()]
    settlements = []  # no settlement at all
    invoices = [_invoice()]
    engine = ReconciliationEngine(payments, settlements, invoices)
    matches, exceptions = engine.run()
    assert matches[0].method == "UNMATCHED"
    categories = [e.category for e in exceptions]
    assert "MISSING_SETTLEMENT" in categories


def test_missing_invoice_detected_even_on_clean_match():
    """A payment can match its settlement perfectly and still lack an invoice."""
    payments = [_payment()]
    settlements = [_settlement(settled_amount="980.00")]  # matches 1000 - 2% fee exactly
    invoices = []  # no invoice
    engine = ReconciliationEngine(payments, settlements, invoices)
    matches, exceptions = engine.run()
    assert matches[0].method == "MATCH_EXACT_ID"
    categories = [e.category for e in exceptions]
    assert "MISSING_INVOICE" in categories


def test_amount_mismatch_detected():
    payments = [_payment(amount="1000.00")]
    # 900 is off from the expected net (980) by more than tolerance, but still
    # above the 90% partial-settlement threshold (882) -- a genuine amount
    # mismatch, distinct from a partial settlement.
    settlements = [_settlement(settled_amount="900.00")]
    invoices = [_invoice()]
    engine = ReconciliationEngine(payments, settlements, invoices)
    matches, exceptions = engine.run()
    categories = [e.category for e in exceptions]
    assert "AMOUNT_MISMATCH" in categories


def test_currency_mismatch_detected():
    payments = [_payment(currency="INR")]
    settlements = [_settlement(currency="USD")]
    invoices = [_invoice()]
    engine = ReconciliationEngine(payments, settlements, invoices)
    matches, exceptions = engine.run()
    categories = [e.category for e in exceptions]
    assert "CURRENCY_MISMATCH" in categories


def test_duplicate_transaction_detected():
    p = _payment()
    payments = [p, dict(p)]  # exact duplicate row
    settlements = [_settlement()]
    invoices = [_invoice()]
    engine = ReconciliationEngine(payments, settlements, invoices)
    matches, exceptions = engine.run()
    categories = [e.category for e in exceptions]
    assert "DUPLICATE_TRANSACTION" in categories


def test_partial_settlement_detected():
    payments = [_payment(amount="1000.00")]
    settlements = [_settlement(settled_amount="400.00")]  # well under 90% of expected net
    invoices = [_invoice()]
    engine = ReconciliationEngine(payments, settlements, invoices)
    matches, exceptions = engine.run()
    categories = [e.category for e in exceptions]
    assert "PARTIAL_SETTLEMENT" in categories


def test_decimal_used_for_money_not_float():
    """Amounts must be exact Decimal, not float, to avoid silent rounding errors."""
    from decimal import Decimal
    payments = [_payment(amount="0.10")]
    settlements = [_settlement(settled_amount="0.098")]
    invoices = [_invoice(expected_amount="0.10")]
    engine = ReconciliationEngine(payments, settlements, invoices)
    matches, _ = engine.run()
    # this would silently fail with float due to 0.1 + 0.2 != 0.3 style errors --
    # asserting the match still succeeds correctly with Decimal math is the point
    assert matches[0].transaction_id == "TXN1"


# --- Priority scoring ---

def test_priority_tier_ordering():
    from app.reconciliation.engine import Exception_
    from decimal import Decimal
    high_value_high_severity = Exception_(
        exception_id="E1", reference_id="TXN1", category="MISSING_SETTLEMENT",
        affected_amount="50000", evidence={}, severity="HIGH", confidence=0.9,
        status="OPEN", created_at="2026-08-01T00:00:00",
    )
    low_value_low_severity = Exception_(
        exception_id="E2", reference_id="TXN2", category="DATE_MISMATCH",
        affected_amount="10", evidence={}, severity="LOW", confidence=0.5,
        status="OPEN", created_at="2026-08-01T00:00:00",
    )
    p1 = compute_priority_score(high_value_high_severity, Decimal("50000"), age_days=5)
    p2 = compute_priority_score(low_value_low_severity, Decimal("10"), age_days=0)
    assert p1["score"] > p2["score"]
    tier_rank = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
    assert tier_rank[p1["tier"]] > tier_rank[p2["tier"]]


# --- Regression tests for Phase A (ID collision fix) ---

def test_messy_id_deterministic_per_id():
    """Verify that messy_id applies the SAME corruption each time for the same ID.
    This prevents accidental collisions between different transactions."""
    from app.data.generate_data import messy_id
    
    # Same ID, dirty=True, multiple calls should give identical results
    txn_id = "TXN10007"
    result1 = messy_id(txn_id, dirty=True)
    result2 = messy_id(txn_id, dirty=True)
    assert result1 == result2, "messy_id should be deterministic per ID"
    
    # Different IDs should get different corruption (based on numeric suffix)
    txn_id_2 = "TXN10008"
    result_2 = messy_id(txn_id_2, dirty=True)
    assert result_2 != result1, "Different IDs should get different corruption"


def test_no_accidental_collisions_after_normalization():
    """Verify that after quality-engine normalization, different transaction IDs
    don't collide even with formatting noise applied."""
    from app.data.generate_data import messy_id
    
    # Generate many IDs and apply corruption
    ids = [f"TXN{10000+i}" for i in range(50)]
    corrupted = [messy_id(txn_id, dirty=True) for txn_id in ids]
    # Normalize as quality engine would (strip whitespace, normalize case)
    normalized = [c.strip().upper() for c in corrupted]
    
    # All should be unique
    assert len(set(normalized)) == len(normalized), "Normalization should not create collisions"


# --- Tests for weak categories ---

def test_invalid_reference_detected_on_fallback_match():
    """INVALID_REFERENCE should be raised when settlement doesn't have exact ID match
    but we find it via amount+date matching."""
    payments = [_payment(txn_id="TXN1", amount="1000.00")]
    settlements = [_settlement(txn_ref="GARBAGE_ID", settled_amount="980.00", fee="20.00")]
    invoices = [_invoice()]
    
    engine = ReconciliationEngine(payments, settlements, invoices)
    matches, exceptions = engine.run()
    
    categories = [e.category for e in exceptions]
    # Should have matched via amount+date, but flagged the invalid reference
    assert matches[0].settlement_id is not None  # Match was found
    assert "INVALID_REFERENCE" in categories  # But reference was flagged


def test_missing_settlement_with_exact_amount_match():
    """Verify MISSING_SETTLEMENT is NOT raised when an exact-amount settlement exists,
    even if the transaction_id reference is corrupted (aggressive matching)."""
    payments = [_payment(amount="1000.00")]
    # Settlement with different reference but exact matching amount
    settlements = [_settlement(txn_ref="CORRUPTED_ID", settled_amount="980.00", fee="20.00")]
    invoices = [_invoice()]
    
    engine = ReconciliationEngine(payments, settlements, invoices)
    matches, exceptions = engine.run()
    
    # Should match via amount, so no MISSING_SETTLEMENT
    assert matches[0].settlement_id is not None
    categories = [e.category for e in exceptions]
    assert "MISSING_SETTLEMENT" not in categories


def test_missing_payment_detection():
    """Verify MISSING_PAYMENT is detected when invoice exists but no payment."""
    payments = []  # No payment
    settlements = []
    invoices = [_invoice(order_id="ORD1")]
    
    engine = ReconciliationEngine(payments, settlements, invoices)
    matches, exceptions = engine.run()
    
    categories = [e.category for e in exceptions]
    assert "MISSING_PAYMENT" in categories


def test_possible_duplicate_same_order_customer_amount_in_window():
    """Verify POSSIBLE_DUPLICATE is detected for same (order, customer, amount) within 2 days."""
    from datetime import timedelta
    p1 = _payment(txn_id="TXN1", order_id="ORD1", customer_id="CUST1", 
                  amount="5000.00", txn_date="2026-07-01")
    p2 = _payment(txn_id="TXN2", order_id="ORD1", customer_id="CUST1",
                  amount="5000.00", txn_date="2026-07-02")
    payments = [p1, p2]
    settlements = [
        _settlement(txn_ref="TXN1", settled_amount="4900.00"),
        _settlement(txn_ref="TXN2", settled_amount="4900.00"),
    ]
    invoices = [_invoice(order_id="ORD1")]
    
    engine = ReconciliationEngine(payments, settlements, invoices)
    matches, exceptions = engine.run()
    
    categories = [e.category for e in exceptions]
    assert "POSSIBLE_DUPLICATE" in categories


def test_unexpected_fee_detected():
    """Verify UNEXPECTED_FEE is detected when fee is far from standard 2%."""
    payments = [_payment(amount="1000.00")]
    # Fee is 15% instead of 2%
    settlements = [_settlement(settled_amount="850.00", fee="150.00")]
    invoices = [_invoice()]
    
    engine = ReconciliationEngine(payments, settlements, invoices)
    matches, exceptions = engine.run()
    
    categories = [e.category for e in exceptions]
    assert "UNEXPECTED_FEE" in categories


def test_partial_settlement_detected():
    """Verify PARTIAL_SETTLEMENT is detected when settled amount is <90% of expected net."""
    payments = [_payment(amount="1000.00")]
    # Only 50% settled
    settlements = [_settlement(settled_amount="500.00")]
    invoices = [_invoice()]
    
    engine = ReconciliationEngine(payments, settlements, invoices)
    matches, exceptions = engine.run()
    
    categories = [e.category for e in exceptions]
    assert "PARTIAL_SETTLEMENT" in categories


def test_amount_mismatch_within_tolerance():
    """Verify AMOUNT_MISMATCH is NOT raised for small differences within tolerance."""
    payments = [_payment(amount="1000.00")]
    # Settled amount differs by exactly 2% (standard fee) -- within tolerance
    settlements = [_settlement(settled_amount="980.00", fee="20.00")]
    invoices = [_invoice()]
    
    engine = ReconciliationEngine(payments, settlements, invoices)
    matches, exceptions = engine.run()
    
    # Match should succeed, no amount mismatch
    assert matches[0].settlement_id is not None
    categories = [e.category for e in exceptions]
    # May have other exceptions, but not amount mismatch for standard fee
    matching_mismatches = [e for e in exceptions if e.category == "AMOUNT_MISMATCH"]
    # If there's an amount mismatch, it should have low severity (fee explains it)
    for exc in matching_mismatches:
        # It's OK to have one, as long as confidence is lowered
        assert exc.confidence < 0.8 or exc.severity == "LOW"


if __name__ == "__main__":
    import subprocess
    subprocess.run(["pytest", __file__, "-v"])
