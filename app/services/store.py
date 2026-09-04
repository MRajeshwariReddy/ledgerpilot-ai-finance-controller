"""
LedgerStore: an in-memory index over one reconciliation run's output.

This exists so the AI Controller's tools (services/tools.py) have
something concrete, fast, and read-only to query -- get_transaction,
search_transactions, get_exception, etc. all resolve against this.

A real deployment would back this with SQLite/Postgres (the project
structure has room for it), but for a buildathon demo an in-memory
index rebuilt from one pipeline run is simpler, fully deterministic,
and just as easy to reason about in an interview.
"""
from dataclasses import asdict


class LedgerStore:
    def __init__(self, pipeline_result: dict):
        self.payments = {}
        self.settlements = {}
        self.invoices = {}
        self.matches_by_txn = {}
        self.exceptions_by_id = {}
        self.exceptions_by_ref = {}
        self.priority_by_exception_id = {}

        self._index(pipeline_result)

    def _index(self, result: dict):
        # payments/settlements/invoices come from the DQ-cleaned records that
        # fed the reconciliation engine
        engine_inputs = result.get("engine_inputs", {})
        for p in engine_inputs.get("payments", []):
            self.payments[p["transaction_id"]] = p
        for s in engine_inputs.get("settlements", []):
            self.settlements[s["settlement_id"]] = s
        for inv in engine_inputs.get("invoices", []):
            self.invoices[inv["order_id"]] = inv

        for m in result["matches"]:
            self.matches_by_txn[m.transaction_id] = m

        for exc, priority in result["scored_exceptions"]:
            self.exceptions_by_id[exc.exception_id] = exc
            self.priority_by_exception_id[exc.exception_id] = priority
            self.exceptions_by_ref.setdefault(exc.reference_id, []).append(exc.exception_id)

    # ---- lookups used by the tool layer ----

    def get_transaction(self, transaction_id: str):
        return self.payments.get(transaction_id.strip().upper())

    def get_settlement_by_id(self, settlement_id: str):
        return self.settlements.get(settlement_id.strip().upper())

    def get_invoice_by_order(self, order_id: str):
        return self.invoices.get(order_id.strip().upper())

    def get_match(self, transaction_id: str):
        return self.matches_by_txn.get(transaction_id.strip().upper())

    def get_exception(self, exception_id: str):
        return self.exceptions_by_id.get(exception_id)

    def get_exceptions_for_reference(self, reference_id: str):
        ids = self.exceptions_by_ref.get(reference_id, [])
        return [self.exceptions_by_id[i] for i in ids]

    def all_exceptions(self):
        return list(self.exceptions_by_id.values())

    def search_transactions(self, customer_id=None, order_id=None, min_amount=None, max_amount=None):
        results = []
        for p in self.payments.values():
            if customer_id and p["customer_id"] != customer_id.strip().upper():
                continue
            if order_id and p["order_id"] != order_id.strip().upper():
                continue
            if min_amount is not None and float(p["amount"]) < min_amount:
                continue
            if max_amount is not None and float(p["amount"]) > max_amount:
                continue
            results.append(p)
        return results
