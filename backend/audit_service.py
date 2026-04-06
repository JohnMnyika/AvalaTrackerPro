"""
Audit Service Module - Handles payment reconciliation, duplicate detection, and audit trail management.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from .models import (
    PaymentAuditLog,
    PaymentBatch,
    PaymentDuplicate,
    PaymentHistory,
    PaymentReconciliation,
)


class DuplicateDetector:
    """Detects duplicate payment entries across batches and history."""

    @staticmethod
    def check_batch_duplicates(db: Session) -> List[PaymentDuplicate]:
        """
        Check for duplicate PaymentBatch entries.
        Duplicates are identified when batch_name matches exactly.
        """
        duplicates = []

        # Get all batches grouped by normalized name
        batches = db.query(PaymentBatch).all()
        batch_dict: Dict[str, List[PaymentBatch]] = {}

        for batch in batches:
            key = batch.batch_name.lower().strip()
            if key not in batch_dict:
                batch_dict[key] = []
            batch_dict[key].append(batch)

        # Find duplicates within each group
        for key, batch_list in batch_dict.items():
            if len(batch_list) > 1:
                # Sort by ID to establish primary vs duplicate
                batch_list.sort(key=lambda x: x.id)
                primary = batch_list[0]

                for duplicate_batch in batch_list[1:]:
                    # Check if duplicate already exists in DB
                    existing = (
                        db.query(PaymentDuplicate)
                        .filter(
                            PaymentDuplicate.primary_payment_id == primary.id,
                            PaymentDuplicate.duplicate_payment_id == duplicate_batch.id,
                            PaymentDuplicate.primary_payment_type == "batch",
                        )
                        .first()
                    )

                    if not existing:
                        has_diff = primary.amount_usd != duplicate_batch.amount_usd
                        diff_summary = None
                        if has_diff:
                            diff_summary = json.dumps(
                                {
                                    "primary_amount": primary.amount_usd,
                                    "duplicate_amount": duplicate_batch.amount_usd,
                                }
                            )

                        dup_record = PaymentDuplicate(
                            primary_payment_type="batch",
                            primary_payment_id=primary.id,
                            duplicate_payment_type="batch",
                            duplicate_payment_id=duplicate_batch.id,
                            match_key=key,
                            similarity_score=1.0,
                            has_value_difference=1 if has_diff else 0,
                            value_difference_summary=diff_summary,
                            reconciliation_status="pending",
                        )
                        db.add(dup_record)
                        duplicates.append(dup_record)

        db.commit()
        return duplicates

    @staticmethod
    def check_history_duplicates(db: Session) -> List[PaymentDuplicate]:
        """
        Check for duplicate PaymentHistory entries.
        Duplicates are identified when date matches exactly.
        """
        duplicates = []

        # Get all history records grouped by date
        histories = db.query(PaymentHistory).all()
        history_dict: Dict[str, List[PaymentHistory]] = {}

        for hist in histories:
            key = str(hist.date)
            if key not in history_dict:
                history_dict[key] = []
            history_dict[key].append(hist)

        # Find duplicates within each group
        for key, hist_list in history_dict.items():
            if len(hist_list) > 1:
                # Sort by ID to establish primary vs duplicate
                hist_list.sort(key=lambda x: x.id)
                primary = hist_list[0]

                for duplicate_hist in hist_list[1:]:
                    # Check if duplicate already exists in DB
                    existing = (
                        db.query(PaymentDuplicate)
                        .filter(
                            PaymentDuplicate.primary_payment_id == primary.id,
                            PaymentDuplicate.duplicate_payment_id == duplicate_hist.id,
                            PaymentDuplicate.primary_payment_type == "history",
                        )
                        .first()
                    )

                    if not existing:
                        has_diff = (
                            primary.amount_usd != duplicate_hist.amount_usd
                            or primary.amount_kes != duplicate_hist.amount_kes
                            or primary.status != duplicate_hist.status
                        )
                        diff_summary = None
                        if has_diff:
                            diff_summary = json.dumps(
                                {
                                    "primary_usd": primary.amount_usd,
                                    "duplicate_usd": duplicate_hist.amount_usd,
                                    "primary_kes": primary.amount_kes,
                                    "duplicate_kes": duplicate_hist.amount_kes,
                                    "primary_status": primary.status,
                                    "duplicate_status": duplicate_hist.status,
                                }
                            )

                        dup_record = PaymentDuplicate(
                            primary_payment_type="history",
                            primary_payment_id=primary.id,
                            duplicate_payment_type="history",
                            duplicate_payment_id=duplicate_hist.id,
                            match_key=key,
                            similarity_score=1.0,
                            has_value_difference=1 if has_diff else 0,
                            value_difference_summary=diff_summary,
                            reconciliation_status="pending",
                        )
                        db.add(dup_record)
                        duplicates.append(dup_record)

        db.commit()
        return duplicates

    @staticmethod
    def detect_all_duplicates(db: Session) -> Tuple[List[PaymentDuplicate], List[PaymentDuplicate]]:
        """Run all duplicate detection checks."""
        batch_dups = DuplicateDetector.check_batch_duplicates(db)
        history_dups = DuplicateDetector.check_history_duplicates(db)
        return batch_dups, history_dups


class AuditLogger:
    """Handles creation of audit trail entries."""

    @staticmethod
    def log_payment_change(
        db: Session,
        payment_type: str,
        payment_id: Optional[int],
        action: str,
        old_values: Optional[Dict[str, Any]] = None,
        new_values: Optional[Dict[str, Any]] = None,
        audit_user: str = "system",
        audit_source: str = "api",
        is_duplicate_update: bool = False,
    ) -> PaymentAuditLog:
        """
        Create an audit log entry for a payment change.
        
        Args:
            payment_type: 'batch' or 'history'
            payment_id: ID of the payment record
            action: 'created', 'updated', 'flagged', 'reconciled'
            old_values: Previous values (dict)
            new_values: New values (dict)
            audit_user: Who/what triggered the change
            audit_source: 'api', 'reconciliation', 'manual', 'duplicate_detection'
            is_duplicate_update: Whether this update was due to duplicate detection
        """
        # Generate change summary
        change_summary = AuditLogger._generate_change_summary(
            action, old_values, new_values
        )

        log_entry = PaymentAuditLog(
            payment_type=payment_type,
            payment_id=payment_id,
            action=action,
            old_values=json.dumps(old_values) if old_values else None,
            new_values=json.dumps(new_values) if new_values else None,
            change_summary=change_summary,
            audit_timestamp=datetime.utcnow(),
            audit_user=audit_user,
            audit_source=audit_source,
            is_duplicate_update=1 if is_duplicate_update else 0,
        )
        db.add(log_entry)
        db.commit()
        return log_entry

    @staticmethod
    def _generate_change_summary(
        action: str, old_values: Optional[Dict] = None, new_values: Optional[Dict] = None
    ) -> str:
        """Generate human-readable summary of changes."""
        if action == "created":
            return "Payment entry created"
        elif action == "flagged":
            if new_values and "flag_reason" in new_values:
                return f"Payment flagged: {new_values['flag_reason']}"
            return "Payment flagged"
        elif action == "updated":
            if not old_values or not new_values:
                return "Payment updated"

            changes = []
            for key in new_values:
                if key not in old_values or old_values[key] != new_values[key]:
                    changes.append(f"{key}: {old_values.get(key)} → {new_values[key]}")

            return "Payment updated: " + "; ".join(changes)
        elif action == "reconciled":
            return "Payment reconciled (duplicate handled)"
        else:
            return f"Payment {action}"

    @staticmethod
    def get_audit_log(
        db: Session,
        payment_type: Optional[str] = None,
        action: Optional[str] = None,
        is_duplicate_update: Optional[int] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Tuple[List[PaymentAuditLog], int]:
        """
        Query audit logs with optional filtering.
        
        Returns:
            Tuple of (audit log entries, total count)
        """
        query = db.query(PaymentAuditLog)

        if payment_type:
            query = query.filter(PaymentAuditLog.payment_type == payment_type)
        if action:
            query = query.filter(PaymentAuditLog.action == action)
        if is_duplicate_update is not None:
            query = query.filter(
                PaymentAuditLog.is_duplicate_update == is_duplicate_update
            )

        total = query.count()
        entries = (
            query.order_by(PaymentAuditLog.audit_timestamp.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )

        return entries, total


class PaymentReconciliationService:
    """Manages reconciliation of duplicate payments."""

    @staticmethod
    def reconcile_duplicate(
        db: Session,
        duplicate_id: int,
        action_type: str,
        action_user: str = "system",
        action_notes: Optional[str] = None,
        final_value_used: Optional[Dict[str, Any]] = None,
    ) -> Optional[PaymentReconciliation]:
        """
        Reconcile a duplicate payment entry.
        
        Args:
            duplicate_id: ID of the PaymentDuplicate record
            action_type: 'kept_primary', 'use_higher_value', 'manual_merge', 'ignore'
            action_user: User performing the action
            action_notes: Notes about the action
            final_value_used: Final values selected for the primary record
        
        Returns:
            PaymentReconciliation record or None if duplicate not found
        """
        duplicate = db.query(PaymentDuplicate).filter_by(id=duplicate_id).first()
        if not duplicate:
            return None

        # Execute reconciliation based on action type
        if action_type == "kept_primary":
            # Delete duplicate, keep primary as-is
            PaymentReconciliationService._delete_duplicate_record(
                db, duplicate
            )
        elif action_type == "use_higher_value":
            # Update primary with higher value from duplicate
            PaymentReconciliationService._merge_with_higher_value(
                db, duplicate
            )
        elif action_type == "manual_merge":
            # Apply custom final values to primary
            if final_value_used:
                PaymentReconciliationService._apply_custom_merge(
                    db, duplicate, final_value_used
                )
        elif action_type == "ignore":
            # Don't delete, just mark as reconciled
            pass

        # Log the reconciliation action
        reconciliation = PaymentReconciliation(
            duplicate_id=duplicate_id,
            action_type=action_type,
            action_timestamp=datetime.utcnow(),
            action_user=action_user,
            final_value_used=json.dumps(final_value_used) if final_value_used else None,
            action_notes=action_notes,
        )
        db.add(reconciliation)

        # Update duplicate status
        duplicate.reconciliation_status = "merged" if action_type != "ignore" else "ignored"
        duplicate.reconciled_at = datetime.utcnow()
        duplicate.reconciliation_notes = action_notes

        db.commit()
        return reconciliation

    @staticmethod
    def _delete_duplicate_record(db: Session, duplicate: PaymentDuplicate) -> None:
        """Delete the duplicate payment record."""
        if duplicate.duplicate_payment_type == "batch":
            db.query(PaymentBatch).filter_by(id=duplicate.duplicate_payment_id).delete()
            AuditLogger.log_payment_change(
                db,
                payment_type="batch",
                payment_id=duplicate.duplicate_payment_id,
                action="reconciled",
                audit_source="reconciliation",
                audit_user="reconciliation_service",
            )
        elif duplicate.duplicate_payment_type == "history":
            db.query(PaymentHistory).filter_by(id=duplicate.duplicate_payment_id).delete()
            AuditLogger.log_payment_change(
                db,
                payment_type="history",
                payment_id=duplicate.duplicate_payment_id,
                action="reconciled",
                audit_source="reconciliation",
                audit_user="reconciliation_service",
            )

    @staticmethod
    def _merge_with_higher_value(db: Session, duplicate: PaymentDuplicate) -> None:
        """Update primary record with higher value from duplicate."""
        if duplicate.primary_payment_type == "batch":
            primary = db.query(PaymentBatch).filter_by(id=duplicate.primary_payment_id).first()
            dup = db.query(PaymentBatch).filter_by(id=duplicate.duplicate_payment_id).first()

            if primary and dup:
                old_value = primary.amount_usd
                primary.amount_usd = max(primary.amount_usd, dup.amount_usd)

                AuditLogger.log_payment_change(
                    db,
                    payment_type="batch",
                    payment_id=primary.id,
                    action="updated",
                    old_values={"amount_usd": old_value},
                    new_values={"amount_usd": primary.amount_usd},
                    audit_source="reconciliation",
                    audit_user="reconciliation_service",
                    is_duplicate_update=True,
                )

                # Delete duplicate
                db.delete(dup)

        elif duplicate.primary_payment_type == "history":
            primary = db.query(PaymentHistory).filter_by(id=duplicate.primary_payment_id).first()
            dup = db.query(PaymentHistory).filter_by(id=duplicate.duplicate_payment_id).first()

            if primary and dup:
                old_values = {
                    "amount_usd": primary.amount_usd,
                    "amount_kes": primary.amount_kes,
                }
                primary.amount_usd = max(primary.amount_usd, dup.amount_usd)
                primary.amount_kes = max(primary.amount_kes or 0, dup.amount_kes or 0)

                AuditLogger.log_payment_change(
                    db,
                    payment_type="history",
                    payment_id=primary.id,
                    action="updated",
                    old_values=old_values,
                    new_values={
                        "amount_usd": primary.amount_usd,
                        "amount_kes": primary.amount_kes,
                    },
                    audit_source="reconciliation",
                    audit_user="reconciliation_service",
                    is_duplicate_update=True,
                )

                # Delete duplicate
                db.delete(dup)

    @staticmethod
    def _apply_custom_merge(
        db: Session, duplicate: PaymentDuplicate, final_values: Dict[str, Any]
    ) -> None:
        """Apply custom merged values to primary record."""
        if duplicate.primary_payment_type == "batch":
            primary = db.query(PaymentBatch).filter_by(id=duplicate.primary_payment_id).first()
            if primary:
                old_values = {"amount_usd": primary.amount_usd}
                if "amount_usd" in final_values:
                    primary.amount_usd = final_values["amount_usd"]

                AuditLogger.log_payment_change(
                    db,
                    payment_type="batch",
                    payment_id=primary.id,
                    action="updated",
                    old_values=old_values,
                    new_values=final_values,
                    audit_source="reconciliation",
                    audit_user="reconciliation_service",
                    is_duplicate_update=True,
                )

                # Delete duplicate
                dup = db.query(PaymentBatch).filter_by(id=duplicate.duplicate_payment_id).first()
                if dup:
                    db.delete(dup)

        elif duplicate.primary_payment_type == "history":
            primary = db.query(PaymentHistory).filter_by(id=duplicate.primary_payment_id).first()
            if primary:
                old_values = {
                    "amount_usd": primary.amount_usd,
                    "amount_kes": primary.amount_kes,
                }
                if "amount_usd" in final_values:
                    primary.amount_usd = final_values["amount_usd"]
                if "amount_kes" in final_values:
                    primary.amount_kes = final_values["amount_kes"]

                AuditLogger.log_payment_change(
                    db,
                    payment_type="history",
                    payment_id=primary.id,
                    action="updated",
                    old_values=old_values,
                    new_values=final_values,
                    audit_source="reconciliation",
                    audit_user="reconciliation_service",
                    is_duplicate_update=True,
                )

                # Delete duplicate
                dup = db.query(PaymentHistory).filter_by(id=duplicate.duplicate_payment_id).first()
                if dup:
                    db.delete(dup)

    @staticmethod
    def get_pending_duplicates(db: Session) -> List[PaymentDuplicate]:
        """Get all pending duplicates awaiting reconciliation."""
        return (
            db.query(PaymentDuplicate)
            .filter_by(reconciliation_status="pending")
            .order_by(PaymentDuplicate.detected_at.desc())
            .all()
        )

    @staticmethod
    def get_duplicate_summary(db: Session) -> Dict[str, int]:
        """Get summary statistics of duplicate reconciliation."""
        total = db.query(PaymentDuplicate).count()
        pending = db.query(PaymentDuplicate).filter_by(reconciliation_status="pending").count()
        merged = db.query(PaymentDuplicate).filter_by(reconciliation_status="merged").count()
        ignored = db.query(PaymentDuplicate).filter_by(reconciliation_status="ignored").count()

        return {
            "total_duplicates": total,
            "pending_review": pending,
            "merged": merged,
            "ignored": ignored,
        }
