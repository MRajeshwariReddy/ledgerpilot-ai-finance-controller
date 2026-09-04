"""
Deterministic exception prioritization.

Priority is a plain weighted formula over known factors -- never
LLM-assigned. The AI Controller can EXPLAIN why something is CRITICAL,
but it does not decide that it is.
"""
from decimal import Decimal
from datetime import datetime, date

SEVERITY_WEIGHT = {"HIGH": 3.0, "MEDIUM": 2.0, "LOW": 1.0}
CATEGORY_RISK_WEIGHT = {
    "MISSING_SETTLEMENT": 1.5,
    "MISSING_PAYMENT": 1.4,
    "MISSING_INVOICE": 1.0,
    "AMOUNT_MISMATCH": 1.3,
    "CURRENCY_MISMATCH": 1.2,
    "DUPLICATE_TRANSACTION": 1.4,
    "POSSIBLE_DUPLICATE": 0.9,
    "DATE_MISMATCH": 0.6,
    "UNEXPECTED_FEE": 1.0,
    "INVALID_REFERENCE": 1.1,
    "PARTIAL_SETTLEMENT": 1.3,
    "UNRESOLVED": 0.8,
}


def compute_priority_score(exception, amount_value: Decimal, age_days: int,
                            repeat_count: int = 1) -> dict:
    """
    Returns {"score": float, "tier": "CRITICAL"|"HIGH"|"MEDIUM"|"LOW", "breakdown": {...}}

    score = amount_component + severity_component + age_component
            + category_risk_component + repeat_component
    all normalized to comparable ranges so no single factor dominates by
    accident (e.g. a ₹10 exception doesn't get buried by amount alone).
    """
    amount_component = min(float(amount_value) / 10000.0, 5.0)  # caps at 5.0 for very large amounts
    severity_component = SEVERITY_WEIGHT.get(exception.severity, 1.0)
    age_component = min(age_days / 3.0, 4.0)  # older exceptions escalate, caps at 4.0
    category_component = CATEGORY_RISK_WEIGHT.get(exception.category, 1.0)
    repeat_component = min((repeat_count - 1) * 0.5, 2.0)
    confidence_component = exception.confidence  # low-confidence exceptions get a slight discount

    raw_score = (
        amount_component + severity_component + age_component
        + category_component + repeat_component
    ) * (0.5 + 0.5 * confidence_component)

    if raw_score >= 8.0:
        tier = "CRITICAL"
    elif raw_score >= 5.5:
        tier = "HIGH"
    elif raw_score >= 3.0:
        tier = "MEDIUM"
    else:
        tier = "LOW"

    return {
        "score": round(raw_score, 2),
        "tier": tier,
        "breakdown": {
            "amount_component": round(amount_component, 2),
            "severity_component": round(severity_component, 2),
            "age_component": round(age_component, 2),
            "category_component": round(category_component, 2),
            "repeat_component": round(repeat_component, 2),
            "confidence_multiplier": round(0.5 + 0.5 * confidence_component, 2),
        },
    }
