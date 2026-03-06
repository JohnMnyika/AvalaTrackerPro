from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go


def bar_frames_per_hour(df: pd.DataFrame):
    if df.empty:
        return go.Figure()
    fig = px.bar(
        df,
        x="task_uid",
        y="frames_per_hour",
        title="Frames Per Hour by Task",
        labels={"task_uid": "Task", "frames_per_hour": "Frames/Hour"},
    )
    return fig


def line_tasks_per_day(df: pd.DataFrame):
    if df.empty:
        return go.Figure()
    fig = px.line(
        df,
        x="date",
        y="tasks_completed",
        markers=True,
        title="Tasks Completed Per Day",
    )
    return fig


def pie_distribution(data: dict, title: str):
    if not data:
        return go.Figure()
    labels = list(data.keys())
    values = list(data.values())
    fig = px.pie(names=labels, values=values, title=title)
    return fig


def line_efficiency(df: pd.DataFrame):
    if df.empty:
        return go.Figure()
    fig = px.line(
        df,
        x="task_uid",
        y=["expected_hours", "actual_hours"],
        markers=True,
        title="Estimated Time vs Actual Time",
    )
    return fig


def line_frame_speed(df: pd.DataFrame):
    if df.empty:
        return go.Figure()
    fig = px.line(
        df,
        x="task_uid",
        y="frames_per_minute",
        markers=True,
        title="Frame Annotation Speed",
        labels={"frames_per_minute": "Frames / Minute", "task_uid": "Task"},
    )
    return fig


def heatmap_tasks(df: pd.DataFrame):
    if df.empty:
        return go.Figure()

    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df["dow"] = df["date"].dt.day_name().str.slice(0, 3)
    df["week"] = df["date"].dt.isocalendar().week.astype(int)

    pivot = df.pivot_table(
        index="dow", columns="week", values="tasks_completed", fill_value=0, aggfunc="sum"
    )
    desired_order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    pivot = pivot.reindex(desired_order).fillna(0)

    fig = go.Figure(
        data=go.Heatmap(
            z=pivot.values,
            x=[str(c) for c in pivot.columns],
            y=pivot.index,
            colorscale="Greens",
            colorbar=dict(title="Tasks"),
        )
    )
    fig.update_layout(title="Productivity Heatmap (GitHub Style)", xaxis_title="ISO Week", yaxis_title="Day")
    return fig
