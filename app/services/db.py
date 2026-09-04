"""
SQLite persistence for LedgerPilot.

The deterministic pipeline (data quality -> reconciliation -> priority)
runs in plain Python/pandas and produces in-memory results. This module
persists those results into SQLite so that:
  - the AI Controller's tools (app/services/tools.py) can query specific
    records without re-running the whole pipeline per question
  - the dashboard can page/filter/sort without recomputation
  - case resolutions (human decisions) have somewhere durable to live

This module contains NO reconciliation logic itself -- it only stores and
retrieves what the deterministic engine already decided.
"""
import sqlite3
import json
import os
from decimal import Decimal
from dataclasses import asdict

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "generated", "ledgerpilot.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS payments (
    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id TEXT, order_id TEXT, customer_id TEXT, amount TEXT, currency TEXT,
    transaction_date TEXT, payment_method TEXT, status TEXT
);
CREATE INDEX IF NOT EXISTS idx_payments_txn ON payments(transaction_id);
CREATE TABLE IF NOT EXISTS settlements (
    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    settlement_id TEXT, transaction_id TEXT, settled_amount TEXT, settlement_date TEXT,
    fee TEXT, currency TEXT, bank_reference TEXT, settlement_status TEXT
);
CREATE INDEX IF NOT EXISTS idx_settlements_id ON settlements(settlement_id);
CREATE INDEX IF NOT EXISTS idx_settlements_txn ON settlements(transaction_id);
CREATE TABLE IF NOT EXISTS invoices (
    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id TEXT, order_id TEXT, expected_amount TEXT, invoice_date TEXT,
    customer_id TEXT, invoice_status TEXT
);
CREATE INDEX IF NOT EXISTS idx_invoices_order ON invoices(order_id);
CREATE TABLE IF NOT EXISTS matches (
    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id TEXT, order_id TEXT, method TEXT, confidence REAL,
    settlement_id TEXT, invoice_id TEXT, reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_matches_txn ON matches(transaction_id);
CREATE TABLE IF NOT EXISTS exceptions (
    exception_id TEXT PRIMARY KEY,
    reference_id TEXT, category TEXT, affected_amount TEXT,
    evidence_json TEXT, severity TEXT, confidence REAL,
    status TEXT, created_at TEXT,
    priority_score REAL, priority_tier TEXT, priority_breakdown_json TEXT
);
CREATE TABLE IF NOT EXISTS case_resolutions (
    resolution_id INTEGER PRIMARY KEY AUTOINCREMENT,
    exception_id TEXT, exception_category TEXT, exception_attributes_json TEXT,
    resolution TEXT, evidence_json TEXT, reviewer TEXT, decision TEXT,
    reason TEXT, ai_recommendation TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    exception_id TEXT, actor TEXT, action TEXT,
    ai_recommendation TEXT, evidence_json TEXT,
    final_decision TEXT, reason TEXT, created_at TEXT
);
"""


def _json_default(o):
    if isinstance(o, Decimal):
        return str(o)
    return str(o)


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def reset_and_load(payments, settlements, invoices, matches, exceptions, scored_exceptions):
    """Wipes and reloads the DB from a fresh pipeline run. Called by run.py / evaluation."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    conn = get_connection()
    init_schema(conn)

    conn.executemany(
        "INSERT INTO payments (transaction_id, order_id, customer_id, amount, currency, "
        "transaction_date, payment_method, status) VALUES (?,?,?,?,?,?,?,?)",
        [(p["transaction_id"], p["order_id"], p["customer_id"], p["amount"], p["currency"],
          p["transaction_date"], p["payment_method"], p["status"]) for p in payments],
    )
    conn.executemany(
        "INSERT INTO settlements (settlement_id, transaction_id, settled_amount, settlement_date, "
        "fee, currency, bank_reference, settlement_status) VALUES (?,?,?,?,?,?,?,?)",
        [(s["settlement_id"], s["transaction_id"], s["settled_amount"], s["settlement_date"],
          s["fee"], s["currency"], s["bank_reference"], s["settlement_status"]) for s in settlements],
    )
    conn.executemany(
        "INSERT INTO invoices (invoice_id, order_id, expected_amount, invoice_date, "
        "customer_id, invoice_status) VALUES (?,?,?,?,?,?)",
        [(inv["invoice_id"], inv["order_id"], inv["expected_amount"], inv["invoice_date"],
          inv["customer_id"], inv["invoice_status"]) for inv in invoices],
    )
    conn.executemany(
        "INSERT INTO matches (transaction_id, order_id, method, confidence, settlement_id, "
        "invoice_id, reason) VALUES (?,?,?,?,?,?,?)",
        [(m.transaction_id, m.order_id, m.method, m.confidence, m.settlement_id, m.invoice_id,
          getattr(m, "reason", None))
         for m in matches],
    )

    priority_by_exc_id = {}
    exc_rows = []
    for exc, priority in scored_exceptions:
        exc_rows.append((
            exc.exception_id, exc.reference_id, exc.category, exc.affected_amount,
            json.dumps(exc.evidence, default=_json_default), exc.severity, exc.confidence,
            exc.status, exc.created_at,
            priority["score"], priority["tier"], json.dumps(priority["breakdown"]),
        ))
    conn.executemany("INSERT OR REPLACE INTO exceptions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", exc_rows)

    conn.commit()
    conn.close()
