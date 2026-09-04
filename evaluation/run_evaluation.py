"""
Runs the full deterministic pipeline end to end:
  raw CSVs -> Data Quality Engine -> Reconciliation Engine -> Priority Scoring
and evaluates detected exceptions against the ground-truth labels the
generator produced -- giving REAL precision/recall/F1 and monetary
metrics, never claimed ones.

Ground truth source of truth: data/generated/ground_truth_labels.csv
(every injected anomaly, written at generation time) and
data/generated/scenario_manifest.csv (every order's scenario AND whether
independent formatting noise was applied to it -- the authoritative
record of what the generator did, read directly, never inferred from
what the reconciliation engine outputs).
"""
import os
import csv
import time
from decimal import Decimal
from datetime import date, datetime
import sys

from app.data.quality_engine import load_and_process
from app.reconciliation.engine import ReconciliationEngine, _to_decimal, _to_date
from app.reconciliation.priority import compute_priority_score
from app.services import db as db_module

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "generated")

REFERENCE_TYPE_BY_CATEGORY = {
    "MISSING_PAYMENT": "order_id",
}


def run_pipeline():
    t_start = time.perf_counter()

    payments, payments_rejected, payments_report = load_and_process(
        os.path.join(DATA_DIR, "payments_raw.csv"), "payment")
    settlements, settlements_rejected, settlements_report = load_and_process(
        os.path.join(DATA_DIR, "settlements_raw.csv"), "settlement")
    invoices, invoices_rejected, invoices_report = load_and_process(
        os.path.join(DATA_DIR, "invoices_raw.csv"), "invoice")

    t_after_dq = time.perf_counter()

    engine = ReconciliationEngine(payments, settlements, invoices)
    matches, exceptions = engine.run()

    t_after_reconcile = time.perf_counter()

    today = date(2026, 8, 1)
    scored = []
    for exc in exceptions:
        age_days = (today - _to_date(exc.created_at[:10])).days if exc.created_at else 0
        priority = compute_priority_score(exc, _to_decimal(exc.affected_amount), max(age_days, 0))
        scored.append((exc, priority))

    t_end = time.perf_counter()

    total_records = len(payments) + len(settlements) + len(invoices)
    timing = {
        "data_quality_seconds": round(t_after_dq - t_start, 4),
        "reconciliation_seconds": round(t_after_reconcile - t_after_dq, 4),
        "priority_scoring_seconds": round(t_end - t_after_reconcile, 4),
        "total_seconds": round(t_end - t_start, 4),
        "total_records_processed": total_records,
        "records_per_second": round(total_records / (t_end - t_start), 1) if (t_end - t_start) > 0 else None,
    }

    return {
        "dq_reports": {
            "payment": payments_report, "settlement": settlements_report, "invoice": invoices_report,
        },
        "rejected": {
            "payment": payments_rejected, "settlement": settlements_rejected, "invoice": invoices_rejected,
        },
        "matches": matches,
        "exceptions": exceptions,
        "scored_exceptions": scored,
        "payments": payments, "settlements": settlements, "invoices": invoices,
        "timing": timing,
    }


def load_scenario_manifest():
    path = os.path.join(DATA_DIR, "scenario_manifest.csv")
    rows = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            row["is_dirty_formatting"] = row["is_dirty_formatting"] == "True"
            rows.append(row)
    return rows


def load_ground_truth():
    path = os.path.join(DATA_DIR, "ground_truth_labels.csv")
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def _expand_reference(ref: str) -> list:
    return ref.split("/")


def evaluate_against_ground_truth(exceptions):
    """
    Definitions used, stated explicitly:
      - precision/recall/F1: standard, per (reference_id, category) pair.
      - macro precision/recall/F1: unweighted mean across categories.
      - weighted F1: F1 weighted by each category's ground-truth support.
      - detection_rate: fraction of ground-truth anomalies with AT LEAST
        ONE exception raised on that reference, in ANY category.
      - false_positive_rate: FP / (FP + TP) at the detection level (i.e.
        1 - precision). No natural true-negative population exists in
        this multi-category setting, so a classic TN-based FPR isn't
        meaningful; this is the standard substitute for detection tasks.
    """
    ground_truth = load_ground_truth()

    gt_set = set()
    for row in ground_truth:
        ref_type = REFERENCE_TYPE_BY_CATEGORY.get(row["anomaly_type"], "transaction_id")
        gt_set.add((row[ref_type], row["anomaly_type"]))

    detected_set = set()
    for exc in exceptions:
        for ref in _expand_reference(exc.reference_id):
            detected_set.add((ref, exc.category))

    true_positives = gt_set & detected_set
    false_negatives = gt_set - detected_set
    false_positives = detected_set - gt_set

    precision = len(true_positives) / len(detected_set) if detected_set else 0.0
    recall = len(true_positives) / len(gt_set) if gt_set else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    fpr = len(false_positives) / len(detected_set) if detected_set else 0.0

    gt_refs = {ref for ref, _ in gt_set}
    detected_refs = {ref for ref, _ in detected_set}
    detection_rate = len(gt_refs & detected_refs) / len(gt_refs) if gt_refs else 0.0

    by_category = {}
    categories = {c for _, c in gt_set} | {c for _, c in detected_set}
    for cat in categories:
        cat_gt = {r for r, c in gt_set if c == cat}
        cat_detected = {r for r, c in detected_set if c == cat}
        tp = len(cat_gt & cat_detected)
        fp = len(cat_detected - cat_gt)
        fn = len(cat_gt - cat_detected)
        prec = tp / len(cat_detected) if cat_detected else 0.0
        rec = tp / len(cat_gt) if cat_gt else 0.0
        cat_f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) else 0.0
        by_category[cat] = {
            "ground_truth_count": len(cat_gt), "detected_count": len(cat_detected),
            "true_positives": tp, "false_positives": fp, "false_negatives": fn,
            "precision": round(prec, 3), "recall": round(rec, 3), "f1": round(cat_f1, 3),
        }

    macro_precision = sum(s["precision"] for s in by_category.values()) / len(by_category) if by_category else 0.0
    macro_recall = sum(s["recall"] for s in by_category.values()) / len(by_category) if by_category else 0.0
    macro_f1 = sum(s["f1"] for s in by_category.values()) / len(by_category) if by_category else 0.0

    total_support = sum(s["ground_truth_count"] for s in by_category.values())
    weighted_f1 = (
        sum(s["f1"] * s["ground_truth_count"] for s in by_category.values()) / total_support
        if total_support else 0.0
    )

    return {
        "overall": {
            "precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3),
            "true_positives": len(true_positives), "false_positives": len(false_positives),
            "false_negatives": len(false_negatives), "false_positive_rate": round(fpr, 3),
            "detection_rate": round(detection_rate, 3),
        },
        "macro": {
            "precision": round(macro_precision, 3), "recall": round(macro_recall, 3),
            "f1": round(macro_f1, 3),
        },
        "weighted_f1": round(weighted_f1, 3),
        "by_category": by_category,
    }


def evaluate_monetary_impact(exceptions, payments):
    ground_truth = load_ground_truth()
    gt_set = set()
    for row in ground_truth:
        ref_type = REFERENCE_TYPE_BY_CATEGORY.get(row["anomaly_type"], "transaction_id")
        gt_set.add((row[ref_type], row["anomaly_type"]))

    total_transaction_value = sum(_to_decimal(p["amount"]) for p in payments)

    tp_value = Decimal("0")
    fp_value = Decimal("0")
    unresolved_value = Decimal("0")
    value_by_category = {}

    for exc in exceptions:
        amount = _to_decimal(exc.affected_amount)
        refs = _expand_reference(exc.reference_id)
        is_tp = any((ref, exc.category) in gt_set for ref in refs)

        value_by_category.setdefault(exc.category, Decimal("0"))
        value_by_category[exc.category] += amount

        if is_tp:
            tp_value += amount
        else:
            fp_value += amount

        if exc.status == "OPEN":
            unresolved_value += amount

    pct_reconciled = (
        round(100 * (1 - (unresolved_value / total_transaction_value)), 2)
        if total_transaction_value else 0.0
    )

    return {
        "total_transaction_value": str(total_transaction_value.quantize(Decimal("0.01"))),
        "true_positive_exception_value": str(tp_value.quantize(Decimal("0.01"))),
        "false_positive_exception_value": str(fp_value.quantize(Decimal("0.01"))),
        "total_unresolved_value": str(unresolved_value.quantize(Decimal("0.01"))),
        "percentage_value_reconciled": pct_reconciled,
        "value_by_category": {k: str(v.quantize(Decimal("0.01"))) for k, v in
                               sorted(value_by_category.items(), key=lambda x: -x[1])},
    }


def evaluate_at_thresholds(exceptions, thresholds=None):
    if thresholds is None:
        thresholds = {"high_recall": 0.0, "balanced": 0.65, "high_precision": 0.85}

    results = {}
    for label, cutoff in thresholds.items():
        filtered = [e for e in exceptions if e.confidence >= cutoff]
        eval_result = evaluate_against_ground_truth(filtered)
        results[label] = {
            "confidence_cutoff": cutoff,
            "exceptions_retained": len(filtered),
            "exceptions_dropped": len(exceptions) - len(filtered),
            "precision": eval_result["overall"]["precision"],
            "recall": eval_result["overall"]["recall"],
            "f1": eval_result["overall"]["f1"],
        }
    return results


if __name__ == "__main__":
    result = run_pipeline()

    db_module.reset_and_load(
        result["payments"], result["settlements"], result["invoices"],
        result["matches"], result["exceptions"], result["scored_exceptions"],
    )
    print(f"Loaded pipeline output into SQLite: {db_module.DB_PATH}\n")
    
    # Import and run enhanced diagnostics
    from evaluation.diagnostics import print_enhanced_diagnostics
    print_enhanced_diagnostics(result["exceptions"], result["matches"], result["payments"])

    print("=== Processing Time & Throughput ===")
    t = result["timing"]
    print(f"  Data quality: {t['data_quality_seconds']}s | Reconciliation: {t['reconciliation_seconds']}s | "
          f"Priority scoring: {t['priority_scoring_seconds']}s | Total: {t['total_seconds']}s")
    print(f"  {t['total_records_processed']} records processed, {t['records_per_second']} records/sec")

    print("\n=== Data Quality Reports ===")
    for source, report in result["dq_reports"].items():
        print(f"  {source}: {report.valid_records}/{report.total_records} valid, "
              f"{report.invalid_records} rejected, {report.duplicate_records} duplicates, "
              f"{report.normalization_actions} normalizations")

    print("\n=== Ground Truth Composition (from scenario_manifest.csv, generator's own record) ===")
    manifest = load_scenario_manifest()
    clean = sum(1 for r in manifest if r["scenario"] == "CLEAN")
    dirty = sum(1 for r in manifest if r["is_dirty_formatting"])
    print(f"  Total orders: {len(manifest)}")
    print(f"  Clean baseline: {clean} ({100*clean/len(manifest):.1f}%)")
    print(f"  Injected anomaly scenarios: {len(manifest)-clean} ({100*(len(manifest)-clean)/len(manifest):.1f}%)")
    print(f"  Independent formatting noise applied: {dirty} ({100*dirty/len(manifest):.1f}%) "
          f"-- NOT counted as ground-truth anomalies, but can still trigger real exceptions")

    print("\n=== Match Method Breakdown ===")
    method_counts = {}
    for m in result["matches"]:
        method_counts[m.method] = method_counts.get(m.method, 0) + 1
    for method, count in sorted(method_counts.items(), key=lambda x: -x[1]):
        print(f"  {method}: {count}")

    print(f"\n=== Exceptions: {len(result['exceptions'])} total ===")
    cat_counts = {}
    for exc in result["exceptions"]:
        cat_counts[exc.category] = cat_counts.get(exc.category, 0) + 1
    for cat, count in sorted(cat_counts.items(), key=lambda x: -x[1]):
        print(f"  {cat}: {count}")

    print("\n=== Priority Tier Breakdown ===")
    tier_counts = {}
    for exc, priority in result["scored_exceptions"]:
        tier_counts[priority["tier"]] = tier_counts.get(priority["tier"], 0) + 1
    for tier in ["CRITICAL", "HIGH", "MEDIUM", "LOW"]:
        print(f"  {tier}: {tier_counts.get(tier, 0)}")

    print("\n=== Evaluation Against Ground Truth ===")
    evaluation = evaluate_against_ground_truth(result["exceptions"])
    o = evaluation["overall"]
    print(f"  Overall: precision={o['precision']}, recall={o['recall']}, f1={o['f1']}")
    print(f"  TP={o['true_positives']}, FP={o['false_positives']}, FN={o['false_negatives']}")
    print(f"  False-positive rate (FP / all detected): {o['false_positive_rate']}")
    print(f"  Detection rate (any exception on a true-anomaly reference, any category): {o['detection_rate']}")
    m = evaluation["macro"]
    print(f"  Macro precision={m['precision']}, macro recall={m['recall']}, macro F1={m['f1']}")
    print(f"  Weighted F1 (by category support): {evaluation['weighted_f1']}")
    print("\n  By category (TP/FP/FN, precision/recall/F1):")
    for cat, stats in sorted(evaluation["by_category"].items()):
        print(f"    {cat}: TP={stats['true_positives']} FP={stats['false_positives']} FN={stats['false_negatives']} "
              f"| precision={stats['precision']} recall={stats['recall']} f1={stats['f1']} "
              f"(gt={stats['ground_truth_count']}, detected={stats['detected_count']})")

    print("\n=== Monetary Impact ===")
    monetary = evaluate_monetary_impact(result["exceptions"], result["payments"])
    print(f"  Total transaction value: Rs {monetary['total_transaction_value']}")
    print(f"  True-positive exception value: Rs {monetary['true_positive_exception_value']}")
    print(f"  False-positive exception value: Rs {monetary['false_positive_exception_value']}")
    print(f"  Total unresolved value: Rs {monetary['total_unresolved_value']}")
    print(f"  Percentage of value reconciled: {monetary['percentage_value_reconciled']}%")
    print("  Value by category (top 5):")
    for cat, val in list(monetary["value_by_category"].items())[:5]:
        print(f"    {cat}: Rs {val}")

    print("\n=== Threshold Analysis (confidence operating points) ===")
    thresholds = evaluate_at_thresholds(result["exceptions"])
    for label, stats in thresholds.items():
        print(f"  {label} (cutoff>={stats['confidence_cutoff']}): "
              f"precision={stats['precision']}, recall={stats['recall']}, f1={stats['f1']}, "
              f"retained={stats['exceptions_retained']}, dropped={stats['exceptions_dropped']}")
