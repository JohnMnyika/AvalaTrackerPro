from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

from backend.models import Session as WorkSession
from backend.models import Task

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.json"


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {"rate_per_hour": 7.0, "rate_per_task": 0.0}


def _tasks_df(db: Session) -> pd.DataFrame:
    tasks = db.query(Task).all()
    rows = [
        {
            "id": t.id,
            "task_uid": t.task_uid,
            "dataset": t.dataset,
            "camera_name": t.camera_name,
            "frame_start": t.frame_start,
            "frame_end": t.frame_end,
            "total_frames": t.total_frames,
            "expected_hours": t.expected_hours,
            "created_at": t.created_at,
        }
        for t in tasks
    ]
    return pd.DataFrame(rows)


def _sessions_df(db: Session) -> pd.DataFrame:
    sessions = db.query(WorkSession).all()
    rows = [
        {
            "id": s.id,
            "task_id": s.task_id,
            "start_time": s.start_time,
            "end_time": s.end_time,
            "active_minutes": s.active_minutes,
            "idle_minutes": s.idle_minutes,
            "frames_completed": s.frames_completed,
            "efficiency_score": s.efficiency_score,
        }
        for s in sessions
    ]
    return pd.DataFrame(rows)


def compute_core_metrics(db: Session) -> dict:
    tasks = _tasks_df(db)
    sessions = _sessions_df(db)
    cfg = _load_config()

    if tasks.empty:
        return {
            "frames_per_hour": 0.0,
            "tasks_completed": 0,
            "daily_hours_worked": 0.0,
            "efficiency_ratio": 0.0,
            "dataset_distribution": {},
            "camera_distribution": {},
            "frames_annotated_today": 0,
            "tasks_completed_today": 0,
            "hours_worked_today": 0.0,
            "earnings": {"daily": 0.0, "weekly": 0.0, "monthly_projection": 0.0},
        }

    tasks["created_at"] = pd.to_datetime(tasks["created_at"]) if "created_at" in tasks else pd.Series()
    today = pd.Timestamp(date.today())

    tasks_today = tasks[tasks["created_at"].dt.date == today.date()]

    total_frames = int(tasks["total_frames"].fillna(0).sum())
    total_active_hours = (
        float(sessions["active_minutes"].fillna(0).sum()) / 60.0 if not sessions.empty else 0.0
    )
    frames_per_hour = round(total_frames / max(total_active_hours, 1e-6), 2)

    efficiency_ratio = (
        float(sessions["efficiency_score"].mean()) if not sessions.empty else 0.0
    )

    hours_today = 0.0
    frames_today = int(tasks_today["total_frames"].fillna(0).sum()) if not tasks_today.empty else 0
    if not sessions.empty and not tasks_today.empty:
        today_task_ids = set(tasks_today["id"].tolist())
        hours_today = (
            sessions[sessions["task_id"].isin(today_task_ids)]["active_minutes"].fillna(0).sum()
            / 60.0
        )

    rate_per_hour = float(cfg.get("rate_per_hour", 7.0))
    rate_per_task = float(cfg.get("rate_per_task", 0.0))
    daily_earnings = (hours_today * rate_per_hour) + (len(tasks_today) * rate_per_task)

    return {
        "frames_per_hour": frames_per_hour,
        "tasks_completed": int(len(tasks)),
        "daily_hours_worked": round(hours_today, 2),
        "efficiency_ratio": round(efficiency_ratio, 3),
        "dataset_distribution": tasks["dataset"].fillna("unknown").value_counts().to_dict(),
        "camera_distribution": tasks["camera_name"].fillna("unknown").value_counts().to_dict(),
        "frames_annotated_today": frames_today,
        "tasks_completed_today": int(len(tasks_today)),
        "hours_worked_today": round(hours_today, 2),
        "earnings": {
            "daily": round(daily_earnings, 2),
            "weekly": round(daily_earnings * 5, 2),
            "monthly_projection": round(daily_earnings * 22, 2),
        },
    }
