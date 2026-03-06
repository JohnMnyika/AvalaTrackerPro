from __future__ import annotations

from datetime import date, datetime
import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from analytics.metrics import compute_core_metrics
from analytics.productivity import build_heatmap_data, build_performance_insights
from backend.database import get_db
from backend.models import FrameLog, Session as WorkSession
from backend.models import Task
from backend.schemas import (
    ActivityPingRequest,
    FrameLogRequest,
    GenericResponse,
    SessionResponse,
    TaskEndRequest,
    TaskStartRequest,
)
from tracker.frame_tracker import calculate_frame_speed

router = APIRouter()
SYNTHETIC_TASK_UID_RE = re.compile(r"^task-[0-9a-f]{5,}$")

# Set from main.py at startup.
session_manager = None


@router.get("/health")
def health_check():
    return {"status": "ok", "service": "Avala Tracker Pro"}


@router.post("/task/start", response_model=SessionResponse)
def start_task(payload: TaskStartRequest, db: Session = Depends(get_db)):
    if session_manager is None:
        raise HTTPException(status_code=500, detail="Session manager unavailable")

    task = db.query(Task).filter(Task.task_uid == payload.task_uid).first()
    if task is None and SYNTHETIC_TASK_UID_RE.match(payload.task_uid or ""):
        # Guardrail: when extension fallback IDs are synthetic, reuse a very recent
        # task with the same dataset/camera to prevent URL-churn overcounting.
        task = (
            db.query(Task)
            .filter(
                Task.dataset == payload.dataset,
                Task.camera_name == payload.camera,
                Task.created_at >= func.datetime("now", "-2 minutes"),
            )
            .order_by(Task.created_at.desc())
            .first()
        )
    if task is None:
        task = Task(
            task_uid=payload.task_uid,
            dataset=payload.dataset,
            camera_name=payload.camera,
            frame_start=payload.frame_start,
            frame_end=payload.frame_end,
            total_frames=payload.total_frames,
            expected_hours=payload.expected_hours,
        )
        db.add(task)
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
