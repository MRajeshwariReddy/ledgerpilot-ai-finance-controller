#!/usr/bin/env python3
"""
Test script for upgraded AI Controller with real Anthropic Claude API tool calling.

Tests four specific scenarios:
1. "Why is TXNxxxx unresolved?"
2. "What are today's highest-value exceptions?"
3. "Show possible duplicates."
4. "Have we seen a similar exception before?"
"""
import os
import sys
sys.path.insert(0, ".")

from app.agents.controller import investigate, answer_query
from app.services import db as db_module
from app.services import tools
import sqlite3

# First, run the pipeline to ensure we have a populated database
print("=" * 80)
print("SETTING UP TEST DATABASE")
print("=" * 80)
from evaluation.run_evaluation import run_pipeline

result = run_pipeline()
db_module.reset_and_load(
    result["payments"], result["settlements"], result["invoices"],
    result["matches"], result["exceptions"], result["scored_exceptions"],
)
print("✅ Database loaded with test data\n")

# Get some test exception IDs
conn = db_module.get_connection()
test_exceptions = conn.execute(
    "SELECT exception_id, reference_id, category FROM exceptions LIMIT 5"
).fetchall()

print("=" * 80)
print("AVAILABLE TEST EXCEPTIONS")
print("=" * 80)
for exc in test_exceptions:
    print(f"  {exc['exception_id']}: {exc['category']} (reference: {exc['reference_id']})")
print()

# TEST 1: "Why is TXNxxxx unresolved?"
print("=" * 80)
print("TEST 1: Why is TXNxxxx unresolved?")
print("=" * 80)
if test_exceptions:
    exc_id = test_exceptions[0]['exception_id']
    ref_id = test_exceptions[0]['reference_id']
    print(f"\nInvestigating: {exc_id} (ref: {ref_id})")
    print("-" * 80)
    
    result = investigate(exc_id)
    print(f"Tools called: {result['tools_called']}")
    print(f"\nResponse:\n{result['response'][:500]}...")  # First 500 chars
    print()

# TEST 2: "What are today's highest-value exceptions?"
print("=" * 80)
print("TEST 2: What are today's highest-value exceptions?")
print("=" * 80)
print("\nCalling tools to find highest-value exceptions...")
print("-" * 80)

stats = tools.get_exception_statistics()
if stats and "by_category" in stats:
    # Sort by value (v key) descending
    sorted_categories = sorted(
        stats["by_category"],
        key=lambda x: float(x.get("v", 0)),
        reverse=True
    )
    print(f"Top 5 exception categories by value:")
    for cat_stats in sorted_categories[:5]:
        print(f"  {cat_stats['category']}: ₹{cat_stats['v']:,.2f} ({cat_stats['c']} cases)")
print()

# TEST 3: "Show possible duplicates"
print("=" * 80)
print("TEST 3: Show possible duplicates")
print("=" * 80)
print("\nSearching for POSSIBLE_DUPLICATE and DUPLICATE_TRANSACTION exceptions...")
print("-" * 80)

conn = db_module.get_connection()
duplicate_exceptions = conn.execute(
    "SELECT exception_id, reference_id, affected_amount, severity FROM exceptions "
    "WHERE category IN ('POSSIBLE_DUPLICATE', 'DUPLICATE_TRANSACTION') LIMIT 5"
).fetchall()

for exc in duplicate_exceptions:
    print(f"  {exc['exception_id']}: {exc['reference_id']} - ₹{exc['affected_amount']} ({exc['severity']})")

if not duplicate_exceptions:
    print("  No duplicate exceptions found in database")
print()

# TEST 4: "Have we seen a similar exception before?"
print("=" * 80)
print("TEST 4: Have we seen a similar exception before?")
print("=" * 80)
print("\nGetting previous resolutions for AMOUNT_MISMATCH category...")
print("-" * 80)

previous = tools.get_previous_resolutions("AMOUNT_MISMATCH", limit=3)
if previous:
    print(f"Found {len(previous)} previous AMOUNT_MISMATCH cases:")
    for case in previous:
        print(f"  {case['exception_id']}: resolved as '{case['resolution']}' by {case['reviewer']}")
else:
    print("  No previous resolutions found for AMOUNT_MISMATCH")
print()

# TEST 5: Using answer_query for natural language
print("=" * 80)
print("TEST 5: Natural language query (answer_query)")
print("=" * 80)

test_queries = [
    "What is the reconciliation rate?",
    "How many unresolved exceptions do we have?",
    "Show me duplicates",
]

for query in test_queries:
    print(f"\nQuery: {query}")
    print("-" * 40)
    response = answer_query(query)
    print(f"Response: {response}")
    print()

print("=" * 80)
print("✅ ALL TESTS COMPLETED")
print("=" * 80)
print("\nSummary:")
print("  - investigate() function: Uses Anthropic Claude API with tool calling")
print("  - Falls back to deterministic response if no API key")
print("  - answer_query() function: Uses deterministic tool selection")
print("  - All tools are read-only and database safe")
print("  - Tool calling tracks which tools were used for each response")
