"""
Controlled, read-only tools for the AI Controller.

Every function here is the ONLY way the AI Controller is allowed to touch
the database. All are read-only by construction (SELECT only) except
`record_case_resolution`, which is invoked exclusively by the human
approval flow (app/audit/), never by the AI directly -- the Controller can
recommend a resolution, but only a human action calls this.

`simulate_resolution` computes a projection and writes NOTHING.
"""
import json
import sqlite3
from decimal import Decimal
from app.services.db import get_connection


def _row_to_dict(row):
    return dict(row) if row else None


def get_transaction(transaction_id: str) -> dict:
    conn = get_connection()
    row = conn.execute("SELECT * FROM payments WHERE transaction_id = ?", (transaction_id,)).fetchone()
    conn.close()
    return _row_to_dict(row)


def get_invoice(order_id: str) -> dict:
    conn = get_connection()
    row = conn.execute("SELECT * FROM invoices WHERE order_id = ?", (order_id,)).fetchone()
    conn.close()
    return _row_to_dict(row)


def get_settlement(settlement_id: str) -> dict:
    conn = get_connection()
    row = conn.execute("SELECT * FROM settlements WHERE settlement_id = ?", (settlement_id,)).fetchone()
    conn.close()
    return _row_to_dict(row)


def search_transactions(customer_id: str = None, order_id: str = None,
                         min_amount: float = None, max_amount: float = None,
                         limit: int = 20) -> list:
    conn = get_connection()
    clauses, params = [], []
    if customer_id:
        clauses.append("customer_id = ?"); params.append(customer_id)
    if order_id:
        clauses.append("order_id = ?"); params.append(order_id)
    if min_amount is not None:
        clauses.append("CAST(amount AS REAL) >= ?"); params.append(min_amount)
    if max_amount is not None:
        clauses.append("CAST(amount AS REAL) <= ?"); params.append(max_amount)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(f"SELECT * FROM payments {where} LIMIT ?", (*params, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def find_matching_candidates(transaction_id: str, tolerance_pct: float = 15.0) -> list:
    """Read-only re-derivation of plausible settlement candidates for a
    transaction, for the AI to show its work when explaining a match/mismatch."""
    conn = get_connection()
    payment = conn.execute("SELECT * FROM payments WHERE transaction_id = ?", (transaction_id,)).fetchone()
    if payment is None:
        conn.close()
        return []
    amount = float(payment["amount"])
    lo, hi = amount * (1 - tolerance_pct / 100), amount * (1 + tolerance_pct / 100)
    rows = conn.execute(
        "SELECT * FROM settlements WHERE CAST(settled_amount AS REAL) BETWEEN ? AND ?",
        (lo, hi),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_exception(exception_id: str) -> dict:
    conn = get_connection()
    row = conn.execute("SELECT * FROM exceptions WHERE exception_id = ?", (exception_id,)).fetchone()
    conn.close()
    if row is None:
        return None
    d = dict(row)
    d["evidence"] = json.loads(d.pop("evidence_json"))
    d["priority_breakdown"] = json.loads(d.pop("priority_breakdown_json"))
    return d


def get_previous_resolutions(category: str = None, current_amount: str = None, limit: int = 5) -> list:
    """
    Retrieve similar previously resolved cases using exception type and amount similarity.
    
    Previous cases are EVIDENCE ONLY for the AI investigator, never automatic authority.
    The AI must not auto-apply a previous resolution to a new case.
    
    Args:
        category: Exception category to match (e.g., 'AMOUNT_MISMATCH')
        current_amount: Amount of current exception (for similarity scoring)
        limit: Maximum number of results to return
    
    Returns:
        List of dicts with fields: exception_id, exception_category, resolution, decision,
        reviewer, reason, created_at, similarity_score (0-100, 100=identical amount)
    """
    from decimal import Decimal, InvalidOperation
    
    conn = get_connection()
    if category:
        rows = conn.execute(
            "SELECT * FROM case_resolutions WHERE exception_category = ? "
            "ORDER BY created_at DESC LIMIT ?", (category, limit * 2),  # Get extra, will filter by score
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM case_resolutions ORDER BY created_at DESC LIMIT ?", (limit * 2,),
        ).fetchall()
    conn.close()
    
    results = []
    for row in rows:
        row_dict = dict(row)
        
        # Calculate similarity score if current_amount provided
        similarity_score = None
        if current_amount:
            try:
                # Parse amounts as Decimal for precise calculation
                current = Decimal(str(current_amount))
                
                # Try to extract amount from exception_attributes_json
                import json
                if row_dict.get('exception_attributes_json'):
                    try:
                        attrs = json.loads(row_dict['exception_attributes_json'])
                        if isinstance(attrs, str):
                            attrs = json.loads(attrs)
                        if not isinstance(attrs, dict):
                            attrs = {}
                        prev_amount_str = attrs.get('affected_amount')
                        if prev_amount_str:
                            prev = Decimal(str(prev_amount_str))
                            # Calculate similarity as percentage (100 = exact match, 0 = very different)
                            # Use max to avoid division issues
                            max_amt = max(current, prev)
                            if max_amt > 0:
                                diff = abs(current - prev)
                                pct_diff = (diff / max_amt) * 100
                                similarity_score = max(0, 100 - pct_diff)  # 100 = exact, 0 = 100%+ difference
                    except (json.JSONDecodeError, ValueError, InvalidOperation):
                        pass
            except (ValueError, InvalidOperation):
                pass
        
        # Always include the row, but with similarity score if calculated
        if similarity_score is not None:
            row_dict['similarity_score'] = round(similarity_score, 1)
        
        results.append(row_dict)
    
    # Sort by similarity score (descending) if available, keeping order otherwise
    if current_amount and any('similarity_score' in r for r in results):
        results.sort(key=lambda r: r.get('similarity_score', 0), reverse=True)
    
    # Return only up to limit
    return results[:limit]


def calculate_reconciliation_summary() -> dict:
    conn = get_connection()
    total_payments = conn.execute("SELECT COUNT(*) c FROM payments").fetchone()["c"]
    total_exceptions = conn.execute("SELECT COUNT(*) c FROM exceptions WHERE status = 'OPEN'").fetchone()["c"]
    reconciled = conn.execute(
        "SELECT COUNT(*) c FROM matches WHERE method = 'MATCH_EXACT_ID'"
    ).fetchone()["c"]
    unresolved_value = conn.execute(
        "SELECT SUM(CAST(affected_amount AS REAL)) v FROM exceptions WHERE status = 'OPEN'"
    ).fetchone()["v"] or 0.0
    conn.close()
    reconciliation_rate = round(100 * reconciled / total_payments, 2) if total_payments else 0.0
    return {
        "total_transactions": total_payments,
        "cleanly_reconciled": reconciled,
        "reconciliation_rate_pct": reconciliation_rate,
        "total_exceptions": total_exceptions,
        "total_unresolved_value": round(unresolved_value, 2),
    }


def get_exception_statistics() -> dict:
    conn = get_connection()
    by_category = conn.execute(
        "SELECT category, COUNT(*) c, SUM(CAST(affected_amount AS REAL)) v "
        "FROM exceptions GROUP BY category ORDER BY v DESC"
    ).fetchall()
    by_severity = conn.execute(
        "SELECT severity, COUNT(*) c FROM exceptions GROUP BY severity"
    ).fetchall()
    by_tier = conn.execute(
        "SELECT priority_tier, COUNT(*) c FROM exceptions WHERE status = 'OPEN' GROUP BY priority_tier"
    ).fetchall()
    conn.close()
    return {
        "by_category": [dict(r) for r in by_category],
        "by_severity": [dict(r) for r in by_severity],
        "by_priority_tier": [dict(r) for r in by_tier],
    }


def simulate_resolution(exception_ids: list) -> dict:
    """
    Projects the outcome of resolving a batch of exceptions WITHOUT writing
    anything. Used by simulation mode before a human approves a batch.
    """
    conn = get_connection()
    placeholders = ",".join("?" * len(exception_ids))
    rows = conn.execute(
        f"SELECT * FROM exceptions WHERE exception_id IN ({placeholders})", exception_ids,
    ).fetchall()
    remaining_high_risk = conn.execute(
        f"SELECT COUNT(*) c FROM exceptions WHERE priority_tier IN ('CRITICAL','HIGH') "
        f"AND exception_id NOT IN ({placeholders})", exception_ids,
    ).fetchone()["c"]
    conn.close()

    rows = [dict(r) for r in rows]
    total_value = sum(float(r["affected_amount"]) for r in rows)
    total_open = calculate_reconciliation_summary()["total_exceptions"]

    return {
        "records_affected": len(rows),
        "total_monetary_value": round(total_value, 2),
        "expected_new_open_count": max(total_open - len(rows), 0),
        "high_risk_cases_remaining": remaining_high_risk,
        "estimated_review_minutes_saved": len(rows) * 4,  # rough estimate, clearly labeled as such by caller
    }


def record_case_resolution(exception_id: str, exception_category: str, exception_attributes: dict,
                            resolution: str, evidence: dict, reviewer: str, decision: str,
                            reason: str, ai_recommendation: str = None):
    """
    NOT an AI tool. Called only by the human approval flow
    (app/audit/approval.py) after a person makes a decision. Writes the
    case memory record AND the audit log entry in one transaction.
    """
    from datetime import datetime
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    conn.execute(
        "INSERT INTO case_resolutions "
        "(exception_id, exception_category, exception_attributes_json, resolution, "
        "evidence_json, reviewer, decision, reason, ai_recommendation, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (exception_id, exception_category, json.dumps(exception_attributes, default=str),
         resolution, json.dumps(evidence, default=str), reviewer, decision, reason,
         ai_recommendation, now),
    )
    conn.execute(
        "INSERT INTO audit_log "
        "(exception_id, actor, action, ai_recommendation, evidence_json, final_decision, reason, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (exception_id, reviewer, decision, ai_recommendation, json.dumps(evidence, default=str),
         decision, reason, now),
    )
    conn.execute("UPDATE exceptions SET status = ? WHERE exception_id = ?",
                 ("RESOLVED" if decision == "APPROVE" else "OPEN", exception_id))
    conn.commit()
    conn.close()
