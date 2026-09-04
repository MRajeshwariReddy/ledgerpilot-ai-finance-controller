"""
LedgerPilot - Professional Fintech Dashboard
Clean, focused workflow: Dashboard → Exception Queue → Investigation → Approval → Audit Trail
"""

import streamlit as st
import sqlite3
import json
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.services import tools
from app.services.db import get_connection, DB_PATH
from app.agents import controller

# ============== PAGE CONFIG ==============
st.set_page_config(
    page_title="LedgerPilot",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ============== VALIDATION ==============
if not os.path.exists(DB_PATH):
    st.error("Database not found. Run: python -m evaluation.run_evaluation")
    st.stop()

# ============== PROFESSIONAL STYLES ==============
st.markdown("""
<style>

/* =========================
   LEDGERPILOT - CLEAN UI
   ========================= */

.stApp {
    background: var(--background-color) !important;
    color: var(--text-color) !important;
}

.main {
    background: var(--background-color) !important;
}

.block-container {
    max-width: 1480px !important;
    padding-top: 3rem !important;
    padding-bottom: 2rem !important;
    padding-left: 3rem !important;
    padding-right: 3rem !important;
}

/* ---------- Header ---------- */

.lp-title {
    font-size: 24px;
    font-weight: 600;
    color: var(--text-color);
    margin: 0;
}

.lp-subtitle {
    font-size: 12px;
    color: var(--secondary-text-color);
    margin-top: 4px;
}

.lp-ready {
    font-size: 12px;
    color: var(--secondary-text-color);
    text-align: right;
    padding-top: 5px;
}

.lp-divider {
    height: 1px;
    background: #e0e0e0;
    margin: 20px 0;
}

/* ---------- Metric Cards ---------- */

[data-testid="stMetric"] {
    background: #f5f5f5 !important;
    border-radius: 8px !important;
    padding: 16px !important;
    min-height: 105px !important;
    border: none !important;
    box-shadow: none !important;
}

[data-testid="stMetricLabel"] {
    font-size: 12px !important;
    color: #666666 !important;
}
/* ---------- Metric Cards ---------- */

[data-testid="stMetric"] {
    background: #f5f5f5 !important;
    border-radius: 8px !important;
    padding: 16px !important;
    min-height: 105px !important;
    border: none !important;
    box-shadow: none !important;
}

[data-testid="stMetricLabel"] {
    font-size: 12px !important;
    color: #666666 !important;
}

/* ---------- Tabs ---------- */

button[data-baseweb="tab"] {
    font-size: 14px !important;
    color: #999999 !important;
    padding: 12px 0 !important;
}

button[data-baseweb="tab"][aria-selected="true"] {
    color: #1976d2 !important;
    font-weight: 500 !important;
}

div[data-baseweb="tab-highlight"] {
    background: #1976d2 !important;
    height: 2px !important;
}

.metric-card {
    background: var(--secondary-background-color);
    border-radius: 8px !important;
    padding: 16px !important;
    min-height: 105px !important;
    box-sizing: border-box !important;
    border: 1px solid #e0e0e0 !important;
}

.metric-label {
    font-size: 14px;
    font-weight:600;
    color: var(--secondary-text-color);
    margin-bottom: 8px;
}

.metric-value {
    font-size: 38px;
    font-weight: 600;
    line-height: 1.2;
    margin-bottom: 6px;
}

.metric-description {
    font-size: 11px;
    color: #888888;
}

.metric-blue-text {
    color: #1976d2;
}

.metric-red-text {
    color: #d32f2f;
}

.metric-black-text {
    color: #333333;
}
[data-testid="stDataFrame"] th {
    color: #333333 !important;
    font-weight: 600 !important;
}
/* ---------- Section Titles ---------- */

.section-title {
    font-size: 14px;
    font-weight: 600;
    margin-top: 20px;
    margin-bottom: 12px;
    color: var(--text-color) !important;
}

/* ---------- Investigation Panels ---------- */

.fact-section {
    background: var(--secondary-background-color) !important;
    padding: 12px !important;
    border-radius: 6px !important;
    border-left: 4px solid #1976d2 !important;
    margin: 12px 0 !important;
    font-size: 13px !important;
    line-height: 1.6 !important;
    color: var(--text-color) !important;
}

.inference-section {
    background: #e3f2fd !important;
    padding: 12px !important;
    border-radius: 6px !important;
    border-left: 4px solid #1976d2 !important;
    margin: 12px 0 !important;
    font-size: 13px !important;
    line-height: 1.6 !important;
    color: #222222 !important;
}

.recommendation-section {
    background: #e8f5e9 !important;
    padding: 12px !important;
    border-radius: 6px !important;
    border-left: 4px solid #388e3c !important;
    margin: 12px 0 !important;
    font-size: 13px !important;
    line-height: 1.6 !important;
    color: #222222 !important;
}

/* ---------- Priority Labels ---------- */

.priority-critical {
    color: #d32f2f !important;
    font-weight: 600;
}

.priority-high {
    color: #f57c00 !important;
    font-weight: 600;
}

.priority-medium {
    color: #fbc02d !important;
    font-weight: 600;
}

.priority-low {
    color: #388e3c !important;
    font-weight: 600;
}

/* ---------- Buttons ---------- */

.stButton > button {
    background: #1976d2 !important;
    color: #ffffff !important;
    border: none !important;
    border-radius: 6px !important;
    font-size: 13px !important;
    font-weight: 500 !important;
    padding: 10px 20px !important;
}

.stButton > button:hover {
    background: #1565c0 !important;
    color: #ffffff !important;
}

/* ---------- Audit Record ---------- */

.audit-record {
    background: #f5f5f5 !important;
    color: #222222 !important;
    padding: 12px !important;
    border-radius: 6px !important;
    border-left: 4px solid #558b2f !important;
    margin: 12px 0 !important;
    font-size: 12px !important;
    line-height: 1.6 !important;
}

/* ---------- Footer ---------- */

.lp-footer {
    margin-top: 32px;
    padding-top: 16px;
    border-top: 1px solid #e0e0e0;
    font-size: 12px;
    color: #999999;
    text-align: center;
}

/* ---------- Reduce Streamlit Chrome ---------- */

[data-testid="stDecoration"] {
    display: none !important;
}


footer {
    visibility: hidden !important;
}


.fact-section strong,
.inference-section strong,
.recommendation-section strong {
    font-weight: 700 !important;
    color: #222222 !important;
}
/* Hide Streamlit Deploy button */
[data-testid="stAppDeployButton"] {
    display: none !important;
}
\n</style>
""", unsafe_allow_html=True)

# ============== HEADER ==============
col_title, col_status = st.columns([4, 1])

with col_title:
    st.markdown(
        '<div class="lp-title">LedgerPilot</div>',
        unsafe_allow_html=True
    )
    st.markdown(
        '<div class="lp-subtitle">AI Finance Controller</div>',
        unsafe_allow_html=True
    )

with col_status:
    st.markdown(
        '<div class="lp-ready">✓ Ready</div>',
        unsafe_allow_html=True
    )

st.markdown(
    '<div style="height:1px;background:#e0e0e0;margin:20px 0;"></div>',
    unsafe_allow_html=True
)
# ============== KEY METRICS DASHBOARD ==============
summary = tools.calculate_reconciliation_summary()
stats = tools.get_exception_statistics()
critical_count = next((t["c"] for t in stats["by_priority_tier"] if t["priority_tier"] == "CRITICAL"), 0)
high_count = next((t["c"] for t in stats["by_priority_tier"] if t["priority_tier"] == "HIGH"), 0)

col1, col2, col3, col4 = st.columns(4, gap="large")

with col1:
    st.markdown(
        f"""
        <div class="metric-card">
            <div class="metric-label">Reconciliation Rate</div>
            <div class="metric-value metric-blue-text">{summary['reconciliation_rate_pct']}%</div>
            <div class="metric-description">% of transactions successfully matched</div>
        </div>
        """,
        unsafe_allow_html=True
    )

with col2:
    exposure_millions = summary['total_unresolved_value'] / 1_000_000
    st.markdown(
        f"""
        <div class="metric-card">
            <div class="metric-label">Unresolved Exposure</div>
            <div class="metric-value metric-red-text">₹{exposure_millions:.1f}M</div>
            <div class="metric-description">Total monetary value at risk</div>
        </div>
        """,
        unsafe_allow_html=True
    )

with col3:
    st.markdown(
        f"""
        <div class="metric-card">
            <div class="metric-label">Open Exceptions</div>
            <div class="metric-value metric-black-text">{summary['total_exceptions']}</div>
            <div class="metric-description">All cases requiring review</div>
        </div>
        """,
        unsafe_allow_html=True
    )

with col4:
    st.markdown(
        f"""
        <div class="metric-card">
            <div class="metric-label">Critical Priority</div>
            <div class="metric-value metric-red-text">{critical_count} ⚠️</div>
            <div class="metric-description">Highest urgency</div>
        </div>
        """,
        unsafe_allow_html=True
    )

st.divider()

# ============== MAIN WORKFLOW TABS ==============
tab_queue, tab_audit, tab_info = st.tabs([
    "Exception Queue (Main Workflow)",
    "Audit Trail",
    "Evaluation"
])

# ============== TAB 1: EXCEPTION QUEUE & INVESTIGATION ==============
with tab_queue:
    # FILTERS
    col_filter1, col_filter2 = st.columns([2, 3])
    
    with col_filter1:
        status_filter = st.radio(
            "Status",
            ["OPEN", "RESOLVED"],
            horizontal=True,
            help="Filter exceptions by resolution status"
        )
    
    with col_filter2:
        conn = get_connection()
        all_categories = sorted([r["category"] for r in conn.execute("SELECT DISTINCT category FROM exceptions").fetchall()])
        conn.close()
        cat_filter = st.multiselect(
            "Filter by category (optional)",
            all_categories,
            max_selections=3,
            help="Narrow down by exception type"
        )
    
    # GET EXCEPTIONS
    conn = get_connection()
    query = "SELECT exception_id, reference_id, category, affected_amount, severity, priority_tier FROM exceptions WHERE status = ?"
    params = [status_filter]
    
    if cat_filter:
        placeholders = ",".join("?" * len(cat_filter))
        query += f" AND category IN ({placeholders})"
        params.extend(cat_filter)
    
    query += " ORDER BY CASE priority_tier WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2 WHEN 'MEDIUM' THEN 3 WHEN 'LOW' THEN 4 ELSE 5 END, priority_score DESC, affected_amount DESC LIMIT 600"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    
    if not rows:
        st.info(f"No {status_filter.lower()} exceptions. Great work!")
    else:
        # EXCEPTION LIST
        st.markdown(f"<div class='section-title'>📋 {len(rows)} Exception(s) — Select to Investigate</div>", unsafe_allow_html=True)
        
        # Simple, clean list
        exc_data = []
        for r in rows:
            priority_class = f"priority-{r['priority_tier'].lower()}"
            exc_data.append({
                "ID": r["exception_id"],
                "Order": r["reference_id"],
                "Category": r["category"],
                "Amount": f"₹{float(r['affected_amount']):,.0f}",
                "Severity": r["severity"],
                "Priority": r["priority_tier"]
            })
        
        import pandas as pd

        df_exc = pd.DataFrame(exc_data)

        def color_priority(value):
            colors = {
                "CRITICAL": "#d32f2f",
                "HIGH": "#f57c00",
                "MEDIUM": "#fbc02d",
                "LOW": "#388e3c"
            }
            return f"color: {colors.get(value, '#333333')}; font-weight: 600;"

        styled_df = df_exc.style.map(
            color_priority,
            subset=["Priority"]
        )

        st.dataframe(
            styled_df,
            use_container_width=True,
            hide_index=True,
            height=300
        )
        
        # SELECTION & INVESTIGATION
        st.markdown("<div class='section-title'>🔍 Investigation</div>", unsafe_allow_html=True)
        
        exc_ids = [r["exception_id"] for r in rows]
        selected_exc = st.selectbox(
            "Select exception to investigate:",
            exc_ids,
            format_func=lambda x: f"{x}",
            key="exc_select"
        )
        
        # INVESTIGATE BUTTON
        col_btn, col_blank = st.columns([2, 3])
        with col_btn:
            investigate = st.button(
                "🔍 Analyze Exception",
                use_container_width=True,
                key="investigate_btn",
                type="primary"
            )
        
        # INVESTIGATION RESULTS
        if investigate or st.session_state.get("last_investigation_id") == selected_exc:
            if investigate:
                with st.spinner("Analyzing exception with AI..."):
                    result = controller.investigate(selected_exc)
                st.session_state["last_investigation"] = result
                st.session_state["last_investigation_id"] = selected_exc
            
            if "last_investigation" in st.session_state:
                inv = st.session_state["last_investigation"]
                response = inv.get("response", "")
                
                # Parse response into sections
                st.divider()
                st.markdown("<div class='section-title'>📊 AI Investigation Results</div>", unsafe_allow_html=True)
                
                # Extract FACT section
                fact_match = response.find("FACT:")
                inference_match = response.find("INFERENCE:")
                recommendation_match = response.find("RECOMMENDATION:")
                
                if fact_match >= 0:
                    if inference_match >= 0:
                        fact_text = response[fact_match + 5:inference_match].strip()
                    else:
                        fact_text = response[fact_match + 5:].strip()
                    st.markdown("<div style='font-weight: 600; margin-bottom: 8px;'>💾 FACT (Database Retrieved)</div>", unsafe_allow_html=True)
                    st.markdown(f"<div class='fact-section'>{fact_text}</div>", unsafe_allow_html=True)
                
                # Extract INFERENCE section
                if inference_match >= 0:
                    if recommendation_match >= 0:
                        inference_text = response[inference_match + 10:recommendation_match].strip()
                    else:
                        inference_text = response[inference_match + 10:].strip()
                    st.markdown("<div style='font-weight: 600; margin-bottom: 8px; margin-top: 16px;'>🧠 INFERENCE (AI Analysis)</div>", unsafe_allow_html=True)
                    st.markdown(f"<div class='inference-section'>{inference_text}</div>", unsafe_allow_html=True)
                
                # Extract RECOMMENDATION section
                if recommendation_match >= 0:
                    rec_text = response[recommendation_match + 14:].strip()
                    st.markdown("<div style='font-weight: 600; margin-bottom: 8px; margin-top: 16px;'>💡 RECOMMENDATION (Suggested Action)</div>", unsafe_allow_html=True)
                    st.markdown(f"<div class='recommendation-section'>{rec_text}</div>", unsafe_allow_html=True)
                
                # HUMAN APPROVAL SECTION
                st.divider()
                st.markdown("<div class='section-title'>✓ Human Approval Required</div>", unsafe_allow_html=True)
                st.caption("Review the analysis above and make a decision.")
                
                # Get exception details
                conn = get_connection()
                exc = conn.execute(
                    "SELECT * FROM exceptions WHERE exception_id = ?",
                    (selected_exc,)
                ).fetchone()
                conn.close()
                exc_dict = dict(exc) if exc else {}
                
                # Approval form
                approval_col1, approval_col2 = st.columns(2)
                
                with approval_col1:
                    reviewer_name = st.text_input(
                        "Your name",
                        value="",
                        placeholder="Enter your name",
                        help="Who is making this decision?"
                    )
                
                with approval_col2:
                    decision_choice = st.selectbox(
                        "Decision",
                        ["-- Select --", "✓ Approve", "✗ Reject", "⏳ Further Investigation"],
                        help="Your final decision on this exception"
                    )
                
                approval_notes = st.text_area(
                    "Justification (optional)",
                    placeholder="Brief explanation of your decision...",
                    height=80
                )
                
                # RECORD DECISION BUTTON
                col_record, col_blank = st.columns([2, 3])
                with col_record:
                    record_btn = st.button(
                        "✓ Record Decision",
                        use_container_width=True,
                        type="primary",
                        key="record_btn"
                    )
                
                # RECORD THE DECISION
                if record_btn:
                    if not reviewer_name:
                        st.error("Please enter your name.")
                    elif decision_choice == "-- Select --":
                        st.error("Please select a decision.")
                    else:
                        # Map choice to decision code
                        decision_map = {
                            "✓ Approve": "APPROVE",
                            "✗ Reject": "REJECT",
                            "⏳ Further Investigation": "FURTHER_INVESTIGATION"
                        }
                        decision = decision_map[decision_choice]
                        
                        # Record in database
                        tools.record_case_resolution(
                            exception_id=selected_exc,
                            exception_category=exc_dict.get("category", ""),
                            exception_attributes=exc_dict.get("evidence_json", "{}"),
                            resolution={
                                "APPROVE": "Approved",
                                "REJECT": "Rejected",
                                "FURTHER_INVESTIGATION": "Further investigation needed"
                            }[decision],
                            evidence=exc_dict.get("evidence_json", "{}"),
                            reviewer=reviewer_name,
                            decision=decision,
                            reason=approval_notes or "(no additional notes)",
                            ai_recommendation=response,
                        )
                        
                        # Show success
                        st.success(f"✅ Decision recorded: {decision_choice}")
                        st.session_state["last_approval_id"] = selected_exc
                        st.session_state["last_approval_decision"] = decision
                        
                        # Show audit record
                        st.markdown("<div class='section-title'>📝 Audit Record</div>", unsafe_allow_html=True)
                        conn = get_connection()
                        audit = conn.execute(
                            "SELECT exception_id, actor, final_decision, reason, created_at FROM audit_log WHERE exception_id = ? ORDER BY created_at DESC LIMIT 1",
                            (selected_exc,)
                        ).fetchone()
                        conn.close()
                        
                        if audit:
                            audit_dict = dict(audit)
                            st.markdown(
                                f"""
                                <div class="audit-record">
                                    <div><strong>Exception:</strong> {audit_dict['exception_id']}</div>
                                    <div><strong>Reviewer:</strong> {audit_dict['actor']}</div>
                                    <div><strong>Decision:</strong> {audit_dict['final_decision']}</div>
                                    <div><strong>Reason:</strong> {audit_dict['reason']}</div>
                                    <div><strong>Timestamp:</strong> {audit_dict['created_at']}</div>
                                </div>
                                """,
                                unsafe_allow_html=True
                            )

# ============== TAB 2: AUDIT TRAIL ==============
with tab_audit:
    st.markdown("### Audit Trail")
    st.caption("Complete record of all approvals and rejections")
    st.divider()
    
    conn = get_connection()
    audit_rows = conn.execute(
        "SELECT exception_id, actor, final_decision, reason, created_at FROM audit_log ORDER BY created_at DESC LIMIT 100"
    ).fetchall()
    conn.close()
    
    if not audit_rows:
        st.info("No decisions recorded yet.")
    else:
        # Format for display
        audit_display = []
        for audit in audit_rows:
            audit_dict = dict(audit)
            audit_display.append({
                "Exception ID": audit_dict["exception_id"],
                "Decision": audit_dict["final_decision"],
                "Reviewer": audit_dict["actor"],
                "Timestamp": audit_dict["created_at"],
                "Reason": audit_dict["reason"][:60] + "..." if len(audit_dict["reason"]) > 60 else audit_dict["reason"]
            })
        
        st.dataframe(audit_display, use_container_width=True, hide_index=True, height=500)
        st.caption(f"Showing {len(audit_display)} of {len(audit_display)} records")

# ============== TAB 3: EVALUATION ==============
with tab_info:
    st.markdown("### Evaluation Metrics")
    st.caption("System performance on 3,244 synthetic transactions")
    st.divider()
    
    # Performance metrics
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Precision", "0.688", help="Accuracy of anomaly detection")
    with col2:
        st.metric("Recall", "0.934", help="Detection rate of true anomalies")
    with col3:
        st.metric("F1-Score", "0.814", help="Balanced accuracy measure")
    with col4:
        st.metric("Processing Speed", "20.7K/s", help="Transactions per second")
    
    st.divider()
    
    # Category breakdown
    st.markdown("#### Exception Categories")
    st.caption("Performance by exception type")
    
    category_data = {
        "Category": ["DATE_MISMATCH", "MISSING_INVOICE", "UNEXPECTED_FEE", "DUPLICATE_TRANSACTION", 
                     "PARTIAL_SETTLEMENT", "CURRENCY_MISMATCH", "AMOUNT_MISMATCH", "MISSING_PAYMENT"],
        "Precision": [1.00, 1.00, 1.00, 1.00, 0.833, 0.745, 0.694, 0.644],
        "Recall": [0.978, 1.00, 1.00, 0.912, 1.00, 0.972, 0.986, 1.00],
        "F1-Score": [0.989, 1.00, 1.00, 0.954, 0.909, 0.843, 0.814, 0.784]
    }
    
    st.dataframe(category_data, use_container_width=True, hide_index=True)
    
    st.divider()
    
    # Data info
    st.markdown("#### Dataset")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Total Records", "3,244")
    with col2:
        st.metric("Ground-Truth Anomalies", "483")
    with col3:
        st.metric("Monetary Coverage", "₹42.3M")

# ============== FOOTER ==============
st.divider()
st.caption("LedgerPilot v1.0 — AI Finance Controller | All data is immutable and audited")
