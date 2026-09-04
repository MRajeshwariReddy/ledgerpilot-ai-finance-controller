"""
AI Finance Controller (With Native Anthropic Claude API Tool Calling).

Investigates exceptions and answers natural-language finance questions
using Anthropic's Claude API with native tool calling (function calling).
The LLM can call read-only tools directly, see the results, and reason over them.

Tool calling flow:
1. User asks a question or requests exception investigation
2. Claude reads tool definitions and decides which tools to call
3. Claude executes tools (via our middleware) and gets results
4. Claude reasons over results and responds
5. Agentic loop continues until Claude signals it's done (no more tool calls)

Every response is evidence-first: FACT (from tool calls) / INFERENCE 
(the AI's interpretation, clearly labeled as such) / RECOMMENDATION 
(a suggestion requiring human approval, never auto-applied).

Falls back to deterministic templated investigator if no API key is set,
so the app is still demoable without credentials.
"""
import os
import json

from app.services import tools

try:
    from anthropic import Anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False


SYSTEM_PROMPT = """You are LedgerPilot's AI Finance Controller. You investigate
payment reconciliation exceptions by calling read-only tools to retrieve
real data from the database. You have NO other source of truth, and you must
NEVER invent a transaction, amount, or date that isn't returned by a tool.

Structure EVERY investigation response into exactly three labeled sections:

FACT: Only things directly stated in the tool results (amounts, dates,
statuses, IDs). No interpretation here.

INFERENCE: Your interpretation of what the facts suggest. Always phrase
this as an interpretation, never as a certainty ("this is consistent
with...", "this looks like...").

RECOMMENDATION: A specific suggested next action. Always end with a
reminder that this requires human approval before anything is closed or
changed -- you are not authorized to resolve exceptions yourself.

If a previous similar case is provided as evidence, you may mention it as
context ("a similar case was previously resolved as X"), but you must
explicitly say it has NOT been verified for this case and must not copy
its resolution automatically.

If the tool results are insufficient to answer confidently, say exactly:
"Insufficient evidence — human review required." Do not guess.
"""


# ============================================================================
# Tool Definitions for Function Calling
# ============================================================================
# These are the tools the LLM can call via Claude's native function calling.
# All tools are read-only; they fetch data from the database only.

TOOL_DEFINITIONS = [
    {
        "name": "get_transaction",
        "description": "Retrieve a payment transaction by transaction ID",
        "input_schema": {
            "type": "object",
            "properties": {
                "transaction_id": {
                    "type": "string",
                    "description": "The transaction ID (e.g., TXN10001)"
                }
            },
            "required": ["transaction_id"]
        }
    },
    {
        "name": "get_settlement",
        "description": "Retrieve a settlement record by settlement ID",
        "input_schema": {
            "type": "object",
            "properties": {
                "settlement_id": {
                    "type": "string",
                    "description": "The settlement ID (e.g., STL10001)"
                }
            },
            "required": ["settlement_id"]
        }
    },
    {
        "name": "get_invoice",
        "description": "Retrieve an invoice record by order ID",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "The order ID (e.g., ORD10001)"
                }
            },
            "required": ["order_id"]
        }
    },
    {
        "name": "get_exception",
        "description": "Retrieve an exception record by exception ID",
        "input_schema": {
            "type": "object",
            "properties": {
                "exception_id": {
                    "type": "string",
                    "description": "The exception ID (e.g., EXC000001)"
                }
            },
            "required": ["exception_id"]
        }
    },
    {
        "name": "search_transactions",
        "description": "Search for transactions by customer, order, or amount range",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string", "description": "Customer ID (optional)"},
                "order_id": {"type": "string", "description": "Order ID (optional)"},
                "min_amount": {"type": "number", "description": "Minimum amount in rupees (optional)"},
                "max_amount": {"type": "number", "description": "Maximum amount in rupees (optional)"},
                "limit": {"type": "integer", "description": "Max results to return (default 20)", "default": 20}
            }
        }
    },
    {
        "name": "find_matching_candidates",
        "description": "Find settlement candidates that could match a transaction (by amount proximity)",
        "input_schema": {
            "type": "object",
            "properties": {
                "transaction_id": {
                    "type": "string",
                    "description": "The transaction ID to find matches for"
                },
                "tolerance_pct": {
                    "type": "number",
                    "description": "Tolerance band in percent (default 15)",
                    "default": 15.0
                }
            },
            "required": ["transaction_id"]
        }
    },
    {
        "name": "get_previous_resolutions",
        "description": "Get similar past case resolutions for context (NOT authority). Returns cases with similarity scores based on exception type and amount.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Exception category to find similar cases (e.g., AMOUNT_MISMATCH)"
                },
                "current_amount": {
                    "type": "string",
                    "description": "Current exception amount (for similarity scoring, optional)"
                },
                "limit": {
                    "type": "integer",
                    "description": "Max cases to return (default 5)",
                    "default": 5
                }
            }
        }
    },
    {
        "name": "calculate_reconciliation_summary",
        "description": "Get overall reconciliation statistics: total transactions, exceptions, reconciliation rate",
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "get_exception_statistics",
        "description": "Get exception statistics grouped by category, severity, and priority tier",
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    }
]


def _call_tool(tool_name: str, tool_input: dict):
    """Execute a tool and return its result. Used by the function calling loop."""
    if tool_name == "get_transaction":
        return tools.get_transaction(tool_input["transaction_id"])
    elif tool_name == "get_settlement":
        return tools.get_settlement(tool_input["settlement_id"])
    elif tool_name == "get_invoice":
        return tools.get_invoice(tool_input["order_id"])
    elif tool_name == "get_exception":
        return tools.get_exception(tool_input["exception_id"])
    elif tool_name == "search_transactions":
        return tools.search_transactions(
            customer_id=tool_input.get("customer_id"),
            order_id=tool_input.get("order_id"),
            min_amount=tool_input.get("min_amount"),
            max_amount=tool_input.get("max_amount"),
            limit=tool_input.get("limit", 20)
        )
    elif tool_name == "find_matching_candidates":
        return tools.find_matching_candidates(
            transaction_id=tool_input["transaction_id"],
            tolerance_pct=tool_input.get("tolerance_pct", 15.0)
        )
    elif tool_name == "get_previous_resolutions":
        return tools.get_previous_resolutions(
            category=tool_input.get("category"),
            current_amount=tool_input.get("current_amount"),
            limit=tool_input.get("limit", 5)
        )
    elif tool_name == "calculate_reconciliation_summary":
        return tools.calculate_reconciliation_summary()
    elif tool_name == "get_exception_statistics":
        return tools.get_exception_statistics()
    else:
        return {"error": f"Unknown tool: {tool_name}"}


def _gather_evidence(exception_id: str) -> dict:
    exc = tools.get_exception(exception_id)
    if exc is None:
        return {"error": f"No exception found with id {exception_id}"}

    txn = tools.get_transaction(exc["reference_id"])
    settlement = None
    invoice = None
    if txn:
        matches_conn_result = tools.find_matching_candidates(exc["reference_id"])
    else:
        matches_conn_result = []

    previous_cases = tools.get_previous_resolutions(
        category=exc["category"],
        current_amount=exc.get("affected_amount"),
        limit=3
    )

    return {
        "exception": exc,
        "transaction": txn,
        "candidate_settlements": matches_conn_result,
        "previous_similar_cases": previous_cases,
    }


def investigate(exception_id: str, api_key: str = None) -> dict:
    """
    Investigates an exception using Anthropic Claude with native tool calling.
    
    Returns {"evidence": {...}, "response": "FACT:...\\nINFERENCE:...\\nRECOMMENDATION:...",
             "tools_called": ["tool1", "tool2", ...]}
    """
    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    
    if not ANTHROPIC_AVAILABLE or not api_key:
        # Fallback to deterministic investigator
        evidence = _gather_evidence(exception_id)
        if "error" in evidence:
            return {
                "evidence": evidence,
                "response": f"Insufficient evidence — human review required. ({evidence['error']})",
                "tools_called": []
            }
        response = _fallback_investigate(evidence)
        return {"evidence": evidence, "response": response, "tools_called": []}
    
    # Use Anthropic Claude with native tool calling
    client = Anthropic(api_key=api_key)
    messages = [
        {
            "role": "user",
            "content": f"Please investigate exception {exception_id}. Retrieve relevant data using the available tools, analyze it, and provide a FACT / INFERENCE / RECOMMENDATION response."
        }
    ]
    
    tools_called = []
    max_iterations = 10
    iteration = 0
    
    while iteration < max_iterations:
        iteration += 1
        
        # Call Claude with tool definitions
        response = client.messages.create(
            model="claude-opus-4-8",
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            tools=TOOL_DEFINITIONS,
            messages=messages
        )
        
        # Check if Claude wants to stop (no tool calls)
        if response.stop_reason == "end_turn":
            # Extract the text response
            final_response = ""
            for block in response.content:
                if hasattr(block, "text"):
                    final_response = block.text
            return {
                "evidence": None,
                "response": final_response,
                "tools_called": tools_called
            }
        
        # Process tool calls
        tool_calls_made = False
        for block in response.content:
            if block.type == "tool_use":
                tool_calls_made = True
                tool_name = block.name
                tool_input = block.input
                tool_use_id = block.id
                
                if tool_name not in tools_called:
                    tools_called.append(tool_name)
                
                # Execute the tool
                tool_result = _call_tool(tool_name, tool_input)
                
                # Add assistant response and tool result to messages
                messages.append({"role": "assistant", "content": response.content})
                messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "content": json.dumps(tool_result, default=str)
                        }
                    ]
                })
                break  # Process one tool at a time, then loop back to Claude
        
        # If no tool calls were made but we didn't end the turn, something went wrong
        if not tool_calls_made and response.stop_reason != "end_turn":
            # Add the response and try again
            messages.append({"role": "assistant", "content": response.content})
            if iteration >= max_iterations - 1:
                return {
                    "evidence": None,
                    "response": "Insufficient evidence — human review required (too many iterations).",
                    "tools_called": tools_called
                }
    
    return {
        "evidence": None,
        "response": "Insufficient evidence — human review required (too many iterations).",
        "tools_called": tools_called
    }


def _fallback_investigate(evidence: dict) -> str:
    """
    Deterministic templated investigator, used when no LLM key is set.
    Builds the same FACT/INFERENCE/RECOMMENDATION shape from the evidence
    directly -- less fluent than an LLM, but zero hallucination risk and
    keeps the app demoable without a key.
    """
    exc = evidence["exception"]
    txn = evidence.get("transaction")
    prev_cases = evidence.get("previous_similar_cases", [])

    facts = [f"Exception {exc['exception_id']}, category {exc['category']}, "
             f"affected amount ₹{exc['affected_amount']}, severity {exc['severity']}, "
             f"priority tier {exc['priority_tier']}."]
    for k, v in exc.get("evidence", {}).items():
        if not isinstance(v, (dict, list)):
            facts.append(f"{k} = {v}")
    if txn:
        facts.append(f"Transaction {txn['transaction_id']}: ₹{txn['amount']} {txn['currency']}, "
                      f"{txn['payment_method']}, status={txn['status']}, date={txn['transaction_date']}.")

    inference_lines = {
        "AMOUNT_MISMATCH": "The settled amount differs from the expected net amount by more than the standard tolerance. This is consistent with either an uncounted fee/tax component or a genuine underpayment.",
        "MISSING_SETTLEMENT": "No settlement record exists for this captured payment. This is consistent with a payout still pending, or a settlement that failed to land in the bank feed.",
        "MISSING_INVOICE": "No invoice record exists for this order despite a payment/settlement being present. This is consistent with an invoice generation gap rather than a payment problem.",
        "MISSING_PAYMENT": "An invoice and/or settlement reference this order, but no payment transaction was found. This is consistent with a data ingestion gap rather than a real missing payment.",
        "DUPLICATE_TRANSACTION": "An identical transaction record appears more than once. This is consistent with a duplicate ingestion event rather than two real payments.",
        "POSSIBLE_DUPLICATE": "Two distinct transactions share the same order, customer, and amount within a short window. This is consistent with either a genuine retry/duplicate charge or two coincidentally similar legitimate transactions.",
        "DATE_MISMATCH": "The settlement date falls well outside the normal settlement window for this transaction. This is consistent with a processing delay.",
        "CURRENCY_MISMATCH": "The settlement currency does not match the transaction currency. This needs verification, as it could indicate a data entry error or a genuine cross-currency settlement.",
        "UNEXPECTED_FEE": "The deducted fee is well outside the standard fee rate for this transaction. This is consistent with a fee miscalculation or an unusual fee tier.",
        "INVALID_REFERENCE": "This transaction could not be matched by its stated reference ID and was instead matched via amount and date proximity. The reference field itself should be treated as unreliable for this record.",
        "PARTIAL_SETTLEMENT": "The settled amount is substantially less than the expected net amount. This is consistent with a partial payout rather than a full settlement.",
    }
    inference = inference_lines.get(exc["category"],
        "This exception does not match a common pattern; manual review is needed to determine cause.")

    prev_note = ""
    if prev_cases:
        p = prev_cases[0]
        similarity = p.get('similarity_score')
        if similarity is not None:
            prev_note = (f" A similar historical case ({p['exception_id']}, "
                        f"similarity score {similarity}%) was resolved as '{p['resolution']}' "
                        f"by {p['reviewer']}, but this case has not yet been independently verified.")
        else:
            prev_note = (f" A similar historical case ({p['exception_id']}) was resolved as "
                        f"'{p['resolution']}' by {p['reviewer']}, but this case has not yet "
                        f"been independently verified.")

    recommendation = {
        "AMOUNT_MISMATCH": "Verify the settlement's fee breakdown against the bank statement before closing.",
        "MISSING_SETTLEMENT": "Check the payout pipeline for this transaction; escalate if pending beyond the normal settlement window.",
        "MISSING_INVOICE": "Verify whether an invoice should exist for this order and regenerate if needed.",
        "MISSING_PAYMENT": "Verify ingestion logs for this order's payment feed; confirm the payment genuinely doesn't exist before writing it off.",
        "DUPLICATE_TRANSACTION": "Confirm with the ingestion source and remove the duplicate record.",
        "POSSIBLE_DUPLICATE": "Contact the customer or check for a retry pattern before assuming this is a duplicate charge.",
        "DATE_MISMATCH": "Confirm the settlement delay is expected (e.g. bank processing time) rather than a lost payout.",
        "CURRENCY_MISMATCH": "Verify the correct settlement currency with the payment processor.",
        "UNEXPECTED_FEE": "Verify the fee schedule applied to this transaction against the current rate card.",
        "INVALID_REFERENCE": "Verify the numeric-fingerprint match is correct, then correct the reference field at the source.",
        "PARTIAL_SETTLEMENT": "Confirm whether a second, remaining settlement is expected before treating this as final.",
    }.get(exc["category"], "Escalate for manual review.")

    return (
        f"FACT:\n" + "\n".join(f"- {f}" for f in facts) +
        f"\n\nINFERENCE:\n{inference}{prev_note}"
        f"\n\nRECOMMENDATION:\n{recommendation} This requires human approval before the exception "
        f"is closed or any financial record is changed."
    )


def answer_query(question: str, api_key: str = None) -> str:
    """
    Answers a natural-language finance question by calling the appropriate
    deterministic tool(s) and grounding the answer in their output.
    Simple keyword routing to tools -- deliberately not a free-form
    LLM-computed answer, so numbers always come from calculate_reconciliation_summary()
    / get_exception_statistics(), never from the model's own arithmetic.
    """
    q = question.lower()

    # Check specific transaction-id investigation FIRST -- "why is TXN123
    # unresolved" would otherwise get caught by the generic "unresolved"
    # keyword check below, since that word is literally in the question.
    if q.strip().startswith(("why", "what happened")):
        import re
        m = re.search(r"TXN\d+", question.upper())
        if m:
            from app.services.db import get_connection
            conn = get_connection()
            row = conn.execute(
                "SELECT exception_id FROM exceptions WHERE reference_id = ? LIMIT 1", (m.group(0),)
            ).fetchone()
            conn.close()
            if row:
                result = investigate(row["exception_id"])
                return result["response"]
            return f"No open exception found for {m.group(0)} — it may be fully reconciled."

    if "unresolved" in q or "unreconciled" in q:
        s = tools.calculate_reconciliation_summary()
        return (f"{s['total_exceptions']} exceptions are currently unresolved, "
                f"totaling ₹{s['total_unresolved_value']:,.2f} in affected value.")

    if "reconcil" in q and ("percent" in q or "rate" in q or "%" in q):
        s = tools.calculate_reconciliation_summary()
        return (f"{s['reconciliation_rate_pct']}% of transactions were cleanly reconciled "
                f"({s['cleanly_reconciled']} of {s['total_transactions']}).")

    if "duplicate" in q:
        stats = tools.get_exception_statistics()
        cat = next((c for c in stats["by_category"] if c["category"] in
                    ("POSSIBLE_DUPLICATE", "DUPLICATE_TRANSACTION")), None)
        dup_cats = [c for c in stats["by_category"] if "DUPLICATE" in c["category"]]
        if dup_cats:
            lines = [f"{c['category']}: {c['c']} cases, ₹{c['v']:,.2f} total" for c in dup_cats]
            return "Possible duplicate payments:\n" + "\n".join(lines)
        return "No duplicate-type exceptions found."

    if "largest" in q or "biggest" in q or "highest" in q:
        stats = tools.get_exception_statistics()
        if stats["by_category"]:
            top = stats["by_category"][0]
            return (f"{top['category']} has the largest monetary impact: "
                    f"₹{top['v']:,.2f} across {top['c']} exceptions.")

    stats = tools.get_exception_statistics()
    return ("I can answer questions about unresolved exceptions, reconciliation rate, "
            "duplicates, highest-impact categories, or 'why is TXNxxxx unresolved'. "
            f"Currently there are {sum(c['c'] for c in stats['by_category'])} open exceptions "
            "across all categories.")
