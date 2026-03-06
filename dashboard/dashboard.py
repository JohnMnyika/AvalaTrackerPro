from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st
from sqlalchemy import func

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from analytics.metrics import compute_core_metrics
from analytics.predictions import productivity_trend_prediction
from analytics.productivity import build_heatmap_data, build_performance_insights
from backend.database import SessionLocal
from backend.models import FrameLog, Session as WorkSession
from backend.models import Task
from dashboard.charts import (
    bar_frames_per_hour,
    heatmap_tasks,
    line_efficiency,
    line_frame_speed,
    line_tasks_per_day,
    pie_distribution,
)
from tracker.frame_tracker import calculate_frame_speed

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.json"

st.set_page_config(page_title="Avala Tracker Pro", layout="wide")
st.title("Avala Tracker Pro")

with SessionLocal() as db:
    metrics = compute_core_metrics(db)
    insights = build_performance_insights(db)
    heatmap_data = build_heatmap_data(db)
    frame_speed = calculate_frame_speed(db)
    prediction = productivity_trend_prediction(db)

    st.subheader("Overview")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Tasks Completed Today", metrics["tasks_completed_today"])
    c2.metric("Frames Annotated Today", metrics["frames_annotated_today"])
    c3.metric("Hours Worked Today", metrics["hours_worked_today"])
    c4.metric("Efficiency Ratio", metrics["efficiency_ratio"])

    st.subheader("Productivity Graphs")

    speed_df = pd.DataFrame(frame_speed)
    st.plotly_chart(
        bar_frames_per_hour(speed_df),
        use_container_width=True,
        key="frames_per_hour_chart",
    )

    tasks_per_day = pd.DataFrame(heatmap_data)
    st.plotly_chart(
        line_tasks_per_day(tasks_per_day),
        use_container_width=True,
        key="tasks_per_day_chart",
    )

    task_rows = db.query(Task).all()
    session_rows = db.query(WorkSession).all()
    task_map = {t.id: t for t in task_rows}

    efficiency_rows = []
    for s in session_rows:
        task = task_map.get(s.task_id)
        if not task:
            continue
        actual_hours = (s.active_minutes + s.idle_minutes) / 60.0
        efficiency_rows.append(
            {
                "task_uid": task.task_uid,
                "expected_hours": task.expected_hours or 0,
                "actual_hours": round(actual_hours, 3),
            }
        )
    efficiency_df = pd.DataFrame(efficiency_rows)

    st.subheader("Efficiency Trends")
    st.plotly_chart(
        line_efficiency(efficiency_df),
        use_container_width=True,
        key="efficiency_trends_chart",
    )

    st.subheader("Camera Workload")
    st.plotly_chart(
        pie_distribution(metrics["camera_distribution"], "Camera Distribution"),
        use_container_width=True,
        key="camera_distribution_chart",
    )

    st.subheader("Frame Speed")
    st.plotly_chart(
        line_frame_speed(speed_df),
        use_container_width=True,
        key="frame_speed_chart",
    )

    st.subheader("Time Spent Per Dataset")
    if session_rows:
        dataset_minutes = {}
        for s in session_rows:
            task = task_map.get(s.task_id)
            if not task:
                continue
            dataset_minutes[task.dataset] = dataset_minutes.get(task.dataset, 0.0) + s.active_minutes
        ds_df = pd.DataFrame(
            [{"dataset": k, "active_minutes": v} for k, v in dataset_minutes.items()]
        )
        st.plotly_chart(
            pie_distribution(
                {k: round(v / 60.0, 2) for k, v in dataset_minutes.items()},
                "Hours Per Dataset",
            ),
            use_container_width=True,
            key="dataset_hours_chart",
        )
    else:
        st.info("No session data yet.")

    st.subheader("Productivity Heatmap")
    st.plotly_chart(
        heatmap_tasks(tasks_per_day),
        use_container_width=True,
        key="productivity_heatmap_chart",
    )

    st.subheader("Earnings")
    e1, e2, e3 = st.columns(3)
    e1.metric("Daily Earnings", f"${metrics['earnings']['daily']}")
    e2.metric("Weekly Earnings", f"${metrics['earnings']['weekly']}")
    e3.metric("Monthly Projection", f"${metrics['earnings']['monthly_projection']}")

    st.subheader("Performance Insights")
    st.write("Best Working Hours", insights["best_working_hours"])
    st.write("Slowest Tasks", insights["slowest_tasks"])
    st.write("Most Difficult Cameras", insights["most_difficult_cameras"])

    st.subheader("AI Prediction (Optional)")
    st.json(prediction)

st.caption("Runs fully local. Collects analytics only. Does not automate annotation.")
