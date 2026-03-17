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
DASHBOARD_DIR = Path(__file__).resolve().parent
if str(DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_DIR))

from analytics.metrics import compute_core_metrics
from analytics.predictions import productivity_trend_prediction
from analytics.productivity import build_heatmap_data, build_performance_insights, build_period_summaries
from backend.database import SessionLocal
from backend.models import FrameLog, Session as WorkSession
from backend.models import Task
from charts import (
    bar_frames_per_hour,
    bar_period_metric,
    heatmap_tasks,
    line_efficiency,
    line_frame_speed,
    line_period_metric,
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
    period_summaries = build_period_summaries(db)

    st.subheader("Overview")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Tasks Completed Today", metrics["tasks_completed_today"])
    c2.metric("Frames Annotated Today", metrics["frames_annotated_today"])
    c3.metric("Boxes Annotated Today", metrics.get("boxes_annotated_today", 0))
    c4.metric("Hours Worked Today", metrics["hours_worked_today"])
    c5.metric("Efficiency Ratio", metrics["efficiency_ratio"])

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

    st.subheader("Weekly Progress")
    weekly_df = pd.DataFrame(period_summaries.get("weekly", []))
    if not weekly_df.empty:
        st.plotly_chart(
            bar_period_metric(weekly_df, "period", "tasks_completed", "Tasks Completed Per Week"),
            use_container_width=True,
            key="weekly_tasks_chart",
        )
        st.plotly_chart(
            bar_period_metric(weekly_df, "period", "frames_annotated", "Frames Annotated Per Week"),
            use_container_width=True,
            key="weekly_frames_chart",
        )
        st.plotly_chart(
            bar_period_metric(weekly_df, "period", "hours_worked", "Hours Worked Per Week"),
            use_container_width=True,
            key="weekly_hours_chart",
        )
        st.plotly_chart(
            bar_period_metric(weekly_df, "period", "boxes_annotated", "Boxes Annotated Per Week"),
            use_container_width=True,
            key="weekly_boxes_chart",
        )
        st.plotly_chart(
            line_period_metric(weekly_df, "period", "efficiency_ratio", "Efficiency Ratio Per Week"),
            use_container_width=True,
            key="weekly_efficiency_chart",
        )
        st.dataframe(weekly_df, use_container_width=True)
    else:
        st.info("No weekly data yet.")

    st.subheader("Monthly Progress")
    monthly_df = pd.DataFrame(period_summaries.get("monthly", []))
    if not monthly_df.empty:
        st.plotly_chart(
            bar_period_metric(monthly_df, "period", "tasks_completed", "Tasks Completed Per Month"),
            use_container_width=True,
            key="monthly_tasks_chart",
        )
        st.plotly_chart(
            bar_period_metric(monthly_df, "period", "frames_annotated", "Frames Annotated Per Month"),
            use_container_width=True,
            key="monthly_frames_chart",
        )
        st.plotly_chart(
            bar_period_metric(monthly_df, "period", "hours_worked", "Hours Worked Per Month"),
            use_container_width=True,
            key="monthly_hours_chart",
        )
        st.plotly_chart(
            bar_period_metric(monthly_df, "period", "boxes_annotated", "Boxes Annotated Per Month"),
            use_container_width=True,
            key="monthly_boxes_chart",
        )
        st.plotly_chart(
            line_period_metric(monthly_df, "period", "efficiency_ratio", "Efficiency Ratio Per Month"),
            use_container_width=True,
            key="monthly_efficiency_chart",
        )
        st.dataframe(monthly_df, use_container_width=True)
    else:
        st.info("No monthly data yet.")

    st.subheader("Rolling 7-Day Averages")
    if not tasks_per_day.empty:
        tasks_per_day["date"] = pd.to_datetime(tasks_per_day["date"])
        tasks_per_day = tasks_per_day.sort_values("date")
        tasks_per_day["tasks_7d_avg"] = tasks_per_day["tasks_completed"].rolling(7, min_periods=1).mean()
        st.plotly_chart(
            line_period_metric(tasks_per_day, "date", "tasks_7d_avg", "7-Day Avg Tasks Completed"),
            use_container_width=True,
            key="rolling_tasks_chart",
        )
    else:
        st.info("No rolling task data yet.")

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
