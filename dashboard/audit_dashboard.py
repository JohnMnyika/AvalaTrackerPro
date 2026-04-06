"""
Audit Dashboard Module - Visualization and management of payment audit trail and duplicates.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List

import pandas as pd
import streamlit as st


def display_audit_stats(stats: Dict[str, Any]) -> None:
    """Display audit statistics in a formatted way."""
    st.markdown("### 📊 Audit Statistics")

    # Audit entries stats
    audit_stats = stats.get("audit", {})
    flagged_stats = stats.get("flagged_payments", {})
    dup_stats = stats.get("duplicates", {})

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric(
            "Total Audit Entries",
            audit_stats.get("total_entries", 0),
            help="Total number of audit log entries",
        )

    with col2:
        st.metric(
            "Duplicate Updates",
            audit_stats.get("duplicate_update_entries", 0),
            help="Updates triggered by duplicate detection",
        )

    with col3:
        st.metric(
            "Flagged Payments",
            flagged_stats.get("total", 0),
            help="Total payments marked with flags",
        )

    with col4:
        st.metric(
            "Pending Duplicates",
            dup_stats.get("pending_review", 0),
            help="Duplicates awaiting reconciliation",
        )

    # Action breakdown
    st.markdown("#### Audit Actions Breakdown")
    action_counts = audit_stats.get("by_action", {})
    if action_counts:
        action_df = pd.DataFrame(
            list(action_counts.items()), columns=["Action", "Count"]
        )
        st.bar_chart(action_df.set_index("Action"))
    else:
        st.info("No audit entries yet")


def display_duplicate_detection(detection_result: Dict[str, Any]) -> None:
    """Display duplicate detection results."""
    st.markdown("### 🔍 Duplicate Detection Results")

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("Total Duplicates Detected", detection_result.get("total_duplicates", 0))

    with col2:
        st.metric(
            "Pending Review",
            detection_result.get("pending_review", 0),
            delta="⚠️ Needs Action" if detection_result.get("pending_review", 0) > 0 else "✓ All Clear",
        )

    with col3:
        st.metric("Merged", detection_result.get("merged", 0))

    with col4:
        st.metric("Ignored", detection_result.get("ignored", 0))

    # Display pending duplicates
    duplicates = detection_result.get("duplicates", [])
    pending_duplicates = [d for d in duplicates if d["reconciliation_status"] == "pending"]

    if pending_duplicates:
        st.markdown("#### ⚠️ Pending Duplicates")
        st.warning(f"{len(pending_duplicates)} duplicate(s) awaiting reconciliation")

        for dup in pending_duplicates:
            with st.expander(
                f"🔗 {dup['primary_payment_type'].upper()} #{dup['primary_payment_id']} ↔ #{dup['duplicate_payment_id']}"
            ):
                col_left, col_right = st.columns(2)

                with col_left:
                    st.markdown(
                        f"""
                    **Match Key:** `{dup['match_key']}`
                    
                    **Type:** {dup['primary_payment_type']} ↔ {dup['duplicate_payment_type']}
                    
                    **Primary ID:** {dup['primary_payment_id']}
                    
                    **Duplicate ID:** {dup['duplicate_payment_id']}
                    """
                    )

                with col_right:
                    st.markdown(
                        f"""
                    **Similarity:** {dup['similarity_score']}
                    
                    **Has Differences:** {'🔴 Yes' if dup['has_value_difference'] else '🟢 No'}
                    
                    **Detected:** {dup['detected_at'].split('T')[0]}
                    
                    **Status:** {dup['reconciliation_status'].upper()}
                    """
                    )

                if dup.get("value_difference_summary"):
                    st.markdown("**Value Differences:**")
                    diff_data = json.loads(dup["value_difference_summary"])
                    st.json(diff_data)

                # Reconciliation action
                st.markdown("**Reconciliation Action:**")
                col_action1, col_action2, col_action3 = st.columns(3)

                with col_action1:
                    if st.button(
                        "✓ Keep Primary",
                        key=f"dup_keep_{dup['id']}",
                        help="Delete duplicate, keep primary as-is",
                    ):
                        # This would trigger an API call
                        st.session_state[f"action_{dup['id']}"] = "kept_primary"

                with col_action2:
                    if st.button(
                        "📈 Use Higher Value",
                        key=f"dup_higher_{dup['id']}",
                        help="Update primary with higher value",
                    ):
                        st.session_state[f"action_{dup['id']}"] = "use_higher_value"

                with col_action3:
                    if st.button(
                        "🏷️ Ignore",
                        key=f"dup_ignore_{dup['id']}",
                        help="Mark as ignored but keep both records",
                    ):
                        st.session_state[f"action_{dup['id']}"] = "ignore"

    else:
        st.success("✅ No pending duplicates detected")


def display_flagged_payments(flagged_data: Dict[str, Any]) -> None:
    """Display flagged payments."""
    st.markdown("### 🚩 Flagged Payments")

    total_flagged = flagged_data.get("total_flagged", 0)

    if total_flagged == 0:
        st.success("✅ No flagged payments")
        return

    st.warning(f"⚠️ {total_flagged} payment(s) flagged for review")

    tab1, tab2 = st.tabs(
        ["Batch Payments", "History Payments"]
    )

    with tab1:
        batches = flagged_data.get("batches", [])
        if batches:
            batch_df = pd.DataFrame(batches)
            # Convert columns to appropriate types
            batch_df = batch_df[
                ["batch_name", "amount_usd", "flag_reason", "flagged_at", "created_at"]
            ]
            st.dataframe(batch_df, use_container_width=True, hide_index=True)
        else:
            st.info("No flagged batch payments")

    with tab2:
        histories = flagged_data.get("history", [])
        if histories:
            hist_df = pd.DataFrame(histories)
            hist_df = hist_df[
                ["date", "amount_usd", "amount_kes", "status", "flag_reason", "flagged_at"]
            ]
            st.dataframe(hist_df, use_container_width=True, hide_index=True)
        else:
            st.info("No flagged history payments")


def display_audit_trail(audit_logs: Dict[str, Any]) -> None:
    """Display the complete audit trail."""
    st.markdown("### 📝 Audit Trail")

    items = audit_logs.get("items", [])
    total = audit_logs.get("total", 0)

    if total == 0:
        st.info("No audit entries yet")
        return

    st.markdown(f"Showing {len(items)} of {total} entries")

    # Filter options
    col1, col2, col3 = st.columns(3)

    with col1:
        filter_type = st.selectbox(
            "Filter by Type",
            ["All", "batch", "history"],
            key="audit_type_filter",
        )

    with col2:
        filter_action = st.selectbox(
            "Filter by Action",
            ["All", "created", "updated", "flagged", "reconciled"],
            key="audit_action_filter",
        )

    with col3:
        filter_duplicates = st.checkbox("Duplicate Updates Only", key="audit_dup_filter")

    # Apply filters locally (since we already have the data)
    filtered_items = items

    if filter_type != "All":
        filtered_items = [i for i in filtered_items if i["payment_type"] == filter_type]

    if filter_action != "All":
        filtered_items = [i for i in filtered_items if i["action"] == filter_action]

    if filter_duplicates:
        filtered_items = [i for i in filtered_items if i["is_duplicate_update"] == 1]

    # Display entries in reverse chronological order
    for entry in filtered_items:
        with st.expander(
            f"{entry['action'].upper()} | {entry['payment_type']} #{entry['payment_id']} | {entry['audit_timestamp'].split('T')[0]}"
        ):
            col_left, col_right = st.columns(2)

            with col_left:
                st.markdown(
                    f"""
                **Timestamp:** {entry['audit_timestamp']}
                
                **Payment Type:** {entry['payment_type']}
                
                **Payment ID:** {entry['payment_id']}
                
                **Action:** {entry['action']}
                """
                )

            with col_right:
                st.markdown(
                    f"""
                **User:** {entry['audit_user']}
                
                **Source:** {entry['audit_source']}
                
                **Duplicate Update:** {'Yes 🔴' if entry['is_duplicate_update'] else 'No 🟢'}
                
                **Summary:** {entry['change_summary']}
                """
                )

            if entry.get("old_values") or entry.get("new_values"):
                st.markdown("**Changes:**")
                col_old, col_new = st.columns(2)

                with col_old:
                    if entry.get("old_values"):
                        st.markdown("*Old Values:*")
                        st.json(json.loads(entry["old_values"]))

                with col_new:
                    if entry.get("new_values"):
                        st.markdown("*New Values:*")
                        st.json(json.loads(entry["new_values"]))


def display_audit_dashboard() -> None:
    """Main audit dashboard view."""
    try:
        # Fetch stats
        response_stats = st.session_state.get("api_response_stats")
        if not response_stats:
            st.info("Loading audit data...")
            return

        # Fetch duplicate detection results
        response_duplicates = st.session_state.get("api_response_duplicates")
        if not response_duplicates:
            st.info("Loading duplicate detection...")
            return

        # Fetch flagged payments
        response_flagged = st.session_state.get("api_response_flagged")
        if not response_flagged:
            st.info("Loading flagged payments...")
            return

        # Fetch audit trail
        response_audit = st.session_state.get("api_response_audit")
        if not response_audit:
            st.info("Loading audit trail...")
            return

        # Display all sections
        display_audit_stats(response_stats)
        st.divider()

        display_duplicate_detection(response_duplicates)
        st.divider()

        display_flagged_payments(response_flagged)
        st.divider()

        display_audit_trail(response_audit)

        # Add refresh button
        st.markdown("---")
        if st.button("🔄 Refresh Audit Data"):
            st.rerun()

    except Exception as e:
        st.error(f"Error displaying audit dashboard: {str(e)}")
        st.write(e)
