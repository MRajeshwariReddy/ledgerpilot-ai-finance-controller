"""
Enhanced evaluation framework with detailed diagnostics.
Adds:
  1. Per-category false-positive and false-negative analysis
  2. Confidence-level breakdowns showing how metrics change at different thresholds
  3. Matching-method breakdown vs accuracy
  4. Monetary impact by confidence level
  5. Exception records broken down by root cause
"""
import os
import csv
import json
from decimal import Decimal
from collections import defaultdict
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from app.data.quality_engine import load_and_process
from app.reconciliation.engine import ReconciliationEngine, _to_decimal, _to_date
from app.reconciliation.priority import compute_priority_score
from app.services import db as db_module
from datetime import date

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "generated")

def load_ground_truth():
    with open(os.path.join(DATA_DIR, "ground_truth_labels.csv")) as f:
        return list(csv.DictReader(f))

REFERENCE_TYPE_BY_CATEGORY = {"MISSING_PAYMENT": "order_id"}

def analyze_false_positives_by_category(exceptions):
    """Break down false positives per category with details."""
    ground_truth = load_ground_truth()
    
    gt_pairs = set()
    for row in ground_truth:
        ref_type = REFERENCE_TYPE_BY_CATEGORY.get(row["anomaly_type"], "transaction_id")
        gt_pairs.add((row[ref_type], row["anomaly_type"]))
    
    fp_by_category = defaultdict(list)
    for exc in exceptions:
        for ref in exc.reference_id.split("/"):
            if (ref, exc.category) not in gt_pairs:
                fp_by_category[exc.category].append({
                    "exception_id": exc.exception_id,
                    "reference_id": ref,
                    "confidence": exc.confidence,
                    "amount": exc.affected_amount,
                })
    
    return fp_by_category

def analyze_false_negatives_by_category(exceptions):
    """Find ground-truth anomalies we missed per category."""
    ground_truth = load_ground_truth()
    
    detected_pairs = set()
    for exc in exceptions:
        for ref in exc.reference_id.split("/"):
            detected_pairs.add((ref, exc.category))
    
    fn_by_category = defaultdict(list)
    for row in ground_truth:
        ref_type = REFERENCE_TYPE_BY_CATEGORY.get(row["anomaly_type"], "transaction_id")
        if (row[ref_type], row["anomaly_type"]) not in detected_pairs:
            fn_by_category[row["anomaly_type"]].append({
                "transaction_id": row["transaction_id"],
                "order_id": row["order_id"],
                "category": row["anomaly_type"],
            })
    
    return fn_by_category

def analyze_by_match_method(matches, exceptions):
    """Accuracy metrics broken down by reconciliation match method."""
    ground_truth = load_ground_truth()
    
    gt_pairs = set()
    for row in ground_truth:
        ref_type = REFERENCE_TYPE_BY_CATEGORY.get(row["anomaly_type"], "transaction_id")
        gt_pairs.add((row[ref_type], row["anomaly_type"]))
    
    by_method = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0, "matched_count": 0, "exception_count": 0})
    
    for match in matches:
        by_method[match.method]["matched_count"] += 1
    
    for exc in exceptions:
        for ref in exc.reference_id.split("/"):
            # Find the match method for this transaction
            for match in matches:
                if match.transaction_id == ref:
                    method = match.method
                    break
            else:
                method = "UNMATCHED"
            
            is_tp = (ref, exc.category) in gt_pairs
            by_method[method]["exception_count"] += 1
            if is_tp:
                by_method[method]["tp"] += 1
            else:
                by_method[method]["fp"] += 1
    
    results = {}
    for method, stats in by_method.items():
        if stats["exception_count"] > 0:
            precision = stats["tp"] / stats["exception_count"] if stats["exception_count"] else 0
            results[method] = {
                **stats,
                "precision": round(precision, 3),
                "exception_rate": round(100 * stats["exception_count"] / stats["matched_count"], 1) if stats["matched_count"] else 0,
            }
    
    return results

def analyze_by_confidence_level(exceptions, bins=None):
    """Break down metrics by confidence level."""
    if bins is None:
        bins = [0.0, 0.5, 0.65, 0.8, 0.9, 1.0]
    
    ground_truth = load_ground_truth()
    gt_pairs = set()
    for row in ground_truth:
        ref_type = REFERENCE_TYPE_BY_CATEGORY.get(row["anomaly_type"], "transaction_id")
        gt_pairs.add((row[ref_type], row["anomaly_type"]))
    
    by_bin = {}
    for i in range(len(bins) - 1):
        lo, hi = bins[i], bins[i + 1]
        filtered = [e for e in exceptions if lo <= e.confidence < hi]
        tp = sum(1 for e in filtered for r in e.reference_id.split("/") if (r, e.category) in gt_pairs)
        fp = sum(1 for e in filtered for r in e.reference_id.split("/") if (r, e.category) not in gt_pairs)
        precision = tp / (tp + fp) if (tp + fp) else 0
        by_bin[f"{lo:.2f}-{hi:.2f}"] = {
            "count": len(filtered),
            "tp": tp,
            "fp": fp,
            "precision": round(precision, 3),
        }
    
    return by_bin

def print_enhanced_diagnostics(exceptions, matches, payments):
    """Print all diagnostic breakdowns."""
    print("\n" + "=" * 80)
    print("ENHANCED DIAGNOSTICS: FALSE POSITIVE BREAKDOWN")
    print("=" * 80)
    
    fp_by_cat = analyze_false_positives_by_category(exceptions)
    for category in sorted(fp_by_cat.keys()):
        fps = fp_by_cat[category]
        if fps:
            print(f"\n{category}: {len(fps)} false positives")
            print(f"  Total FP amount: Rs {sum(Decimal(fp['amount']) for fp in fps):,.2f}")
            print(f"  Confidence range: {min(fp['confidence'] for fp in fps):.2f} - {max(fp['confidence'] for fp in fps):.2f}")
            for fp in fps[:3]:
                print(f"    {fp['exception_id']}: {fp['reference_id']} (conf={fp['confidence']:.2f})")
    
    print("\n" + "=" * 80)
    print("ENHANCED DIAGNOSTICS: FALSE NEGATIVE BREAKDOWN")
    print("=" * 80)
    
    fn_by_cat = analyze_false_negatives_by_category(exceptions)
    for category in sorted(fn_by_cat.keys()):
        fns = fn_by_cat[category]
        if fns:
            print(f"\n{category}: {len(fns)} false negatives (missed cases)")
            for fn in fns[:3]:
                print(f"  {fn['transaction_id']} (order {fn['order_id']})")
    
    print("\n" + "=" * 80)
    print("ENHANCED DIAGNOSTICS: ACCURACY BY MATCH METHOD")
    print("=" * 80)
    
    by_method = analyze_by_match_method(matches, exceptions)
    for method in sorted(by_method.keys()):
        stats = by_method[method]
        print(f"\n{method}:")
        print(f"  Matched: {stats['matched_count']} | Exceptions: {stats['exception_count']} "
              f"({stats['exception_rate']}% exception rate)")
        print(f"  TP/FP: {stats['tp']}/{stats['fp']} | Precision: {stats['precision']}")
    
    print("\n" + "=" * 80)
    print("ENHANCED DIAGNOSTICS: PRECISION BY CONFIDENCE LEVEL")
    print("=" * 80)
    
    by_conf = analyze_by_confidence_level(exceptions)
    for bin_name in sorted(by_conf.keys()):
        stats = by_conf[bin_name]
        print(f"\nConfidence {bin_name}: {stats['count']} exceptions, {stats['precision']:.3f} precision")
        print(f"  (TP={stats['tp']}, FP={stats['fp']})")
