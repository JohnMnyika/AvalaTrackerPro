from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go


def bar_frames_per_hour(df: pd.DataFrame):
    if df.empty:
        return go.Figure()
    return px.bar(
        df,
        x="task_uid",
        y="frames_per_hour",
        title="Frames Per Hour by Task",
        labels={"task_uid": "Task", "frames_per_hour": "Frames/Hour"},
    )


def line_tasks_per_day(df: pd.DataFrame):
    if df.empty:
        return go.Figure()
    return px.line(df, x="date", y="tasks_completed", markers=True, title="Tasks Completed Per Day")


def pie_distribution(data: dict, title: str):
    if not data:
        return go.Figure()
    return px.pie(names=list(data.keys()), values=list(data.values()), title=title)


def line_efficiency(df: pd.DataFrame):
    if df.empty:
        return go.Figure()
    return px.line(df, x="task_uid", y=["expected_hours", "actual_hours"], markers=True, title="Estimated Time vs Actual Time")


def line_frame_speed(df: pd.DataFrame):
    if df.empty:
        return go.Figure()
    return px.line(
        df,
        x="task_uid",
        y="frames_per_minute",
        markers=True,
        title="Frame Annotation Speed",
        labels={"frames_per_minute": "Frames / Minute", "task_uid": "Task"},
    )


def heatmap_tasks(df: pd.DataFrame):
    if df.empty:
        return go.Figure()
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df["dow"] = df["date"].dt.day_name().str.slice(0, 3)
    df["week"] = df["date"].dt.isocalendar().week.astype(int)
    pivot = df.pivot_table(index="dow", columns="week", values="tasks_completed", fill_value=0, aggfunc="sum")
    pivot = pivot.reindex(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]).fillna(0)
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


def bar_period_metric(df: pd.DataFrame, x_col: str, y_col: str, title: str):
    if df.empty:
        return go.Figure()
    return px.bar(df, x=x_col, y=y_col, title=title)


def line_period_metric(df: pd.DataFrame, x_col: str, y_col: str, title: str):
    if df.empty:
        return go.Figure()
    return px.line(df, x=x_col, y=y_col, markers=True, title=title)


def line_earnings_over_time(df: pd.DataFrame):
    if df.empty:
        return go.Figure()
    return px.line(df, x="date", y="amount_usd", markers=True, title="Earnings Over Time", labels={"amount_usd": "USD Earned", "date": "Date"})


def bar_batch_profitability(df: pd.DataFrame):
    if df.empty:
        return go.Figure()
    return px.bar(df, x="batch_name", y="amount_usd", title="Batch Profitability", labels={"batch_name": "Batch", "amount_usd": "USD"})


def usd_kes_comparison(df: pd.DataFrame):
    if df.empty:
        return go.Figure()
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df["date"], y=df["amount_usd"], mode="lines+markers", name="USD"))
    fig.add_trace(go.Bar(x=df["date"], y=df["amount_kes"], name="KES", yaxis="y2", opacity=0.45))
    fig.update_layout(
        title="USD vs KES Comparison",
        yaxis=dict(title="USD"),
        yaxis2=dict(title="KES", overlaying="y", side="right"),
        legend=dict(orientation="h"),
    )
    return fig


def bar_camera_progress(df: pd.DataFrame):
    if df.empty:
        return go.Figure()
    return px.bar(
        df,
        x="Camera",
        y="Frames",
        hover_data={"Boxes": True, "Hours": True},
        title="Camera Progress",
        labels={"Camera": "Camera", "Frames": "Frames Logged"},
    )
