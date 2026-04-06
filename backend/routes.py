from __future__ import annotations

import json
from datetime import date, datetime
import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from analytics.metrics import compute_core_metrics, compute_payment_metrics
from analytics.productivity import build_heatmap_data, build_performance_insights
from backend.audit_service import AuditLogger, DuplicateDetector, PaymentReconciliationService
from backend.database import get_db
from backend.models import ContributionDay, ExtensionHeartbeat, FrameLog, PaymentAuditLog, PaymentBatch, PaymentDuplicate, PaymentHistory, PaymentSyncDebug, Session as WorkSession
from backend.models import Task
from backend.schemas import (
    ActivityPingRequest,
    AuditLogQuery,
    ContributionSyncRequest,
    DuplicateDetectionResult,
    ExtensionHeartbeatRequest,
    FrameLogRequest,
    GenericResponse,
    PaymentBatchRequest,
    PaymentDuplicateInfo,
    PaymentHistoryRequest,
    PaymentSyncRequest,
    PaymentSyncDebugRequest,
    ReconciliationAction,
    SessionResponse,
    TaskEndRequest,
    TaskStartRequest,
    TaskUpdateRequest,
)
from tracker.frame_tracker import calculate_frame_speed

CAMERA_NORMALIZE_RE = re.compile(r'\s*\(CAM\s*\d+\)\s*$', re.IGNORECASE)

def normalize_camera_name(camera_raw: str | None) -> str | None:
    """Normalize camera names by removing (CAM XX) patterns."""
    if not camera_raw:
        return camera_raw
    return CAMERA_NORMALIZE_RE.sub('', camera_raw.strip())

router = APIRouter()

session_manager = None


def _upsert_extension_heartbeat(payload: ExtensionHeartbeatRequest, db: Session) -> ExtensionHeartbeat:
    record = db.query(ExtensionHeartbeat).filter(ExtensionHeartbeat.client_key == payload.client_key).first()
    if record is None:
        record = ExtensionHeartbeat(client_key=payload.client_key)
        db.add(record)
    record.page_url = payload.page_url
    record.page_type = (payload.page_type or "unknown").strip().lower()
    record.source = (payload.source or "content_script").strip().lower()
    record.last_seen_at = datetime.utcnow()
    return record


def _upsert_payment_sync_debug(payload: PaymentSyncDebugRequest, db: Session) -> PaymentSyncDebug:
    record = db.query(PaymentSyncDebug).filter(PaymentSyncDebug.sync_key == payload.sync_key).first()
    if record is None:
        record = PaymentSyncDebug(sync_key=payload.sync_key)
        db.add(record)

    record.page_url = payload.page_url
    record.page_detected = 1 if payload.page_detected else 0
    record.recent_work_section_found = 1 if payload.recent_work_section_found else 0
    record.payment_history_section_found = 1 if payload.payment_history_section_found else 0
    record.recent_work_rows = max(int(payload.recent_work_rows or 0), 0)
    record.payment_history_rows = max(int(payload.payment_history_rows or 0), 0)
    record.last_status = (payload.last_status or "waiting_for_sync").strip().lower()
    record.last_error = payload.last_error
    record.backend_status_code = payload.backend_status_code
    record.page_fingerprint = payload.page_fingerprint
    record.last_attempt_at = datetime.utcnow()
    if record.last_status in {"synced", "ok"} and (record.recent_work_rows > 0 or record.payment_history_rows > 0):
        record.last_success_at = record.last_attempt_at
    return record

SYNTHETIC_TASK_UID_RE = re.compile(r"^task-[0-9a-f]{5,}$")


@router.get("/health")
def health_check():
    return {"status": "ok", "service": "Avala Tracker Pro"}


def _update_task_fields(task: Task, payload: TaskUpdateRequest | TaskStartRequest, normalized_camera: str | None = None) -> bool:
    changed = False
    if getattr(payload, "dataset", None) and task.dataset != payload.dataset:
        task.dataset = payload.dataset
        changed = True
    if normalized_camera is not None and task.camera_name != normalized_camera:
        task.camera_name = normalized_camera
        changed = True
    elif getattr(payload, "camera", None) and normalized_camera is None and task.camera_name != payload.camera:
        # Fallback for cases where normalized_camera isn't provided
        task.camera_name = payload.camera
        changed = True
    if getattr(payload, "frame_start", None) is not None and payload.frame_start > 0:
        if task.frame_start != payload.frame_start:
            task.frame_start = payload.frame_start
            changed = True
    if getattr(payload, "frame_end", None) is not None and payload.frame_end > 0:
        if task.frame_end != payload.frame_end:
            task.frame_end = payload.frame_end
            changed = True
    if getattr(payload, "total_frames", None) is not None and payload.total_frames > 0:
        if task.total_frames != payload.total_frames:
            task.total_frames = payload.total_frames
            changed = True
    if getattr(payload, "expected_hours", None) is not None and payload.expected_hours > 0:
        if task.expected_hours != payload.expected_hours:
            task.expected_hours = payload.expected_hours
            changed = True
    return changed


@router.post("/task/start", response_model=SessionResponse)
def start_task(payload: TaskStartRequest, db: Session = Depends(get_db)):
    # Normalize camera name
    normalized_camera = normalize_camera_name(payload.camera)
    
    # Skip tracking unknown-dataset or unknown camera tasks
    if payload.dataset == "unknown-dataset" or normalized_camera == "unknown":
        return SessionResponse(session_id=0, task_uid=payload.task_uid, start_time=datetime.utcnow(), end_time=None, active_minutes=0, idle_minutes=0, frames_completed=0, efficiency_score=0)
    
    if session_manager is None:
        raise HTTPException(status_code=500, detail="Session manager unavailable")

    task = db.query(Task).filter(Task.task_uid == payload.task_uid).first()
    if task is None and SYNTHETIC_TASK_UID_RE.match(payload.task_uid or ""):
        task = (
            db.query(Task)
            .filter(
                Task.dataset == payload.dataset,
                Task.camera_name == normalized_camera,
                Task.created_at >= func.datetime("now", "-2 minutes"),
            )
            .order_by(Task.created_at.desc())
            .first()
        )

    if task is None:
        task = Task(
            task_uid=payload.task_uid,
            dataset=payload.dataset,
            camera_name=normalized_camera,
            frame_start=payload.frame_start,
            frame_end=payload.frame_end,
            total_frames=payload.total_frames,
            expected_hours=payload.expected_hours,
        )
        db.add(task)
        db.commit()
        db.refresh(task)
    else:
        if _update_task_fields(task, payload, normalized_camera):
            db.commit()
            db.refresh(task)

    session = session_manager.start_session(db, payload.task_uid)
    return SessionResponse(
        session_id=session.id,
        task_uid=task.task_uid,
        start_time=session.start_time,
        end_time=session.end_time,
        active_minutes=session.active_minutes,
        idle_minutes=session.idle_minutes,
        frames_completed=session.frames_completed,
        efficiency_score=session.efficiency_score,
    )


@router.post("/task/update", response_model=GenericResponse)
def update_task(payload: TaskUpdateRequest, db: Session = Depends(get_db)):
    # Normalize camera name
    normalized_camera = normalize_camera_name(payload.camera)
    
    # Skip tracking unknown-dataset or unknown camera tasks
    if payload.dataset == "unknown-dataset" or normalized_camera == "unknown":
        return GenericResponse(status="ok", detail="Skipped (unknown dataset/camera)")
    
    task = db.query(Task).filter(Task.task_uid == payload.task_uid).first()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if _update_task_fields(task, payload, normalized_camera):
        db.commit()
    return GenericResponse(status="ok", detail="Task updated")


@router.post("/task/end", response_model=GenericResponse)
def end_task(payload: TaskEndRequest, db: Session = Depends(get_db)):
    if session_manager is None:
        raise HTTPException(status_code=500, detail="Session manager unavailable")

    session = session_manager.end_session(db, payload.task_uid)
    if session is None:
        raise HTTPException(status_code=404, detail="No active session found for task")
    return GenericResponse(status="ok", detail="Session closed")


@router.post("/activity/ping", response_model=GenericResponse)
def activity_ping(payload: ActivityPingRequest):
    if session_manager is None:
        raise HTTPException(status_code=500, detail="Session manager unavailable")
    if payload.active:
        session_manager.mark_activity()
    return GenericResponse(status="ok", detail="Activity recorded")


@router.post("/frame/log", response_model=GenericResponse)
def log_frame(payload: FrameLogRequest, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.task_uid == payload.task_uid).first()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    
    # Skip logging frames for unknown-dataset or unknown camera tasks
    if task.dataset == "unknown-dataset" or task.camera_name == "unknown":
        return GenericResponse(status="ok", detail="Skipped (unknown dataset/camera)")

    frame_log = FrameLog(
        task_id=task.id,
        frame_number=payload.frame_number,
        timestamp=datetime.utcnow(),
        annotations_created=payload.annotations_created,
        annotations_deleted=payload.annotations_deleted,
    )
    db.add(frame_log)

    open_session = (
        db.query(WorkSession)
        .filter(WorkSession.task_id == task.id, WorkSession.end_time.is_(None))
        .first()
    )
    if open_session:
        unique_frames = (
            db.query(func.count(func.distinct(FrameLog.frame_number)))
            .filter(FrameLog.task_id == task.id)
            .scalar()
            or 0
        )
        open_session.frames_completed = int(unique_frames)

    db.commit()
    return GenericResponse(status="ok", detail="Frame event logged")


@router.post("/contributions/sync", response_model=GenericResponse)
def sync_contributions(payload: ContributionSyncRequest, db: Session = Depends(get_db)):
    synced = 0
    for item in payload.days:
        contribution_date = item.contribution_date.date()
        record = (
            db.query(ContributionDay)
            .filter(ContributionDay.contribution_date == contribution_date)
            .first()
        )
        if record is None:
            record = ContributionDay(
                contribution_date=contribution_date,
                boxes_count=max(int(item.boxes_count or 0), 0),
                source=item.source or "profile",
                captured_at=datetime.utcnow(),
            )
            db.add(record)
        else:
            record.boxes_count = max(int(item.boxes_count or 0), 0)
            record.source = item.source or record.source
            record.captured_at = datetime.utcnow()
        synced += 1

    db.commit()
    return GenericResponse(status="ok", detail=f"Synced {synced} contribution days")


@router.post("/payments/add-batch", response_model=GenericResponse)
def add_payment_batch(payload: PaymentBatchRequest, db: Session = Depends(get_db)):
    batch_name = payload.batch_name.strip()
    record = db.query(PaymentBatch).filter(PaymentBatch.batch_name == batch_name).first()
    
    if record is None:
        record = PaymentBatch(
            batch_name=batch_name,
            amount_usd=max(float(payload.amount_usd or 0.0), 0.0)
        )
        db.add(record)
        db.flush()  # Get the ID without committing
        
        # Log the creation
        AuditLogger.log_payment_change(
            db,
            payment_type="batch",
            payment_id=record.id,
            action="created",
            new_values={
                "batch_name": record.batch_name,
                "amount_usd": record.amount_usd,
            },
            audit_source="api",
        )
        detail = "Payment batch added"
    else:
        # Log old values before update
        old_amount = record.amount_usd
        new_amount = max(float(payload.amount_usd or 0.0), 0.0)
        
        # Check for value differences (duplicate indicator)
        is_duplicate_update = old_amount != new_amount
        
        record.amount_usd = new_amount
        
        # Flag if values changed (indicates duplicate with different amount)
        if is_duplicate_update:
            record.is_flagged = 1
            record.flag_reason = "Duplicate entry detected with updated amount"
            record.flagged_at = datetime.utcnow()
        
        # Log the update
        AuditLogger.log_payment_change(
            db,
            payment_type="batch",
            payment_id=record.id,
            action="updated",
            old_values={"amount_usd": old_amount},
            new_values={"amount_usd": new_amount},
            audit_source="api",
            is_duplicate_update=is_duplicate_update,
        )
        
        detail = "Payment batch updated" + (" (duplicate flagged)" if is_duplicate_update else "")
    
    db.commit()
    return GenericResponse(status="ok", detail=detail)


@router.post("/payments/add-history", response_model=GenericResponse)
def add_payment_history(payload: PaymentHistoryRequest, db: Session = Depends(get_db)):
    record = (
        db.query(PaymentHistory)
        .filter(PaymentHistory.date == payload.date)
        .first()
    )
    status = (payload.status or "completed").strip().lower()
    
    if record is None:
        record = PaymentHistory(
            date=payload.date,
            amount_usd=max(float(payload.amount_usd or 0.0), 0.0),
            amount_kes=max(float(payload.amount_kes or 0.0), 0.0),
            status=status,
        )
        db.add(record)
        db.flush()  # Get the ID without committing
        
        # Log the creation
        AuditLogger.log_payment_change(
            db,
            payment_type="history",
            payment_id=record.id,
            action="created",
            new_values={
                "date": str(record.date),
                "amount_usd": record.amount_usd,
                "amount_kes": record.amount_kes,
                "status": record.status,
            },
            audit_source="api",
        )
        detail = "Payment history added"
    else:
        # Log old values before update
        old_values = {
            "amount_usd": record.amount_usd,
            "amount_kes": record.amount_kes,
            "status": record.status,
        }
        
        new_usd = max(float(payload.amount_usd or 0.0), 0.0)
        new_kes = max(float(payload.amount_kes or 0.0), 0.0)
        
        # Check for value differences (duplicate indicator)
        is_duplicate_update = (
            record.amount_usd != new_usd
            or record.amount_kes != new_kes
            or record.status != status
        )
        
        record.amount_usd = new_usd
        record.amount_kes = new_kes
        record.status = status
        
        # Flag if values changed (indicates duplicate with different amount)
        if is_duplicate_update:
            record.is_flagged = 1
            record.flag_reason = "Duplicate entry detected with updated values"
            record.flagged_at = datetime.utcnow()
        
        new_values = {
            "amount_usd": new_usd,
            "amount_kes": new_kes,
            "status": status,
        }
        
        # Log the update
        AuditLogger.log_payment_change(
            db,
            payment_type="history",
            payment_id=record.id,
            action="updated",
            old_values=old_values,
            new_values=new_values,
            audit_source="api",
            is_duplicate_update=is_duplicate_update,
        )
        
        detail = "Payment history updated" + (" (duplicate flagged)" if is_duplicate_update else "")
    
    db.commit()
    return GenericResponse(status="ok", detail=detail)




@router.post("/extension/heartbeat", response_model=GenericResponse)
def extension_heartbeat(payload: ExtensionHeartbeatRequest, db: Session = Depends(get_db)):
    _upsert_extension_heartbeat(payload, db)
    db.commit()
    return GenericResponse(status="ok", detail="Extension heartbeat recorded")


@router.post("/payments/debug", response_model=GenericResponse)
def update_payment_sync_debug(payload: PaymentSyncDebugRequest, db: Session = Depends(get_db)):
    _upsert_payment_sync_debug(payload, db)
    db.commit()
    return GenericResponse(status="ok", detail="Payment sync diagnostics updated")

@router.post("/payments/sync", response_model=GenericResponse)
def sync_payments(payload: PaymentSyncRequest, db: Session = Depends(get_db)):
    batch_count = 0
    history_count = 0
    for batch in payload.recent_work:
        add_payment_batch(batch, db)
        batch_count += 1
    for history in payload.payment_history:
        add_payment_history(history, db)
        history_count += 1

    debug_payload = PaymentSyncDebugRequest(
        page_detected=True,
        recent_work_section_found=batch_count > 0,
        payment_history_section_found=history_count > 0,
        recent_work_rows=batch_count,
        payment_history_rows=history_count,
        last_status="synced" if (batch_count or history_count) else "waiting_for_sync",
        backend_status_code=200,
    )
    _upsert_payment_sync_debug(debug_payload, db)
    db.commit()
    return GenericResponse(status="ok", detail=f"Synced {batch_count} batches and {history_count} payments")


@router.get("/payments/summary")
def payments_summary(db: Session = Depends(get_db)):
    return compute_payment_metrics(db)


@router.get("/payments/batches")
def payments_batches(db: Session = Depends(get_db)):
    rows = db.query(PaymentBatch).order_by(PaymentBatch.amount_usd.desc(), PaymentBatch.batch_name.asc()).all()
    return {
        "items": [
            {
                "batch_name": row.batch_name,
                "amount_usd": row.amount_usd,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
    }


@router.get("/analytics/overview")
def analytics_overview(db: Session = Depends(get_db)):
    return compute_core_metrics(db)


@router.get("/analytics/performance")
def analytics_performance(db: Session = Depends(get_db)):
    metrics = compute_core_metrics(db)
    insights = build_performance_insights(db)
    heatmap = build_heatmap_data(db)
    frame_speed = calculate_frame_speed(db)
    return {
        "metrics": metrics,
        "insights": insights,
        "heatmap": heatmap,
        "frame_speed": frame_speed,
    }


@router.get("/analytics/today")
def analytics_today(db: Session = Depends(get_db)):
    today = date.today()
    tasks_today = (
        db.query(Task)
        .filter(func.date(Task.created_at) == str(today))
        .order_by(Task.created_at.desc())
        .all()
    )
    return {
        "date": str(today),
        "tasks": [
            {
                "task_uid": t.task_uid,
                "dataset": t.dataset,
                "camera": t.camera_name,
                "total_frames": t.total_frames,
            }
            for t in tasks_today
        ],
    }


# ============= PAYMENT AUDIT & RECONCILIATION ENDPOINTS =============


@router.post("/payments/detect-duplicates", response_model=DuplicateDetectionResult)
def detect_duplicates(db: Session = Depends(get_db)):
    """
    Detect duplicate payments and return results.
    Scans both PaymentBatch and PaymentHistory for exact matches.
    """
    batch_dups, history_dups = DuplicateDetector.detect_all_duplicates(db)
    
    # Get all duplicates
    all_dups = db.query(PaymentDuplicate).all()
    
    # Calculate summary
    summary = PaymentReconciliationService.get_duplicate_summary(db)
    
    # Convert to response schema
    duplicate_infos = [
        PaymentDuplicateInfo(
            id=dup.id,
            primary_payment_type=dup.primary_payment_type,
            primary_payment_id=dup.primary_payment_id,
            duplicate_payment_type=dup.duplicate_payment_type,
            duplicate_payment_id=dup.duplicate_payment_id,
            match_key=dup.match_key,
            similarity_score=dup.similarity_score,
            has_value_difference=dup.has_value_difference,
            value_difference_summary=dup.value_difference_summary,
            reconciliation_status=dup.reconciliation_status,
            detected_at=dup.detected_at,
            reconciled_at=dup.reconciled_at,
            reconciliation_notes=dup.reconciliation_notes,
        )
        for dup in all_dups
    ]
    
    return DuplicateDetectionResult(
        total_duplicates=summary["total_duplicates"],
        pending_review=summary["pending_review"],
        merged=summary["merged"],
        ignored=summary["ignored"],
        duplicates=duplicate_infos,
    )


@router.get("/payments/duplicates/pending")
def get_pending_duplicates(db: Session = Depends(get_db)):
    """Get all pending duplicates awaiting reconciliation."""
    pending = PaymentReconciliationService.get_pending_duplicates(db)
    
    return {
        "count": len(pending),
        "duplicates": [
            {
                "id": dup.id,
                "primary_payment_type": dup.primary_payment_type,
                "primary_payment_id": dup.primary_payment_id,
                "duplicate_payment_type": dup.duplicate_payment_type,
                "duplicate_payment_id": dup.duplicate_payment_id,
                "match_key": dup.match_key,
                "has_value_difference": dup.has_value_difference,
                "value_difference_summary": dup.value_difference_summary,
                "detected_at": dup.detected_at.isoformat(),
            }
            for dup in pending
        ],
    }


@router.post("/payments/reconcile", response_model=GenericResponse)
def reconcile_payment_duplicate(
    payload: ReconciliationAction,
    db: Session = Depends(get_db),
):
    """
    Reconcile a duplicate payment entry.
    Performs the specified action and logs all changes to the audit trail.
    """
    final_value_used = payload.final_value_used
    if isinstance(final_value_used, str):
        try:
            final_value_used = json.loads(final_value_used)
        except ValueError:
            raise HTTPException(status_code=400, detail="final_value_used must be valid JSON")

    reconciliation = PaymentReconciliationService.reconcile_duplicate(
        db=db,
        duplicate_id=payload.duplicate_id,
        action_type=payload.action_type,
        action_user="api_user",
        action_notes=payload.action_notes,
        final_value_used=final_value_used,
    )
    
    if not reconciliation:
        raise HTTPException(status_code=404, detail="Duplicate payment not found")
    
    return GenericResponse(
        status="ok",
        detail=f"Duplicate payment reconciled with action: {payload.action_type}",
    )


@router.post("/payments/audit-log", response_model=dict)
def query_audit_logs(payload: AuditLogQuery, db: Session = Depends(get_db)):
    """
    Query the payment audit log with optional filtering.
    Returns all changes, updates, and flags applied to payments.
    """
    logs, total = AuditLogger.get_audit_log(
        db=db,
        payment_type=payload.payment_type,
        action=payload.action,
        is_duplicate_update=payload.is_duplicate_update,
        limit=payload.limit,
        offset=payload.offset,
    )
    
    return {
        "total": total,
        "limit": payload.limit,
        "offset": payload.offset,
        "items": [
            {
                "id": log.id,
                "payment_type": log.payment_type,
                "payment_id": log.payment_id,
                "action": log.action,
                "old_values": log.old_values,
                "new_values": log.new_values,
                "change_summary": log.change_summary,
                "audit_timestamp": log.audit_timestamp.isoformat(),
                "audit_user": log.audit_user,
                "audit_source": log.audit_source,
                "is_duplicate_update": log.is_duplicate_update,
            }
            for log in logs
        ],
    }


@router.get("/payments/audit-stats")
def get_audit_statistics(db: Session = Depends(get_db)):
    """Get statistics about payment audit trail and duplicates."""
    from sqlalchemy import func as sql_func
    
    # Audit log stats
    total_audit_entries = db.query(PaymentAuditLog).count()
    duplicate_updates = (
        db.query(PaymentAuditLog)
        .filter(PaymentAuditLog.is_duplicate_update == 1)
        .count()
    )
    
    # Group by action type
    action_counts = (
        db.query(
            PaymentAuditLog.action,
            sql_func.count(PaymentAuditLog.id).label("count"),
        )
        .group_by(PaymentAuditLog.action)
        .all()
    )
    
    # Duplicate stats
    dup_summary = PaymentReconciliationService.get_duplicate_summary(db)
    
    # Flagged payments
    flagged_batches = db.query(PaymentBatch).filter(PaymentBatch.is_flagged == 1).count()
    flagged_history = db.query(PaymentHistory).filter(PaymentHistory.is_flagged == 1).count()
    
    return {
        "audit": {
            "total_entries": total_audit_entries,
            "duplicate_update_entries": duplicate_updates,
            "by_action": {action: count for action, count in action_counts},
        },
        "duplicates": dup_summary,
        "flagged_payments": {
            "batches": flagged_batches,
            "history": flagged_history,
            "total": flagged_batches + flagged_history,
        },
    }


@router.get("/payments/flagged")
def get_flagged_payments(db: Session = Depends(get_db)):
    """Get all flagged payments (those with detected duplicates or issues)."""
    flagged_batches = db.query(PaymentBatch).filter(PaymentBatch.is_flagged == 1).all()
    flagged_histories = (
        db.query(PaymentHistory).filter(PaymentHistory.is_flagged == 1).all()
    )
    
    return {
        "batches": [
            {
                "id": batch.id,
                "batch_name": batch.batch_name,
                "amount_usd": batch.amount_usd,
                "flag_reason": batch.flag_reason,
                "flagged_at": batch.flagged_at.isoformat() if batch.flagged_at else None,
                "created_at": batch.created_at.isoformat() if batch.created_at else None,
            }
            for batch in flagged_batches
        ],
        "history": [
            {
                "id": hist.id,
                "date": str(hist.date),
                "amount_usd": hist.amount_usd,
                "amount_kes": hist.amount_kes,
                "status": hist.status,
                "flag_reason": hist.flag_reason,
                "flagged_at": hist.flagged_at.isoformat() if hist.flagged_at else None,
            }
            for hist in flagged_histories
        ],
        "total_flagged": len(flagged_batches) + len(flagged_histories),
    }

