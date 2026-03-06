from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime

from sqlalchemy.orm import Session

from backend.models import Session as WorkSession
from backend.models import Task


def build_heatmap_data(db: Session) -> list[dict]:
    tasks = db.query(Task).all()
    counter = Counter(t.created_at.date().isoformat() for t in tasks)
    return [{"date": d, "tasks_completed": c} for d, c in sorted(counter.items())]


def best_working_hours(db: Session) -> list[dict]:
    sessions = db.query(WorkSession).filter(WorkSession.end_time.is_not(None)).all()
    hour_stats: dict[int, list[float]] = defaultdict(list)
    for s in sessions:
        if not s.start_time:
            continue
        hour_stats[s.start_time.hour].append(float(s.frames_completed))

    results = []
    for hour, frames in sorted(hour_stats.items()):
        avg_frames = sum(frames) / max(len(frames), 1)
        results.append({"hour": hour, "avg_frames": round(avg_frames, 2)})
    return sorted(results, key=lambda x: x["avg_frames"], reverse=True)


def build_performance_insights(db: Session) -> dict:
    tasks = db.query(Task).all()
    sessions = db.query(WorkSession).all()

    if not tasks:
        return {
            "best_working_hours": [],
            "slowest_tasks": [],
            "most_difficult_cameras": [],
        }

    task_map = {t.id: t for t in tasks}

    slowest = []
    for s in sessions:
        task = task_map.get(s.task_id)
        if not task:
            continue
        actual_hours = (s.active_minutes + s.idle_minutes) / 60.0
        if actual_hours <= 0:
            continue
        slowest.append(
            {
                "task_uid": task.task_uid,
                "dataset": task.dataset,
                "camera": task.camera_name,
                "actual_hours": round(actual_hours, 2),
                "frames_completed": s.frames_completed,
            }
        )
    slowest = sorted(slowest, key=lambda x: x["actual_hours"], reverse=True)[:5]

    camera_difficulty = defaultdict(lambda: {"hours": 0.0, "frames": 0})
    for s in sessions:
        task = task_map.get(s.task_id)
        if not task or not task.camera_name:
            continue
        camera_difficulty[task.camera_name]["hours"] += (s.active_minutes + s.idle_minutes) / 60.0
        camera_difficulty[task.camera_name]["frames"] += s.frames_completed

    difficult = []
    for cam, vals in camera_difficulty.items():
        frame_rate = vals["frames"] / max(vals["hours"], 1e-6)
        difficult.append(
            {
                "camera": cam,
                "hours": round(vals["hours"], 2),
                "frames": int(vals["frames"]),
                "frames_per_hour": round(frame_rate, 2),
            }
        )
    difficult = sorted(difficult, key=lambda x: x["frames_per_hour"])[:5]

    return {
        "best_working_hours": best_working_hours(db)[:5],
        "slowest_tasks": slowest,
        "most_difficult_cameras": difficult,
        "generated_at": datetime.utcnow().isoformat(),
    }
