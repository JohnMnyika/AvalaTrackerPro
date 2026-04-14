from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from analytics.metrics import compute_core_metrics, compute_payment_metrics
from analytics.productivity import build_heatmap_data, build_performance_insights
from backend.audit_service import AuditLogger, DuplicateDetector, PaymentReconciliationService
from backend.batch_matching import extract_batch_anchor, normalize_batch_name
from backend.database import get_db
from backend.models import (
    ContributionDay,
    ExtensionHeartbeat,
    FrameLog,
    PaymentAuditLog,
    PaymentBatch,
    PaymentDuplicate,
    PaymentHistory,
    PaymentSyncDebug,
    Session as WorkSession,
    Task,
    VisionAnalysis,
)
from backend.schemas import (
    ActivityPingRequest,
    AuditLogQuery,
    BoundingBox,
    ContributionSyncRequest,
    DuplicateDetectionResult,
    ExtensionHeartbeatRequest,
    FrameLogRequest,
    GenericResponse,
    PaymentBatchRequest,
    PaymentFullSyncRequest,
    PaymentFullSyncResponse,
    PaymentDuplicateInfo,
    PaymentHistoryRequest,
    PaymentSyncResult,
    PaymentSyncRequest,
    PaymentSyncDebugRequest,
    ReconciliationAction,
    SessionResponse,
    TaskEndRequest,
    TaskStartRequest,
    TaskUpdateRequest,
    VisionAnalyzeRequest,
    VisionAnalyzeResponse,
    VisionSuggestion,
)
from backend.vision_service import VisionAnalyzer
from tracker.frame_tracker import calculate_frame_speed

CAMERA_NORMALIZE_RE = re.compile(r'\s*\(CAM\s*\d+\)\s*$', re.IGNORECASE)

def normalize_camera_name(camera_raw: str | None) -> str | None:
    """Normalize camera names by removing (CAM XX) patterns."""
    if not camera_raw:
        return camera_raw
    return CAMERA_NORMALIZE_RE.sub('', camera_raw.strip())

router = APIRouter()
logger = logging.getLogger(__name__)

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


def _normalize_batch_name(batch_name: str | None) -> str:
    return normalize_batch_name(batch_name)


def _normalize_payment_status(status: str | None) -> str:
    return (status or "completed").strip().lower()


def _coerce_non_negative_amount(value: Any) -> float:
    return max(float(value or 0.0), 0.0)


def _extract_batch_numeric_id(batch_name: str | None) -> int | None:
    match = re.search(r"\bbatch-(\d+)\b", normalize_batch_name(batch_name))
    if not match:
        return None
    return int(match.group(1))


def _is_likely_batch_amount(batch_name: str | None, amount_usd: float) -> bool:
    if not isinstance(amount_usd, (int, float)):
        return False
    if amount_usd < 0 or amount_usd > 100:
        return False
    batch_id = _extract_batch_numeric_id(batch_name)
    if batch_id is not None and float(batch_id) == float(amount_usd):
        return False
    return True


def _build_snapshot_hash(batches: list[dict[str, Any]], history: list[dict[str, Any]]) -> str:
    canonical_payload = {
        "batches": sorted(batches, key=lambda item: item["batch_name"]),
        "history": sorted(history, key=lambda item: item["date"]),
    }
    encoded = json.dumps(canonical_payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _find_matching_tasks_for_payment(db: Session, normalized_batch_name: str) -> list[Task]:
    exact_matches = (
        db.query(Task)
        .filter(Task.normalized_batch_name == normalized_batch_name)
        .all()
    )
    if exact_matches:
        return exact_matches

    anchor = extract_batch_anchor(normalized_batch_name)
    if not anchor:
        return []

    anchor_matches = [
        task for task in db.query(Task).all()
        if extract_batch_anchor(task.normalized_batch_name or task.dataset) == anchor
    ]
    normalized_candidates = {
        normalize_batch_name(task.normalized_batch_name or task.dataset)
        for task in anchor_matches
    }
    if len(normalized_candidates) == 1:
        return anchor_matches
    return []


def _mark_matching_tasks_paid(
    db: Session,
    *,
    payment_batch_name: str,
    normalized_batch_name: str,
    amount_usd: float,
    seen_at: datetime,
) -> int:
    matched_tasks = _find_matching_tasks_for_payment(db, normalized_batch_name)
    updated_count = 0
    for task in matched_tasks:
        source_name = task.dataset or task.normalized_batch_name or ""
        logger.info("Matched %s -> %s", payment_batch_name, source_name)
        task.normalized_batch_name = normalize_batch_name(task.dataset)
        task.payment_status = "paid"
        task.paid_amount_usd = amount_usd
        task.payment_updated_at = seen_at
        updated_count += 1
    return updated_count


def _reconcile_task_payment_status(task: Task, db: Session) -> None:
    task.normalized_batch_name = normalize_batch_name(task.dataset)
    if not task.normalized_batch_name:
        task.payment_status = "unpaid"
        task.paid_amount_usd = 0.0
        return

    exact = (
        db.query(PaymentBatch)
        .filter(PaymentBatch.normalized_batch_name == task.normalized_batch_name)
        .order_by(PaymentBatch.updated_at.desc(), PaymentBatch.created_at.desc())
        .first()
    )
    candidate = exact
    if candidate is None:
        anchor = extract_batch_anchor(task.normalized_batch_name)
        if anchor:
            fuzzy_candidates = [
                payment for payment in db.query(PaymentBatch).all()
                if extract_batch_anchor(payment.normalized_batch_name or payment.batch_name) == anchor
            ]
            normalized_candidates = {
                normalize_batch_name(payment.normalized_batch_name or payment.batch_name)
                for payment in fuzzy_candidates
            }
            if len(normalized_candidates) == 1 and fuzzy_candidates:
                candidate = sorted(
                    fuzzy_candidates,
                    key=lambda payment: (
                        payment.updated_at or payment.created_at or datetime.min,
                        payment.amount_usd,
                    ),
                    reverse=True,
                )[0]
    if candidate is None:
        task.payment_status = "unpaid"
        task.paid_amount_usd = 0.0
        return

    logger.info("Matched %s -> %s", candidate.batch_name, task.dataset)
    task.payment_status = "paid"
    task.paid_amount_usd = candidate.amount_usd
    task.payment_updated_at = candidate.updated_at or candidate.created_at


def _sync_single_batch(
    batch_payload: PaymentBatchRequest,
    db: Session,
    *,
    seen_at: datetime,
    audit_source: str,
) -> str:
    batch_name = _normalize_batch_name(batch_payload.batch_name)
    normalized_batch_name = normalize_batch_name(batch_payload.batch_name)
    amount_usd = _coerce_non_negative_amount(batch_payload.amount_usd)
    if not _is_likely_batch_amount(batch_payload.batch_name, amount_usd):
        raise ValueError(f"Invalid batch amount for {batch_payload.batch_name}: {amount_usd}")

    matching_records = (
        db.query(PaymentBatch)
        .filter(
            (PaymentBatch.normalized_batch_name == normalized_batch_name)
            | (PaymentBatch.batch_name == batch_name)
        )
        .order_by(PaymentBatch.updated_at.desc(), PaymentBatch.created_at.desc(), PaymentBatch.id.asc())
        .all()
    )
    record = matching_records[0] if matching_records else None

    if record is None:
        record = PaymentBatch(
            batch_name=batch_name,
            normalized_batch_name=normalized_batch_name,
            amount_usd=amount_usd,
            created_at=seen_at,
            updated_at=seen_at,
            last_seen_at=seen_at,
            is_updated=0,
        )
        db.add(record)
        db.flush()
        AuditLogger.log_payment_change(
            db,
            payment_type="batch",
            payment_id=record.id,
            action="created",
            new_values={"batch_name": batch_name, "normalized_batch_name": normalized_batch_name, "amount_usd": amount_usd},
            audit_source=audit_source,
            auto_commit=False,
        )
        _mark_matching_tasks_paid(
            db,
            payment_batch_name=batch_payload.batch_name,
            normalized_batch_name=normalized_batch_name,
            amount_usd=amount_usd,
            seen_at=seen_at,
        )
        return "inserted"

    record.normalized_batch_name = normalized_batch_name
    record.batch_name = batch_name
    duplicate_records = matching_records[1:]
    for duplicate in duplicate_records:
        db.delete(duplicate)
    if record.amount_usd != amount_usd:
        old_values = {"amount_usd": record.amount_usd}
        record.amount_usd = amount_usd
        record.updated_at = seen_at
        record.last_seen_at = seen_at
        record.is_updated = 1
        record.is_flagged = 1
        record.flag_reason = "Payment batch value changed during full sync"
        record.flagged_at = seen_at
        AuditLogger.log_payment_change(
            db,
            payment_type="batch",
            payment_id=record.id,
            action="updated",
            old_values=old_values,
            new_values={"amount_usd": amount_usd},
            audit_source=audit_source,
            is_duplicate_update=True,
            auto_commit=False,
        )
        _mark_matching_tasks_paid(
            db,
            payment_batch_name=batch_payload.batch_name,
            normalized_batch_name=normalized_batch_name,
            amount_usd=amount_usd,
            seen_at=seen_at,
        )
        return "updated"

    record.last_seen_at = seen_at
    _mark_matching_tasks_paid(
        db,
        payment_batch_name=batch_payload.batch_name,
        normalized_batch_name=normalized_batch_name,
        amount_usd=amount_usd,
        seen_at=seen_at,
    )
    return "unchanged"


def _sync_single_history(
    history_payload: PaymentHistoryRequest,
    db: Session,
    *,
    seen_at: datetime,
    audit_source: str,
) -> str:
    amount_usd = _coerce_non_negative_amount(history_payload.amount_usd)
    amount_kes = _coerce_non_negative_amount(history_payload.amount_kes)
    status = _normalize_payment_status(history_payload.status)
    record = db.query(PaymentHistory).filter(PaymentHistory.date == history_payload.date).first()

    if record is None:
        record = PaymentHistory(
            date=history_payload.date,
            amount_usd=amount_usd,
            amount_kes=amount_kes,
            status=status,
            created_at=seen_at,
            updated_at=seen_at,
            last_seen_at=seen_at,
            is_updated=0,
        )
        db.add(record)
        db.flush()
        AuditLogger.log_payment_change(
            db,
            payment_type="history",
            payment_id=record.id,
            action="created",
            new_values={
                "date": str(record.date),
                "amount_usd": amount_usd,
                "amount_kes": amount_kes,
                "status": status,
            },
            audit_source=audit_source,
            auto_commit=False,
        )
        return "inserted"

    old_values = {
        "amount_usd": record.amount_usd,
        "amount_kes": record.amount_kes,
        "status": record.status,
    }
    new_values = {
        "amount_usd": amount_usd,
        "amount_kes": amount_kes,
        "status": status,
    }

    if old_values != new_values:
        record.amount_usd = amount_usd
        record.amount_kes = amount_kes
        record.status = status
        record.updated_at = seen_at
        record.last_seen_at = seen_at
        record.is_updated = 1
        record.is_flagged = 1
        record.flag_reason = "Payment history value changed during full sync"
        record.flagged_at = seen_at
        AuditLogger.log_payment_change(
            db,
            payment_type="history",
            payment_id=record.id,
            action="updated",
            old_values=old_values,
            new_values=new_values,
            audit_source=audit_source,
            is_duplicate_update=True,
            auto_commit=False,
        )
        return "updated"

    record.last_seen_at = seen_at
    return "unchanged"


@router.get("/health")
def health_check():
    return {"status": "ok", "service": "Avala Tracker Pro"}


def _update_task_fields(task: Task, payload: TaskUpdateRequest | TaskStartRequest, normalized_camera: str | None = None) -> bool:
    changed = False
    if getattr(payload, "dataset", None) and task.dataset != payload.dataset:
        task.dataset = payload.dataset
        task.normalized_batch_name = normalize_batch_name(payload.dataset)
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
            normalized_batch_name=normalize_batch_name(payload.dataset),
            payment_status="unpaid",
            paid_amount_usd=0.0,
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

    _reconcile_task_payment_status(task, db)
    db.commit()

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
        _reconcile_task_payment_status(task, db)
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
    change_type = _sync_single_batch(payload, db, seen_at=datetime.utcnow(), audit_source="api")
    db.commit()
    if change_type == "inserted":
        detail = "Payment batch added"
    elif change_type == "updated":
        detail = "Payment batch updated (duplicate flagged)"
    else:
        detail = "Payment batch refreshed"
    return GenericResponse(status="ok", detail=detail)


@router.post("/payments/add-history", response_model=GenericResponse)
def add_payment_history(payload: PaymentHistoryRequest, db: Session = Depends(get_db)):
    change_type = _sync_single_history(payload, db, seen_at=datetime.utcnow(), audit_source="api")
    db.commit()
    if change_type == "inserted":
        detail = "Payment history added"
    elif change_type == "updated":
        detail = "Payment history updated (duplicate flagged)"
    else:
        detail = "Payment history refreshed"
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

@router.post("/vision/analyze", response_model=VisionAnalyzeResponse)
def analyze_vision(payload: VisionAnalyzeRequest, db: Session = Depends(get_db)):
    task = None
    if payload.task_uid:
        task = db.query(Task).filter(Task.task_uid == payload.task_uid).first()
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")

    try:
        result = VisionAnalyzer.analyze_frame(
            image_base64=payload.image_base64,
            existing_boxes=[box.dict() for box in payload.existing_boxes] if payload.existing_boxes else [],
            width=payload.width,
            height=payload.height,
            sensitivity=payload.sensitivity,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if payload.task_uid and task is not None:
        analysis_record = VisionAnalysis(
            task_id=task.id,
            task_uid=task.task_uid,
            frame_number=payload.frame_number,
            detected_boxes=json.dumps(result.get("detected_boxes", [])),
            suggestions=json.dumps(result.get("suggestions", [])),
            suggestions_count=result.get("suggestions_count", 0),
            time_saved_estimate_seconds=result.get("time_saved_estimate_seconds", 0.0),
            image_width=payload.width,
            image_height=payload.height,
            processed_at=datetime.utcnow(),
        )
        db.add(analysis_record)
        db.commit()

    return result

@router.post("/payments/sync", response_model=GenericResponse)
def sync_payments(payload: PaymentSyncRequest, db: Session = Depends(get_db)):
    full_payload = PaymentFullSyncRequest(
        batches=payload.recent_work,
        history=payload.payment_history,
    )
    full_result = sync_payments_full(full_payload, db)
    batch_count = full_result.batches.total
    history_count = full_result.history.total

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


@router.post("/payments/sync-full", response_model=PaymentFullSyncResponse)
def sync_payments_full(payload: PaymentFullSyncRequest, db: Session = Depends(get_db)):
    seen_at = datetime.utcnow()
    batch_result = PaymentSyncResult()
    history_result = PaymentSyncResult()
    warnings: list[str] = []
    errors: list[str] = []

    try:
        logger.info(
            "Starting payment full sync: batches=%s history=%s",
            len(payload.batches),
            len(payload.history),
        )
        normalized_batches = []
        for item in payload.batches:
            batch_name = _normalize_batch_name(item.batch_name)
            amount_usd = _coerce_non_negative_amount(item.amount_usd)
            if not batch_name:
                warnings.append("Skipped batch entry with missing batch_name")
                continue
            if not isinstance(amount_usd, float):
                warnings.append(f"Skipped batch {batch_name}: invalid amount_usd")
                continue
            normalized_batches.append({
                "batch_name": batch_name,
                "amount_usd": amount_usd,
            })

        normalized_history = []
        for item in payload.history:
            amount_usd = _coerce_non_negative_amount(item.amount_usd)
            amount_kes = _coerce_non_negative_amount(item.amount_kes)
            normalized_history.append({
                "date": str(item.date),
                "amount_usd": amount_usd,
                "amount_kes": amount_kes,
                "status": _normalize_payment_status(item.status),
            })

        batch_map = {
            item["batch_name"]: PaymentBatchRequest(batch_name=item["batch_name"], amount_usd=item["amount_usd"])
            for item in normalized_batches
        }
        history_map = {
            item["date"]: PaymentHistoryRequest(
                date=date.fromisoformat(item["date"]),
                amount_usd=item["amount_usd"],
                amount_kes=item["amount_kes"],
                status=item["status"],
            )
            for item in normalized_history
        }

        for batch in batch_map.values():
            try:
                change_type = _sync_single_batch(batch, db, seen_at=seen_at, audit_source="sync_full")
                setattr(batch_result, change_type, getattr(batch_result, change_type) + 1)
            except Exception as exc:
                warnings.append(f"Skipped batch {batch.batch_name}: {exc}")

        for history in history_map.values():
            try:
                change_type = _sync_single_history(history, db, seen_at=seen_at, audit_source="sync_full")
                setattr(history_result, change_type, getattr(history_result, change_type) + 1)
            except Exception as exc:
                warnings.append(f"Skipped history {history.date.isoformat()}: {exc}")

        batch_result.total = len(batch_map)
        history_result.total = len(history_map)

        snapshot_hash = _build_snapshot_hash(normalized_batches, normalized_history)
        debug_payload = PaymentSyncDebugRequest(
            page_detected=True,
            recent_work_section_found=batch_result.total > 0,
            payment_history_section_found=history_result.total > 0,
            recent_work_rows=batch_result.total,
            payment_history_rows=history_result.total,
            last_status="synced" if (batch_result.total or history_result.total) else "waiting_for_sync",
            backend_status_code=200,
            page_fingerprint=snapshot_hash,
            last_error="; ".join(warnings[:5]) if warnings else None,
        )
        _upsert_payment_sync_debug(debug_payload, db)
        db.commit()
        logger.info(
            "Payment full sync complete: inserted=%s updated=%s unchanged=%s warnings=%s",
            batch_result.inserted + history_result.inserted,
            batch_result.updated + history_result.updated,
            batch_result.unchanged + history_result.unchanged,
            len(warnings),
        )

        return PaymentFullSyncResponse(
            status="success",
            detail=(
                f"Full sync complete: {batch_result.inserted + history_result.inserted} inserted, "
                f"{batch_result.updated + history_result.updated} updated, "
                f"{batch_result.unchanged + history_result.unchanged} unchanged"
            ),
            message="Payment sync completed successfully",
            batches=batch_result,
            history=history_result,
            snapshot_hash=snapshot_hash,
            warnings=warnings,
        )
    except Exception as exc:
        db.rollback()
        errors.append(str(exc))
        logger.exception("Payment full sync failed")
        debug_payload = PaymentSyncDebugRequest(
            page_detected=True,
            recent_work_section_found=bool(payload.batches),
            payment_history_section_found=bool(payload.history),
            recent_work_rows=len(payload.batches),
            payment_history_rows=len(payload.history),
            last_status="backend_error",
            backend_status_code=500,
            last_error=str(exc),
        )
        _upsert_payment_sync_debug(debug_payload, db)
        db.commit()
        return PaymentFullSyncResponse(
            status="error",
            detail="Payment sync failed",
            message=str(exc),
            batches=batch_result,
            history=history_result,
            errors=errors,
            warnings=warnings,
        )


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
