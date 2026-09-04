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

```mermaid
flowchart TD
    A[Raw CSVs<br/>Payments • Settlements • Invoices] --> B[Data Quality Engine<br/>Validation + Normalization]
    B --> C[Reconciliation Engine<br/>Deterministic Matching + Decimal Math]
    C --> D[Exception Classification<br/>+ Priority Scoring]
    D --> E[(SQLite<br/>Single Source of Truth)]
    E --> F[AI Finance Controller<br/>Read-only Investigation]
    F --> G[Human Approval<br/>Streamlit Dashboard]
    G --> H[Audit Log]
    G --> I[Case Memory]

Design principle:

LedgerPilot deliberately separates financial correctness from AI reasoning.

-Deterministic software performs validation, reconciliation, monetary calculations, exception classification, and priority scoring.
-The AI Controller investigates exceptions, retrieves evidence, explains findings, and recommends actions.
-The AI cannot modify financial records or resolve exceptions directly.
-Human approval is required before any exception resolution is recorded.
-Every human action is recorded in the Audit Log, while approved resolutions can be retained as Case Memory for future investigations.

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

LedgerPilot is evaluated on a synthetic dataset containing payments, settlements, and invoices, including deliberately injected anomaly scenarios and formatting variations.

The generated dataset includes a ground-truth answer key:

data/generated/ground_truth_labels.csv

The evaluation runs the same reconciliation pipeline used by the application and compares detected exceptions against the known ground truth.

The evaluation measures:

-Precision
-Recall
-F1 score
-Macro and weighted category performance
-Detection rate
-False positives and false negatives
-Processing throughput
-Unresolved monetary exposure

This prevents the system from being evaluated using only cherry-picked successful matches.

The evaluation methodology, category-level results, known weaknesses, and tuning history are documented in docs/evaluation.md.

## 11. Results

Evaluation is performed against the generated ground-truth labels after running the full reconciliation pipeline.

| Metric | Result |
|---|---:|
| Overall Precision | 0.688 |
| Overall Recall | 0.934 |
| Overall F1 | 0.792 |
| Macro F1 | 0.820 |
| Weighted F1 | 0.814 |
| Detection Rate | 98.1% |
| Records Processed | 3,211 |
| Processing Throughput | ~24,089 records/sec |

The evaluation also reports category-level performance and explicitly tracks false positives, false negatives, unresolved monetary exposure, and processing throughput.

See [`docs/evaluation.md`](docs/evaluation.md) for the complete methodology, category-level results, limitations, and tuning history.

## 12. Screenshots

The LedgerPilot dashboard provides three main views:

-Exception Queue — prioritized reconciliation exceptions with severity, affected amount, confidence, and investigation actions.
-Audit Trail — records human approvals, rejections, and investigation evidence.
-Evaluation — displays reconciliation and model evaluation metrics.

The dashboard is implemented using Streamlit and connects directly to the LedgerPilot service layer and SQLite database.

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
python run.py setup
```

Generates the synthetic financial dataset, runs the reconciliation pipeline, and loads the resulting records and exceptions into SQLite.

```bash
python run.py dashboard
```

Launches the Streamlit dashboard.

```bash
python run.py test
```

Runs the automated test suite.

```bash
python run.py eval
```

Re-runs the pipeline and reports evaluation metrics against the generated ground-truth labels.

```

### Verification

The project was verified through automated tests and direct testing of the reconciliation, database, tool, and AI-controller components.

The final test suite contains **22 automated tests**, covering core reconciliation logic, exception handling, database operations, controller behavior, and related functionality.
```

## 16. Demo workflow

1.Run python run.py setup to generate the synthetic financial data and populate SQLite.
2.Run python run.py dashboard to launch the Streamlit interface.
3.Review the top-level reconciliation metrics.
4.Open the Exception Queue and select a high-priority exception.
5.Ask the AI Finance Controller to investigate the selected exception.
6.Review the structured FACT / INFERENCE / RECOMMENDATION response and supporting evidence.
7.As the human reviewer, Approve, Reject, or request further investigation.
8.Open the Audit Trail to verify that the human action and supporting evidence were recorded.
9.Open the Evaluation tab to review the reconciliation and evaluation metrics.

The complete workflow demonstrates the core design principle: the system can investigate and recommend, but a human remains responsible for the final financial decision.

## 17. Limitations

-MISSING_SETTLEMENT and INVALID_REFERENCE remain weaker exception categories and would benefit from additional tuning and broader test coverage.
-The LLM-powered investigation path depends on an external API key. The deterministic fallback remains available when an API key is not configured.
-The current system does not perform fuzzy customer-level entity resolution.
-The dashboard is designed as a single-user demonstration application rather than a production multi-user finance platform.
-The current architecture does not include a separate FastAPI layer; the Streamlit dashboard communicates with the service layer in-process.
-The synthetic dataset and injected anomalies are designed for evaluation and demonstration rather than representing real production financial data.

## 18. Future improvements

-Improve detection and precision for weaker exception categories through additional evaluation and tuning.
-Add fuzzy customer-level entity resolution for cases where customer identifiers differ across sources.
-Integrate native LLM function/tool calling for more flexible evidence-driven investigations.
-Add a FastAPI service layer to support programmatic access and multi-client applications.
-Expand evaluation with additional operational metrics such as false-positive rate, processing latency, and investigation quality.
-Add role-based access control and stronger audit/security controls for production deployment.
-Extend the system to support additional financial operations such as settlement forecasting and tax-line reconciliation.
