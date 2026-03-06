from __future__ import annotations

from datetime import date

from analytics.metrics import compute_core_metrics
from backend.database import SessionLocal


def generate_daily_report() -> str:
    with SessionLocal() as db:
        metrics = compute_core_metrics(db)

    today = date.today().isoformat()
    report = (
        f"Date: {today}\n\n"
        f"Tasks Completed: {metrics['tasks_completed_today']}\n"
        f"Frames Annotated: {metrics['frames_annotated_today']}\n"
        f"Hours Worked: {metrics['hours_worked_today']}\n"
        f"Efficiency: {metrics['efficiency_ratio']}\n"
        f"Estimated Earnings: ${metrics['earnings']['daily']}\n"
    )
    return report


if __name__ == "__main__":
    print(generate_daily_report())
