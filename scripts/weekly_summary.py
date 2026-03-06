from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func

from backend.database import SessionLocal
from backend.models import Task


def generate_weekly_summary() -> str:
    with SessionLocal() as db:
        last7 = func.date("now", "-6 day")
        tasks = (
            db.query(Task)
            .filter(func.date(Task.created_at) >= last7)
            .order_by(Task.created_at.asc())
            .all()
        )

    total_tasks = len(tasks)
    total_frames = sum(t.total_frames or 0 for t in tasks)
    datasets = sorted({t.dataset for t in tasks if t.dataset})

    return (
        "Weekly Summary\n\n"
        f"Tasks Completed: {total_tasks}\n"
        f"Frames Annotated: {total_frames}\n"
        f"Datasets Worked: {', '.join(datasets) if datasets else 'None'}\n"
    )


if __name__ == "__main__":
    print(generate_weekly_summary())
