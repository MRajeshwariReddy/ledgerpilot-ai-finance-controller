# LedgerPilot — AI Finance Controller

Built for Razorpay AI Buildathon 2026 — **Track 04: AI Finance Controller**

## 1. Problem

Reconciling payments, settlements, and invoices across sources is manual,
error-prone, and slow. Finance teams spend hours matching records by hand,
and mismatches (fee discrepancies, missing payouts, duplicate charges,
partial settlements) often go unnoticed until they're expensive.

## 2. Why it matters

Every unresolved reconciliation exception is either money the business is
owed and hasn't collected, or a bookkeeping error waiting to compound.
Manual reconciliation doesn't scale with transaction volume, and existing
"AI finance" tools tend to either (a) do everything with an LLM, including
the math, which is unsafe, or (b) offer a dashboard with a chatbot bolted
on, which doesn't actually reduce manual work.

## 3. Solution

LedgerPilot separates the two jobs that get conflated: **deterministic
software reconciles the money**, and **AI investigates, explains, and
prioritizes** — never the reverse. A human approves every resolution.
Nothing closes itself.

## 4. Architecture

```
Raw CSVs (payments, settlements, invoices)
  -> Data Quality Engine (deterministic validation/normalization)
  -> Reconciliation Engine (deterministic matching + Decimal math)
  -> Exception Classification + Priority Scoring (deterministic)
  -> SQLite (single source of truth for everything downstream)
  -> AI Finance Controller (LLM, read-only tool calls only)
  -> Human approval (Streamlit dashboard) -> Audit Log + Case Memory
```

## 5. AI components

- **AI Finance Controller** (`app/agents/controller.py`): investigates a
  specific exception by calling read-only tools, and answers natural
  language questions by routing to deterministic aggregate functions.
  Falls back to a deterministic templated investigator if no LLM API key
  is configured, so the app is fully demoable either way.
- Every investigation response is structured as **FACT / INFERENCE /
  RECOMMENDATION** — facts come only from tool results, inferences are
  explicitly labeled as interpretation, recommendations always require
  human approval.
- **Case memory**: past human resolutions are retrievable as evidence for
  similar new cases, but the AI is instructed never to auto-copy a past
  resolution — it must say the new case "has not yet been independently
  verified."

## 6. Deterministic components

- Data validation/normalization (`app/data/quality_engine.py`)
- Reconciliation matching and Decimal-based discrepancy calculation
  (`app/reconciliation/engine.py`)
- Exception classification (`app/reconciliation/engine.py`)
- Priority scoring (`app/reconciliation/priority.py`)
- All 10 AI Controller tools are read-only SQL queries
  (`app/services/tools.py`) — the AI never computes money or decides a
  match itself.

## 7. Reconciliation methodology

Matching is tried in order per payment:
1. **MATCH_EXACT_ID** — settlement's reference matches the transaction ID directly.
2. **MATCH_ORDER_ID** — resolved via a uniquely-identifying invoice/order link.
3. **MATCH_AMOUNT_DATE** — a numeric fingerprint (settled amount within a
   tight tolerance of the expected net, within a date window) uniquely
   identifies one candidate. This is the primary fallback for corrupted
   reference IDs — see the note below on why ID similarity alone doesn't work here.
4. **PROBABLE_MATCH** — multiple numeric candidates; ID edit-distance used
   only as a tie-breaker among an already-plausible pool.
5. **UNMATCHED** — nothing found.

**Why not fuzzy ID matching as the primary signal?** This domain's IDs are
sequential (`TXN10001`, `TXN10002`...), so two totally *unrelated*
transactions naturally sit only 1-2 character edits apart. Both
ratio-based similarity and raw edit distance were tested and found
useless as standalone discriminators (documented in `docs/evaluation.md`)
— the money itself is the reliable signal here, not the string.

## 8. Exception handling

11 categories, each with `exception_id`, `reference_id`, `category`,
`affected_amount`, `evidence`, `severity`, `confidence`, `status`,
`created_at`. Priority is a deterministic weighted score over amount,
severity, age, category risk, and confidence — never LLM-assigned.

## 9. Human-in-the-loop design

The AI Controller can recommend; it cannot resolve. Every exception
requires an explicit human action (Approve / Reject / Request further
investigation) via the dashboard, which writes both a case-memory record
and an audit log entry (`app/services/tools.py:record_case_resolution`,
called only from the human approval flow, never by the AI directly).

## 10. Evaluation methodology

`data/generated/ground_truth_labels.csv` is a real answer key — the data
generator labels every anomaly it deliberately injects. Evaluation runs
the full pipeline and checks detected exceptions against that key,
per-category and in aggregate. Full methodology and honest discussion of
limitations: `docs/evaluation.md`.

## 11. Results

As of the latest run (`python run.py eval`):

| Metric | Value |
|---|---|
| Overall precision | 0.615 |
| Overall recall | 0.890 |
| Overall F1 | 0.727 |

Category-level breakdown, known limitations, and the story of two real
bugs found and fixed during tuning: see `docs/evaluation.md`.

## 12. Screenshots

Not included — run `python run.py dashboard` locally to see the live UI
(exception queue, AI investigation panel, simulation mode, audit log).

## 13. Installation

```bash
git clone <your-repo-url>
cd ledgerpilot
pip install -r requirements.txt
cp .env.example .env   # optional: add GEMINI_API_KEY for LLM-powered responses
```

## 14. Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | No | Enables LLM-powered AI Controller responses and NL chat. Without it, a deterministic templated investigator is used instead — the app is still fully functional and demoable. |

## 15. How to run

```bash
python run.py setup      # generates 3,260 synthetic records, runs the full
                          # pipeline, loads everything into SQLite
python run.py dashboard  # launches the Streamlit dashboard
python run.py test       # runs the automated test suite (14 tests)
python run.py eval       # re-runs the pipeline and prints evaluation metrics
```

**A note on verification in this repo's build environment:** the sandbox
this was built in had no network access, so `streamlit` and `pytest`
could not be `pip install`ed there to launch a live UI test — the
dashboard's code was verified by syntax-checking it and independently
testing every function it calls (`tools.*`, `controller.*`) against the
real database, which all passed. The 14 automated tests in `tests/` were
run directly as plain Python function calls (not through the `pytest`
CLI, for the same reason) and all 14 passed. Run `python run.py test` and
`python run.py dashboard` yourself as the first step to confirm both in
your actual environment.

## 16. Demo workflow

1. `python run.py setup`
2. `python run.py dashboard`
3. View the top-line metrics (reconciliation rate, unresolved value, critical count)
4. Open the Exception Queue tab, filter to CRITICAL severity
5. Select a high-value exception, click "Ask AI Controller to investigate"
6. Read the FACT / INFERENCE / RECOMMENDATION response and expand "raw evidence"
7. Approve or reject the recommendation as a human reviewer
8. Ask a few questions in the "Ask LedgerPilot" tab (e.g. "what percentage
   of transactions were reconciled?")
9. Go to Simulation Mode, select a batch of low-risk exceptions, run the
   simulation, note nothing was actually changed
10. Check the Audit Log tab — your approval from step 7 is there with
    full evidence attached
11. Check the Evaluation tab for the real precision/recall numbers

Takes about 5 minutes end to end.

## 17. Limitations

- `MISSING_SETTLEMENT` recall (0.75) and `INVALID_REFERENCE` precision
  (0.22, partly a ground-truth labeling gap — see `docs/evaluation.md`)
  are the weakest metrics and would benefit from another tuning pass.
- The AI Controller's Gemini path was not tested with a live API key in
  this build environment (no network access) — only the deterministic
  fallback path was verified running. The fallback and the LLM path share
  the same evidence-gathering code, so the risk is limited to prompt/response
  quality, not correctness of the underlying facts.
- No fuzzy customer-level entity resolution (e.g. two customer IDs that
  are actually the same person).
- Simulation mode's "time saved" estimate is a fixed per-case assumption,
  clearly labeled as an estimate — not a measured figure.
- No FastAPI layer yet — the dashboard talks to the service layer
  in-process rather than over HTTP. Fine for a demo/single-user context;
  would need an API layer for multi-user or programmatic access.

## 18. Future improvements

- Add the fuzzy customer-level matching mentioned above.
- Wire actual LangChain/Gemini function-calling (native tool-use) instead
  of the current evidence-gather-then-prompt pattern, once a live API key
  is available to test against.
- Add a FastAPI layer per the original suggested stack, for API access
  independent of the Streamlit dashboard.
- Expand the evaluation suite to include false-positive-rate and
  processing-time measurements (currently precision/recall/F1 only).
