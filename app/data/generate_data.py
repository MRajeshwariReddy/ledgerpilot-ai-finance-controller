"""
Generates realistic, deliberately messy synthetic data for LedgerPilot's
three sources: payment transactions, settlement/bank records, and
merchant invoices.

Two layers of "problems" are injected, on purpose, and kept SEPARATE:

  1. FORMATTING dirt (for the Data Quality Engine to catch and normalize):
     inconsistent date formats, currency symbols mixed into amounts,
     whitespace/casing noise, blank fields, malformed IDs, duplicate rows,
     impossible dates, negative/zero amounts.

  2. RECONCILIATION anomalies (for the reconciliation engine + evaluation
     framework): amount mismatches, missing settlement/payment/invoice,
     duplicate transactions, possible duplicates, date mismatches,
     currency mismatches, unexpected fees, invalid references, partial
     settlements.

Every injected reconciliation anomaly is recorded in
`ground_truth_labels.csv` with its true category, so evaluation.py can
later compute real precision/recall against a KNOWN answer key rather
than trusting the system's own output.

Run standalone: python -m app.data.generate_data
"""
import csv
import random
import string
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
import os

random.seed(7)

N_ORDERS = 1100  # comfortably over the 1,000-record bar across 3 sources
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "generated")
os.makedirs(OUT_DIR, exist_ok=True)

CURRENCIES = ["INR", "INR", "INR", "INR", "USD"]  # mostly INR, occasional USD
PAYMENT_METHODS = ["card", "upi", "netbanking", "wallet"]
DATE_FORMATS_FOR_DIRT = ["%Y-%m-%d", "%d/%m/%Y", "%m-%d-%Y", "%d-%b-%Y"]

ground_truth = []  # rows: order_id, transaction_id, anomaly_type, detail, is_intentional_scenario
                   # is_intentional_scenario=True means this is a deliberately injected
                   # reconciliation scenario. False means it's data-quality noise.
scenario_manifest = []  # rows: order_id, transaction_id, scenario, has_formatting_noise
                         # one row per order -- the authoritative record of what the
                         # generator DID, independent of anything the engine later decides


def rand_amount(lo=100, hi=75000) -> Decimal:
    val = Decimal(random.uniform(lo, hi)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return val


def messy_date(d: date, dirty: bool) -> str:
    fmt = random.choice(DATE_FORMATS_FOR_DIRT) if dirty else "%Y-%m-%d"
    return d.strftime(fmt)


def messy_amount(amount: Decimal, dirty: bool) -> str:
    if not dirty:
        return str(amount)
    style = random.choice(["symbol", "commas", "suffix", "plain"])
    if style == "symbol":
        return f"₹{amount:,}"
    if style == "commas":
        return f"{amount:,}"
    if style == "suffix":
        return f"{amount} INR"
    return str(amount)


def messy_id(id_str: str, dirty: bool, unique_index: int = None) -> str:
    """Apply formatting noise to an ID while preserving uniqueness.
    
    CRITICAL: corruption must be DETERMINISTIC per ID, not random per call.
    If the same id_str gets corrupted multiple times, it must produce the same
    result. This prevents accidental collisions between different transactions
    that happen to get the same random corruption variant.
    
    Uses the numeric suffix of the ID as a seed to ensure collisions are
    impossible: different numeric suffixes → different corruption paths.
    """
    if not dirty:
        return id_str
    
    # Extract the numeric suffix to make corruption deterministic and unique per ID
    numeric_suffix = ""
    for c in reversed(id_str):
        if c.isdigit():
            numeric_suffix = c + numeric_suffix
        else:
            break
    
    # Use the numeric value to pick a deterministic corruption variant
    # This ensures TXN10007 always gets the same corruption, and TXN10008
    # always gets a different one, preventing collisions
    if numeric_suffix:
        variant_index = int(numeric_suffix) % 3
    else:
        variant_index = hash(id_str) % 3
    
    if variant_index == 0:
        # Add whitespace - preserves all characters
        return f"  {id_str}  "
    elif variant_index == 1:
        # Lowercase - deterministic per ID
        return id_str.lower()
    else:  # variant_index == 2
        # Casing mixed - deterministic: alternate characters based on position
        result = ""
        for i, c in enumerate(id_str):
            if c.isalpha():
                result += c.upper() if i % 2 == 0 else c.lower()
            else:
                result += c
        return result


payments, settlements, invoices = [], [], []

start_date = date(2026, 6, 1)

for i in range(1, N_ORDERS + 1):
    order_id = f"ORD{10000 + i}"
    txn_id = f"TXN{10000 + i}"
    customer_id = f"CUST{1000 + (i % 400)}"  # repeat customers, realistic
    currency = random.choice(CURRENCIES)
    base_amount = rand_amount()
    txn_date = start_date + timedelta(days=random.randint(0, 60))

    dirty_row = random.random() < 0.12  # ~12% of rows get formatting noise
    # order_id corruption must be COHERENT across sources -- the same real-world
    # order_id should come out the same way wherever it's referenced (a garbled
    # export doesn't re-garble differently per file). Deciding it once here and
    # reusing it avoids manufacturing fake MISSING_PAYMENT/MISSING_INVOICE
    # exceptions purely from independent per-source randomization.
    order_id_for_row = messy_id(order_id, dirty_row)

    # --- decide reconciliation scenario for this order ---
    r = random.random()
    scenario = "CLEAN"
    if r < 0.55:
        scenario = "CLEAN"
    elif r < 0.62:
        scenario = "AMOUNT_MISMATCH"
    elif r < 0.68:
        scenario = "MISSING_SETTLEMENT"
    elif r < 0.72:
        scenario = "MISSING_PAYMENT"
    elif r < 0.76:
        scenario = "MISSING_INVOICE"
    elif r < 0.80:
        scenario = "DUPLICATE_TRANSACTION"
    elif r < 0.84:
        scenario = "POSSIBLE_DUPLICATE"
    elif r < 0.89:
        scenario = "DATE_MISMATCH"
    elif r < 0.92:
        scenario = "CURRENCY_MISMATCH"
    elif r < 0.95:
        scenario = "UNEXPECTED_FEE"
    elif r < 0.97:
        scenario = "INVALID_REFERENCE"
    else:
        scenario = "PARTIAL_SETTLEMENT"

    scenario_manifest.append([order_id, txn_id, scenario, dirty_row])

    # --- Payment transaction ---
    payment_present = scenario != "MISSING_PAYMENT"
    if payment_present:
        payments.append({
            "transaction_id": messy_id(txn_id, dirty_row),
            "order_id": order_id_for_row,
            "customer_id": customer_id,
            "amount": messy_amount(base_amount, dirty_row),
            "currency": currency,
            "transaction_date": messy_date(txn_date, dirty_row),
            "payment_method": random.choice(PAYMENT_METHODS),
            "status": "captured",
        })
        if scenario == "DUPLICATE_TRANSACTION":
            # exact duplicate row -- a true ingestion duplicate
            payments.append(dict(payments[-1]))
            ground_truth.append([order_id, txn_id, "DUPLICATE_TRANSACTION",
                                  "Exact duplicate payment row ingested twice", "True"])

    # --- Settlement record ---
    standard_fee_rate = Decimal("0.02")
    fee = (base_amount * standard_fee_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    settled_amount = base_amount - fee
    settlement_date_val = txn_date + timedelta(days=random.randint(1, 3))
    settlement_currency = currency
    settlement_ref = txn_id

    settlement_present = scenario != "MISSING_SETTLEMENT"
    if scenario == "AMOUNT_MISMATCH":
        settled_amount = settled_amount - rand_amount(50, 500)
    elif scenario == "DATE_MISMATCH":
        settlement_date_val = txn_date + timedelta(days=random.randint(12, 25))
    elif scenario == "CURRENCY_MISMATCH":
        settlement_currency = "USD" if currency == "INR" else "INR"
    elif scenario == "UNEXPECTED_FEE":
        fee = (base_amount * Decimal("0.09")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        settled_amount = base_amount - fee
    elif scenario == "INVALID_REFERENCE":
        settlement_ref = "REF_" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    elif scenario == "PARTIAL_SETTLEMENT":
        settled_amount = (base_amount * Decimal("0.5")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    if settlement_present:
        settlements.append({
            "settlement_id": f"STL{10000 + i}",
            "transaction_id": messy_id(settlement_ref, dirty_row),
            "settled_amount": messy_amount(settled_amount, dirty_row),
            "settlement_date": messy_date(settlement_date_val, dirty_row),
            "fee": messy_amount(fee, dirty_row),
            "currency": settlement_currency,
            "bank_reference": f"BANKREF{20000 + i}",
            "settlement_status": "settled" if scenario != "PARTIAL_SETTLEMENT" else "partial",
        })
    else:
        ground_truth.append([order_id, txn_id, "MISSING_SETTLEMENT",
                              "No settlement record exists for this captured payment", "True"])

    if scenario in ("AMOUNT_MISMATCH", "DATE_MISMATCH", "CURRENCY_MISMATCH",
                    "UNEXPECTED_FEE", "INVALID_REFERENCE", "PARTIAL_SETTLEMENT"):
        ground_truth.append([order_id, txn_id, scenario, f"Injected {scenario} scenario", "True"])

    # --- Invoice record ---
    invoice_present = scenario != "MISSING_INVOICE"
    if invoice_present:
        invoices.append({
            "invoice_id": f"INV{10000 + i}",
            "order_id": order_id_for_row,
            "expected_amount": messy_amount(base_amount, dirty_row),
            "invoice_date": messy_date(txn_date - timedelta(days=random.randint(0, 2)), dirty_row),
            "customer_id": customer_id,
            "invoice_status": "issued",
        })
    else:
        ground_truth.append([order_id, txn_id, "MISSING_INVOICE",
                              "No invoice record exists for this order", "True"])

    if scenario == "MISSING_PAYMENT":
        ground_truth.append([order_id, txn_id, "MISSING_PAYMENT",
                              "Settlement/invoice exist but no payment transaction was ingested", "True"])

    if scenario == "POSSIBLE_DUPLICATE" and payment_present:
        # a SEPARATE transaction, same order/customer/amount, close date --
        # looks like a duplicate but has a different transaction_id
        dup_txn_id = f"TXN{10000 + i}B"
        payments.append({
            "transaction_id": dup_txn_id,
            "order_id": order_id,
            "customer_id": customer_id,
            "amount": str(base_amount),
            "currency": currency,
            "transaction_date": (txn_date + timedelta(days=1)).strftime("%Y-%m-%d"),
            "payment_method": random.choice(PAYMENT_METHODS),
            "status": "captured",
        })
        ground_truth.append([order_id, dup_txn_id, "POSSIBLE_DUPLICATE",
                              "Same order/customer/amount as another transaction within 1 day", "True"])

# --- inject a few pure data-quality problems unrelated to reconciliation scenarios ---
# blank/missing fields
for _ in range(15):
    row = random.choice(payments)
    row["payment_method"] = ""
# invalid amounts
for _ in range(10):
    row = random.choice(payments)
    row["amount"] = random.choice(["-500.00", "0", "N/A", "abc"])
# impossible dates
for _ in range(8):
    row = random.choice(settlements)
    row["settlement_date"] = random.choice(["2026-02-30", "1899-01-01", "2099-12-31"])
# fully duplicate rows (ingestion glitch, not the labeled DUPLICATE_TRANSACTION scenario)
for _ in range(6):
    row = random.choice(invoices)
    invoices.append(dict(row))


def write_csv(rows, path):
    if not rows:
        return
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


write_csv(payments, os.path.join(OUT_DIR, "payments_raw.csv"))
write_csv(settlements, os.path.join(OUT_DIR, "settlements_raw.csv"))
write_csv(invoices, os.path.join(OUT_DIR, "invoices_raw.csv"))

with open(os.path.join(OUT_DIR, "ground_truth_labels.csv"), "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["order_id", "transaction_id", "anomaly_type", "detail", "is_intentional_scenario"])
    writer.writerows(ground_truth)

with open(os.path.join(OUT_DIR, "scenario_manifest.csv"), "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["order_id", "transaction_id", "scenario", "is_dirty_formatting"])
    writer.writerows(scenario_manifest)

clean_count = sum(1 for row in scenario_manifest if row[2] == "CLEAN")
dirty_count = sum(1 for row in scenario_manifest if row[3])
anomaly_count = len(scenario_manifest) - clean_count

print(f"payments: {len(payments)} rows")
print(f"settlements: {len(settlements)} rows")
print(f"invoices: {len(invoices)} rows")
print(f"total records: {len(payments) + len(settlements) + len(invoices)}")
print(f"ground truth injected anomalies: {len(ground_truth)}")
print(f"\n--- scenario_manifest.csv summary (authoritative, independent of engine output) ---")
print(f"orders total: {len(scenario_manifest)}")
print(f"clean baseline orders: {clean_count} ({100*clean_count/len(scenario_manifest):.1f}%)")
print(f"orders with an injected anomaly scenario: {anomaly_count} ({100*anomaly_count/len(scenario_manifest):.1f}%)")
print(f"orders with independent formatting noise applied: {dirty_count} ({100*dirty_count/len(scenario_manifest):.1f}%)")
