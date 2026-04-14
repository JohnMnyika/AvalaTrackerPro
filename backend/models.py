from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Column, Date, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from .database import Base


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    task_uid = Column(String, unique=True, index=True, nullable=False)
    dataset = Column(String, nullable=False)
    normalized_batch_name = Column(String, index=True, nullable=True)
    payment_status = Column(String, nullable=False, default="unpaid")
    paid_amount_usd = Column(Float, nullable=False, default=0.0)
    payment_updated_at = Column(DateTime, nullable=True)
    camera_name = Column(String, nullable=True)
    frame_start = Column(Integer, nullable=False, default=0)
    frame_end = Column(Integer, nullable=False, default=0)
    total_frames = Column(Integer, nullable=False, default=0)
    expected_hours = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    sessions = relationship("Session", back_populates="task", cascade="all, delete-orphan")
    frame_logs = relationship("FrameLog", back_populates="task", cascade="all, delete-orphan")


class Session(Base):
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=False, index=True)
    start_time = Column(DateTime, default=datetime.utcnow, nullable=False)
    end_time = Column(DateTime, nullable=True)
    active_minutes = Column(Float, default=0.0, nullable=False)
    idle_minutes = Column(Float, default=0.0, nullable=False)
    frames_completed = Column(Integer, default=0, nullable=False)
    efficiency_score = Column(Float, default=0.0, nullable=False)
    last_update_time = Column(DateTime, default=datetime.utcnow, nullable=False)

    task = relationship("Task", back_populates="sessions")


class FrameLog(Base):
    __tablename__ = "frame_logs"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=False, index=True)
    frame_number = Column(Integer, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    annotations_created = Column(Integer, default=0, nullable=False)
    annotations_deleted = Column(Integer, default=0, nullable=False)

    task = relationship("Task", back_populates="frame_logs")


class VisionAnalysis(Base):
    __tablename__ = "vision_analysis"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=True, index=True)
    task_uid = Column(String, nullable=True, index=True)
    frame_number = Column(Integer, nullable=True)
    detected_boxes = Column(String, nullable=True)
    suggestions = Column(String, nullable=True)
    suggestions_count = Column(Integer, default=0, nullable=False)
    time_saved_estimate_seconds = Column(Float, default=0.0, nullable=False)
    image_width = Column(Integer, default=0, nullable=False)
    image_height = Column(Integer, default=0, nullable=False)
    processed_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    task = relationship("Task")


class ContributionDay(Base):
    __tablename__ = "contribution_days"

    id = Column(Integer, primary_key=True, index=True)
    contribution_date = Column(Date, unique=True, index=True, nullable=False)
    boxes_count = Column(Integer, default=0, nullable=False)
    source = Column(String, default="profile", nullable=False)
    captured_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class PaymentBatch(Base):
    __tablename__ = "payments_batches"

    id = Column(Integer, primary_key=True, index=True)
    batch_name = Column(String, index=True, nullable=False)
    normalized_batch_name = Column(String, index=True, nullable=True)
    amount_usd = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    last_seen_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    is_updated = Column(Integer, default=0, nullable=False)
    is_flagged = Column(Integer, default=0, nullable=False)
    flag_reason = Column(String, nullable=True)
    flagged_at = Column(DateTime, nullable=True)


class PaymentHistory(Base):
    __tablename__ = "payments_history"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(Date, index=True, nullable=False)
    amount_usd = Column(Float, nullable=False, default=0.0)
    amount_kes = Column(Float, nullable=True, default=0.0)
    status = Column(String, nullable=False, default="completed")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    last_seen_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    is_updated = Column(Integer, default=0, nullable=False)
    is_flagged = Column(Integer, default=0, nullable=False)
    flag_reason = Column(String, nullable=True)
    flagged_at = Column(DateTime, nullable=True)


class PaymentSyncDebug(Base):
    __tablename__ = "payments_sync_debug"

    id = Column(Integer, primary_key=True, index=True)
    sync_key = Column(String, unique=True, index=True, nullable=False, default="payments_dashboard")
    page_url = Column(String, nullable=True)
    page_detected = Column(Integer, nullable=False, default=0)
    recent_work_section_found = Column(Integer, nullable=False, default=0)
    payment_history_section_found = Column(Integer, nullable=False, default=0)
    recent_work_rows = Column(Integer, nullable=False, default=0)
    payment_history_rows = Column(Integer, nullable=False, default=0)
    last_status = Column(String, nullable=False, default="waiting_for_sync")
    last_error = Column(String, nullable=True)
    backend_status_code = Column(Integer, nullable=True)
    page_fingerprint = Column(String, nullable=True)
    last_attempt_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_success_at = Column(DateTime, nullable=True)


class PaymentAuditLog(Base):
    """Immutable audit trail for all payment changes and updates."""
    __tablename__ = "payments_audit_log"

    id = Column(Integer, primary_key=True, index=True)
    payment_type = Column(String, nullable=False)  # 'batch' or 'history'
    payment_id = Column(Integer, nullable=True)  # FK to PaymentBatch.id or PaymentHistory.id
    action = Column(String, nullable=False)  # 'created', 'updated', 'flagged', 'reconciled'
    old_values = Column(String, nullable=True)  # JSON-stringified old values
    new_values = Column(String, nullable=True)  # JSON-stringified new values
    change_summary = Column(String, nullable=True)  # Human-readable summary
    audit_timestamp = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    audit_user = Column(String, default="system", nullable=False)  # Who/what triggered the change
    audit_source = Column(String, nullable=True)  # 'api', 'reconciliation', 'manual', etc.
    is_duplicate_update = Column(Integer, default=0, nullable=False)  # 1 if this was a duplicate update


class PaymentDuplicate(Base):
    """Tracks detected duplicate payments for reconciliation."""
    __tablename__ = "payments_duplicates"

    id = Column(Integer, primary_key=True, index=True)
    primary_payment_type = Column(String, nullable=False)  # 'batch' or 'history'
    primary_payment_id = Column(Integer, nullable=False)  # ID of the primary record
    duplicate_payment_type = Column(String, nullable=False)
    duplicate_payment_id = Column(Integer, nullable=False)  # ID of the duplicate record
    match_key = Column(String, nullable=False, index=True)  # Unique key for matching (e.g., batch_name or date)
    similarity_score = Column(Float, default=1.0, nullable=False)  # 0.0-1.0 confidence
    has_value_difference = Column(Integer, default=0, nullable=False)  # 1 if values differ
    value_difference_summary = Column(String, nullable=True)  # JSON-stringified differences
    reconciliation_status = Column(String, default="pending", nullable=False)  # 'pending', 'merged', 'ignored', 'needs_review'
    detected_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    reconciled_at = Column(DateTime, nullable=True)
    reconciliation_notes = Column(String, nullable=True)


class PaymentReconciliation(Base):
    """Tracks reconciliation actions taken on duplicates."""
    __tablename__ = "payments_reconciliations"

    id = Column(Integer, primary_key=True, index=True)
    duplicate_id = Column(Integer, ForeignKey("payments_duplicates.id"), nullable=False, index=True)
    action_type = Column(String, nullable=False)  # 'kept_primary', 'use_higher_value', 'manual_merge', 'ignore'
    action_timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    action_user = Column(String, default="system", nullable=False)
    final_value_used = Column(String, nullable=True)  # JSON of final values selected
    action_notes = Column(String, nullable=True)

class ExtensionHeartbeat(Base):
    __tablename__ = "extension_heartbeats"

    id = Column(Integer, primary_key=True, index=True)
    client_key = Column(String, unique=True, index=True, nullable=False, default="primary")
    page_url = Column(String, nullable=True)
    page_type = Column(String, nullable=False, default="unknown")
    source = Column(String, nullable=False, default="content_script")
    last_seen_at = Column(DateTime, default=datetime.utcnow, nullable=False)
