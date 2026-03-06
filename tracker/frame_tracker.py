from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from sqlalchemy.orm import Session

from backend.models import FrameLog, Task


def calculate_frame_speed(db: Session) -> list[dict]:
    logs = (
        db.query(FrameLog, Task)
        .join(Task, Task.id == FrameLog.task_id)
        .order_by(FrameLog.timestamp.asc())
        .all()
    )

    per_task_times: dict[str, list[datetime]] = defaultdict(list)
    per_task_frames: dict[str, set[int]] = defaultdict(set)
    for log, task in logs:
        per_task_times[task.task_uid].append(log.timestamp)
        per_task_frames[task.task_uid].add(int(log.frame_number))

    speed_points: list[dict] = []
    for task_uid, timestamps in per_task_times.items():
        if len(timestamps) < 2:
            continue
        minutes = max((timestamps[-1] - timestamps[0]).total_seconds() / 60.0, 1e-6)
        frames = len(per_task_frames[task_uid])
        speed_points.append(
            {
                "task_uid": task_uid,
                "frames_per_minute": round(frames / minutes, 3),
                "frames_per_hour": round((frames / minutes) * 60.0, 2),
            }
        )

    return speed_points
