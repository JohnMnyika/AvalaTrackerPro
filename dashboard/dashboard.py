from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from textwrap import dedent

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
DASHBOARD_DIR = Path(__file__).resolve().parent
if str(DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_DIR))

from analytics.metrics import compute_core_metrics
from analytics.predictions import productivity_trend_prediction
from analytics.productivity import build_heatmap_data, build_performance_insights, build_period_summaries
from backend.database import SessionLocal, ensure_schema
from backend.models import FrameLog
from backend.models import Session as WorkSession
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

CONFIG_PATH = PROJECT_ROOT / "config" / "settings.json"


def load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {"rate_per_hour": 3.0, "rate_per_task": 0.0}


def money(value: float) -> str:
    return f"${value:,.2f}"


def style_figure(fig, accent: str = "#b026ff"):
    if not fig.data:
        fig.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="#101115",
            font=dict(color="#eef2ff"),
        )
        return fig

    palette = [accent, "#1f8cff", "#00c16a", "#ff9800", "#ff5f7a", "#7b61ff"]
    for idx, trace in enumerate(fig.data):
        color = palette[idx % len(palette)]
        trace_type = getattr(trace, "type", "")
        if trace_type in {"bar", "histogram"}:
            trace.marker.color = color
        elif trace_type == "scatter":
            if hasattr(trace, "line"):
                trace.line.color = color
                trace.line.width = 3
            if hasattr(trace, "marker"):
                trace.marker.color = color
                trace.marker.size = 8
        elif trace_type == "pie":
            trace.marker.colors = palette
            trace.hole = 0.42
        elif trace_type == "heatmap":
            trace.colorscale = [
                [0.0, "#111216"],
                [0.35, "#3a1552"],
                [0.7, "#7b1fff"],
                [1.0, "#d27dff"],
            ]

    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#101115",
        font=dict(color="#eef2ff"),
        title=dict(font=dict(size=18, color="#ffffff")),
        margin=dict(l=18, r=18, t=56, b=18),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            bgcolor="rgba(0,0,0,0)",
        ),
    )
    fig.update_xaxes(showgrid=False, color="#aeb4c9")
    fig.update_yaxes(showgrid=True, gridcolor="rgba(255,255,255,0.08)", color="#aeb4c9")
    return fig


def card(title: str, subtitle: str, icon: str, tone: str = "purple") -> str:
    return dedent(
        f"""
        <div class="summary-card">
          <div class="summary-icon {tone}">{icon}</div>
          <div>
            <div class="summary-title">{title}</div>
            <div class="summary-subtitle">{subtitle}</div>
          </div>
        </div>
        """
    ).strip()


def header_button(label: str, view: str, current_view: str) -> str:
    return f'<span class="nav-pill{" active" if view == current_view else ""}">{label}</span>'


def normalize_view(value: str | None) -> str:
    allowed = {"dashboard", "batches", "payments", "quality", "profile"}
    return value if value in allowed else "dashboard"


def view_meta(current_view: str) -> tuple[str, str]:
    mapping = {
        "dashboard": ("Dashboard", "Welcome back. Here’s your local productivity and earnings overview in the Avala Tracker visual style."),
        "batches": ("Batches", "Review every tracked batch and break down the work that has already been completed."),
        "payments": ("Payments", "See how your tracked earnings roll up into daily, weekly, and projected payout views."),
        "quality": ("Quality", "Use your local metrics to spot slower areas, tougher cameras, and consistency trends."),
        "profile": ("Profile", "A quick operational snapshot of your tracker setup, pace, and current working profile."),
    }
    return mapping[current_view]


def to_dataframe(rows: list[dict], columns: list[str]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=columns)
    frame = pd.DataFrame(rows)
    for column in columns:
        if column not in frame.columns:
            frame[column] = None
    return frame[columns]


def stringify_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return str(value)


def build_batch_breakdown(task_rows: list[Task], session_rows: list[WorkSession], frame_logs: list[FrameLog]) -> pd.DataFrame:
    session_summary: dict[int, dict] = defaultdict(lambda: {"hours": 0.0, "frames_completed": 0})
    for session in session_rows:
        summary = session_summary[session.task_id]
        summary["hours"] += (float(session.active_minutes or 0) + float(session.idle_minutes or 0)) / 60.0
        summary["frames_completed"] = max(summary["frames_completed"], int(session.frames_completed or 0))

    box_summary: dict[int, int] = defaultdict(int)
    for log in frame_logs:
        box_summary[log.task_id] += int(log.annotations_created or 0)

    rows = []
    for task in sorted(task_rows, key=lambda item: item.created_at, reverse=True):
        summary = session_summary.get(task.id, {"hours": 0.0, "frames_completed": 0})
        rows.append(
            {
                "Batch ID": task.task_uid,
                "Dataset": task.dataset,
                "Camera": task.camera_name or "unknown",
                "Frames": max(int(task.total_frames or 0), int(summary["frames_completed"])),
                "Boxes": box_summary.get(task.id, 0),
                "Hours": round(summary["hours"], 2),
                "Expected Hrs": round(float(task.expected_hours or 0), 2),
                "Created": task.created_at.strftime("%Y-%m-%d %H:%M") if task.created_at else "",
            }
        )
    return pd.DataFrame(rows)


def render_insights(insights: dict, prediction: dict, metrics: dict) -> None:
    insight_left, insight_right = st.columns([1.35, 1])
    with insight_left:
        st.markdown("### Performance Insights")
        best_hours_df = to_dataframe(insights.get("best_working_hours", []), ["hour", "avg_frames"])
        slowest_tasks_df = to_dataframe(
            insights.get("slowest_tasks", []),
            ["task_uid", "dataset", "camera", "actual_hours", "frames_completed"],
        )
        difficult_cameras_df = to_dataframe(
            insights.get("most_difficult_cameras", []),
            ["camera", "hours", "frames", "frames_per_hour"],
        )

        st.markdown("**Best Working Hours**")
        if best_hours_df.empty:
            st.info("Not enough completed sessions yet.")
        else:
            st.dataframe(best_hours_df, use_container_width=True, hide_index=True)

        st.markdown("**Slowest Tasks**")
        if slowest_tasks_df.empty:
            st.info("No slow-task data yet.")
        else:
            st.dataframe(slowest_tasks_df, use_container_width=True, hide_index=True)

        st.markdown("**Most Difficult Cameras**")
        if difficult_cameras_df.empty:
            st.info("No camera difficulty data yet.")
        else:
            st.dataframe(difficult_cameras_df, use_container_width=True, hide_index=True)

    with insight_right:
        st.markdown("### AI Prediction")
        status = prediction.get("status", "unknown")
        predicted_frames = prediction.get("predicted_next_session_frames", 0)
        trend_slope = prediction.get("trend_slope", 0)
        p1, p2 = st.columns(2)
        p1.metric("Status", str(status).replace("_", " ").title())
        p2.metric("Next Session Frames", predicted_frames if predicted_frames is not None else "—")
        st.metric("Trend Slope", trend_slope if trend_slope is not None else "—")

        details_df = pd.DataFrame(
            [
                {"Field": key.replace("_", " ").title(), "Value": stringify_value(value)}
                for key, value in prediction.items()
            ]
        )
        st.dataframe(details_df, use_container_width=True, hide_index=True)

        st.markdown("### Earnings Snapshot")
        earn1, earn2, earn3 = st.columns(3)
        earn1.metric("Daily", money(metrics["earnings"]["daily"]))
        earn2.metric("Weekly", money(metrics["earnings"]["weekly"]))
        earn3.metric("Monthly Projection", money(metrics["earnings"]["monthly_projection"]))


st.set_page_config(page_title="Avala Tracker Pro", layout="wide")
ensure_schema()

st.markdown(
    dedent(
        """
    <style>
      .stApp {
        background:
          radial-gradient(circle at top left, rgba(152, 64, 255, 0.16), transparent 24%),
          radial-gradient(circle at top right, rgba(31, 140, 255, 0.10), transparent 20%),
          #0b0b0d;
        color: #f4f5f8;
      }
      .block-container {
        max-width: 1120px;
        padding-top: 1.2rem;
        padding-bottom: 3rem;
      }
      .stMetric {
        background: #101115;
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 18px;
        padding: 0.8rem 0.9rem;
      }
      .dashboard-shell {
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 24px;
        overflow: hidden;
        background: rgba(11, 11, 13, 0.94);
        box-shadow: 0 28px 80px rgba(0, 0, 0, 0.28);
      }
      .topbar {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 1rem 1.3rem;
        border-bottom: 1px solid rgba(255,255,255,0.08);
      }
      .brand {
        display: flex;
        gap: 0.65rem;
        align-items: center;
        font-size: 1.05rem;
        font-weight: 700;
      }
      .brand-mark {
        width: 18px;
        height: 18px;
        border-radius: 6px;
        background: linear-gradient(135deg, #ffffff 0%, #b026ff 58%, #1f8cff 100%);
      }
      .nav {
        display: flex;
        gap: 0.7rem;
        color: #c6cad6;
        font-size: 0.95rem;
      }
      .nav-pill {
        padding: 0.45rem 0.8rem;
        border-radius: 10px;
        border: 1px solid rgba(255,255,255,0.05);
        background: rgba(255,255,255,0.02);
      }
      .nav-pill.active {
        background: rgba(255,255,255,0.07);
        color: white;
      }
      div.stButton > button {
        background: rgba(255,255,255,0.02);
        border: 1px solid rgba(255,255,255,0.06);
        border-radius: 10px;
        color: #c6cad6;
        padding: 0.45rem 0.8rem;
      }
      div.stButton > button:hover {
        border-color: rgba(255,255,255,0.14);
        color: #ffffff;
      }
      .hero {
        padding: 2rem 1.35rem 1rem;
      }
      .hero h1 {
        margin: 0;
        font-size: 2.15rem;
        line-height: 1.05;
        color: #ffffff;
      }
      .hero p {
        margin: 0.5rem 0 0;
        color: #b8bfd2;
        font-size: 1rem;
      }
      .section-card {
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 18px;
        background: #101115;
        padding: 1.15rem 1.2rem;
      }
      .summary-card {
        display: flex;
        gap: 0.85rem;
        align-items: center;
        margin-bottom: 1rem;
      }
      .summary-icon {
        width: 40px;
        height: 40px;
        border-radius: 10px;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 1.15rem;
      }
      .summary-icon.purple {
        background: rgba(176, 38, 255, 0.12);
        color: #c95dff;
      }
      .summary-icon.blue {
        background: rgba(31, 140, 255, 0.12);
        color: #55a9ff;
      }
      .summary-icon.gold {
        background: rgba(255, 152, 0, 0.12);
        color: #ffb341;
      }
      .summary-title {
        font-size: 1.05rem;
        font-weight: 700;
        color: #ffffff;
      }
      .summary-subtitle {
        color: #b8bfd2;
        font-size: 0.95rem;
      }
      .stats-grid {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 1rem;
      }
      .stat-kicker {
        color: #b8bfd2;
        font-size: 0.9rem;
        margin-bottom: 0.25rem;
      }
      .stat-value {
        color: #ffffff;
        font-size: 1.95rem;
        font-weight: 800;
        line-height: 1.1;
      }
      .stat-value.green { color: #00c16a; }
      .stat-value.orange { color: #ff9800; }
      .notice-card {
        margin-top: 1.1rem;
        border: 1px solid rgba(176, 38, 255, 0.35);
        border-radius: 16px;
        background: rgba(44, 18, 58, 0.42);
        padding: 1rem 1.2rem;
      }
      .notice-title {
        color: #ffffff;
        font-weight: 700;
      }
      .notice-subtitle {
        color: #c8bfd8;
      }
      .card-title {
        color: #ffffff;
        font-size: 1.05rem;
        font-weight: 700;
      }
      .card-subtitle {
        color: #b8bfd2;
        margin-bottom: 1rem;
      }
      .timeline-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 1.2rem 2rem;
      }
      .timeline-label {
        color: #d4d9e8;
        font-size: 0.9rem;
      }
      .timeline-value {
        color: #ffffff;
        font-size: 1.8rem;
        font-weight: 800;
        line-height: 1.15;
      }
      .timeline-note {
        color: #9fa8bf;
        font-size: 0.88rem;
      }
      div[data-testid="stTabs"] button {
        color: #cad0df;
      }
      div[data-testid="stTabs"] button[aria-selected="true"] {
        color: #ffffff;
      }
      .stDataFrame, .st-emotion-cache-1v0mbdj {
        border-radius: 16px;
      }
      .footer-note {
        color: #8e95ab;
        font-size: 0.88rem;
        margin-top: 1rem;
      }
    </style>
    """
    ),
    unsafe_allow_html=True,
)

config = load_config()
rate_per_hour = float(config.get("rate_per_hour", 3.0))
rate_per_task = float(config.get("rate_per_task", 0.0))
if "nav_view" not in st.session_state:
    st.session_state.nav_view = "dashboard"
current_view = normalize_view(st.session_state.nav_view)
hero_title, hero_subtitle = view_meta(current_view)

st.markdown('<div class="dashboard-shell"><div class="topbar">', unsafe_allow_html=True)
brand_col, nav_col = st.columns([2.3, 4.7])
with brand_col:
    st.markdown(
        '<div class="brand"><span class="brand-mark"></span><span>avala <span style="font-weight:500;color:#ccd2e2;">Tracker</span></span></div>',
        unsafe_allow_html=True,
    )
with nav_col:
    nav_specs = [
        ("Dashboard", "dashboard"),
        ("Batches", "batches"),
        ("Payments", "payments"),
        ("Quality", "quality"),
        ("Profile", "profile"),
    ]
    nav_cols = st.columns(len(nav_specs))
    for idx, (label, view) in enumerate(nav_specs):
        with nav_cols[idx]:
            if view == current_view:
                st.markdown(
                    f'<div class="nav-pill active" style="text-align:center;">{label}</div>',
                    unsafe_allow_html=True,
                )
                continue
            if st.button(label, key=f"nav_{view}", use_container_width=True):
                st.session_state.nav_view = view

st.markdown(
    dedent(
        f"""
        </div>
        <div class="hero">
          <h1>{hero_title}</h1>
          <p>{hero_subtitle}</p>
        </div>
        </div>
        """
    ),
    unsafe_allow_html=True,
)

with SessionLocal() as db:
    metrics = compute_core_metrics(db)
    insights = build_performance_insights(db)
    heatmap_data = build_heatmap_data(db)
    frame_speed = calculate_frame_speed(db)
    prediction = productivity_trend_prediction(db)
    period_summaries = build_period_summaries(db)

    task_rows = db.query(Task).all()
    session_rows = db.query(WorkSession).all()
    frame_logs = db.query(FrameLog).all()
    task_map = {t.id: t for t in task_rows}

    speed_df = pd.DataFrame(frame_speed)
    tasks_per_day = pd.DataFrame(heatmap_data)
    batch_df = build_batch_breakdown(task_rows, session_rows, frame_logs)

    efficiency_rows = []
    for session in session_rows:
        task = task_map.get(session.task_id)
        if not task:
            continue
        actual_hours = (session.active_minutes + session.idle_minutes) / 60.0
        efficiency_rows.append(
            {
                "task_uid": task.task_uid,
                "expected_hours": task.expected_hours or 0,
                "actual_hours": round(actual_hours, 3),
            }
        )
    efficiency_df = pd.DataFrame(efficiency_rows)

    total_hours = sum((s.active_minutes + s.idle_minutes) for s in session_rows) / 60.0
    total_earned = (total_hours * rate_per_hour) + (len(task_rows) * rate_per_task)
    current_balance = float(metrics["earnings"]["weekly"])
    paid_estimate = max(total_earned - current_balance, 0.0)
    hourly_yield = total_earned / total_hours if total_hours > 0 else 0.0
    next_expected = float(metrics["earnings"]["daily"])

    if current_view == "dashboard":
        summary_left, summary_right = st.columns(2)
        with summary_left:
            st.markdown(
                dedent(
                    f"""
                <div class="section-card">
                  {card("Earnings", "Your earnings summary", "$", "purple")}
                  <div class="stats-grid">
                    <div>
                      <div class="stat-kicker">Earned</div>
                      <div class="stat-value">{money(total_earned)}</div>
                    </div>
                    <div>
                      <div class="stat-kicker">Projected</div>
                      <div class="stat-value green">{money(metrics['earnings']['monthly_projection'])}</div>
                    </div>
                    <div>
                      <div class="stat-kicker">This Week</div>
                      <div class="stat-value orange">{money(metrics['earnings']['weekly'])}</div>
                    </div>
                  </div>
                </div>
                """
                ),
                unsafe_allow_html=True,
            )
        with summary_right:
            st.markdown(
                dedent(
                    f"""
                <div class="section-card">
                  {card("Performance", "Your work metrics", "▥", "blue")}
                  <div class="stats-grid">
                    <div>
                      <div class="stat-kicker">Items</div>
                      <div class="stat-value">{metrics['tasks_completed']}</div>
                    </div>
                    <div>
                      <div class="stat-kicker">Hours</div>
                      <div class="stat-value">{total_hours:.1f}</div>
                    </div>
                    <div>
                      <div class="stat-kicker">$/Hour</div>
                      <div class="stat-value">{money(hourly_yield) if hourly_yield else "—"}</div>
                    </div>
                  </div>
                </div>
                """
                ),
                unsafe_allow_html=True,
            )

        st.markdown(
            dedent(
                """
            <div class="notice-card">
              <div class="notice-title">Transaction fees covered</div>
              <div class="notice-subtitle">This local dashboard mirrors the Avala Tracker feel while keeping your original analytics workflow intact.</div>
            </div>
            """
            ),
            unsafe_allow_html=True,
        )

        st.markdown(
            dedent(
                f"""
            <div class="section-card" style="margin-top:1.1rem;">
              {card("Analytics Timeline", "Track your work and payout momentum", "◴", "blue")}
              <div class="timeline-grid">
                <div>
                  <div class="timeline-label">Daily Earnings</div>
                  <div class="timeline-value">{money(metrics['earnings']['daily'])}</div>
                  <div class="timeline-note">Estimated from your configured rates</div>
                </div>
                <div>
                  <div class="timeline-label">Next Focus Metric</div>
                  <div class="timeline-value">{next_expected:.2f}</div>
                  <div class="timeline-note">Current daily earnings pace</div>
                </div>
                <div>
                  <div class="timeline-label">Tasks Completed Today</div>
                  <div class="timeline-value">{metrics['tasks_completed_today']}</div>
                  <div class="timeline-note">Frames today: {metrics['frames_annotated_today']}</div>
                </div>
                <div>
                  <div class="timeline-label">Hours Worked Today</div>
                  <div class="timeline-value">{metrics['hours_worked_today']:.2f}</div>
                  <div class="timeline-note">Efficiency ratio: {metrics['efficiency_ratio']}</div>
                </div>
              </div>
            </div>
            """
            ),
            unsafe_allow_html=True,
        )

        quick1, quick2, quick3, quick4, quick5 = st.columns(5)
        quick1.metric("Tasks Today", metrics["tasks_completed_today"])
        quick2.metric("Frames Today", metrics["frames_annotated_today"])
        quick3.metric("Boxes Today", metrics.get("boxes_annotated_today", 0))
        quick4.metric("Hours Today", metrics["hours_worked_today"])
        quick5.metric("Efficiency", metrics["efficiency_ratio"])

        tabs = st.tabs(["Analytics", "Progress", "Insights"])

        with tabs[0]:
            left, right = st.columns(2)
            with left:
                st.plotly_chart(
                    style_figure(bar_frames_per_hour(speed_df)),
                    use_container_width=True,
                    key="frames_per_hour_chart",
                )
            with right:
                st.plotly_chart(
                    style_figure(line_tasks_per_day(tasks_per_day), accent="#1f8cff"),
                    use_container_width=True,
                    key="tasks_per_day_chart",
                )

            left, right = st.columns(2)
            with left:
                st.plotly_chart(
                    style_figure(line_efficiency(efficiency_df)),
                    use_container_width=True,
                    key="efficiency_trends_chart",
                )
            with right:
                st.plotly_chart(
                    style_figure(pie_distribution(metrics["camera_distribution"], "Camera Distribution"), accent="#1f8cff"),
                    use_container_width=True,
                    key="camera_distribution_chart",
                )

            left, right = st.columns(2)
            with left:
                st.plotly_chart(
                    style_figure(line_frame_speed(speed_df), accent="#00c16a"),
                    use_container_width=True,
                    key="frame_speed_chart",
                )
            with right:
                if session_rows:
                    dataset_minutes = {}
                    for session in session_rows:
                        task = task_map.get(session.task_id)
                        if not task:
                            continue
                        dataset_minutes[task.dataset] = dataset_minutes.get(task.dataset, 0.0) + session.active_minutes
                    st.plotly_chart(
                        style_figure(
                            pie_distribution(
                                {key: round(value / 60.0, 2) for key, value in dataset_minutes.items()},
                                "Hours Per Dataset",
                            ),
                            accent="#ff9800",
                        ),
                        use_container_width=True,
                        key="dataset_hours_chart",
                    )
                else:
                    st.info("No session data yet.")

            st.plotly_chart(
                style_figure(heatmap_tasks(tasks_per_day), accent="#b026ff"),
                use_container_width=True,
                key="productivity_heatmap_chart",
            )

        with tabs[1]:
            weekly_df = pd.DataFrame(period_summaries.get("weekly", []))
            monthly_df = pd.DataFrame(period_summaries.get("monthly", []))

            st.markdown("### Weekly Progress")
            if not weekly_df.empty:
                left, right = st.columns(2)
                with left:
                    st.plotly_chart(
                        style_figure(bar_period_metric(weekly_df, "period", "tasks_completed", "Tasks Completed Per Week")),
                        use_container_width=True,
                        key="weekly_tasks_chart",
                    )
                with right:
                    st.plotly_chart(
                        style_figure(bar_period_metric(weekly_df, "period", "frames_annotated", "Frames Annotated Per Week"), accent="#1f8cff"),
                        use_container_width=True,
                        key="weekly_frames_chart",
                    )

                left, right = st.columns(2)
                with left:
                    st.plotly_chart(
                        style_figure(bar_period_metric(weekly_df, "period", "hours_worked", "Hours Worked Per Week"), accent="#00c16a"),
                        use_container_width=True,
                        key="weekly_hours_chart",
                    )
                with right:
                    st.plotly_chart(
                        style_figure(bar_period_metric(weekly_df, "period", "boxes_annotated", "Boxes Annotated Per Week"), accent="#ff9800"),
                        use_container_width=True,
                        key="weekly_boxes_chart",
                    )

                st.plotly_chart(
                    style_figure(line_period_metric(weekly_df, "period", "efficiency_ratio", "Efficiency Ratio Per Week"), accent="#ff5f7a"),
                    use_container_width=True,
                    key="weekly_efficiency_chart",
                )
                st.dataframe(weekly_df, use_container_width=True, hide_index=True)
            else:
                st.info("No weekly data yet.")

            st.markdown("### Monthly Progress")
            if not monthly_df.empty:
                left, right = st.columns(2)
                with left:
                    st.plotly_chart(
                        style_figure(bar_period_metric(monthly_df, "period", "tasks_completed", "Tasks Completed Per Month")),
                        use_container_width=True,
                        key="monthly_tasks_chart",
                    )
                with right:
                    st.plotly_chart(
                        style_figure(bar_period_metric(monthly_df, "period", "frames_annotated", "Frames Annotated Per Month"), accent="#1f8cff"),
                        use_container_width=True,
                        key="monthly_frames_chart",
                    )

                left, right = st.columns(2)
                with left:
                    st.plotly_chart(
                        style_figure(bar_period_metric(monthly_df, "period", "hours_worked", "Hours Worked Per Month"), accent="#00c16a"),
                        use_container_width=True,
                        key="monthly_hours_chart",
                    )
                with right:
                    st.plotly_chart(
                        style_figure(bar_period_metric(monthly_df, "period", "boxes_annotated", "Boxes Annotated Per Month"), accent="#ff9800"),
                        use_container_width=True,
                        key="monthly_boxes_chart",
                    )

                st.plotly_chart(
                    style_figure(line_period_metric(monthly_df, "period", "efficiency_ratio", "Efficiency Ratio Per Month"), accent="#ff5f7a"),
                    use_container_width=True,
                    key="monthly_efficiency_chart",
                )
                st.dataframe(monthly_df, use_container_width=True, hide_index=True)
            else:
                st.info("No monthly data yet.")

            st.markdown("### Rolling 7-Day Average")
            if not tasks_per_day.empty:
                rolling_df = tasks_per_day.copy()
                rolling_df["date"] = pd.to_datetime(rolling_df["date"])
                rolling_df = rolling_df.sort_values("date")
                rolling_df["tasks_7d_avg"] = rolling_df["tasks_completed"].rolling(7, min_periods=1).mean()
                st.plotly_chart(
                    style_figure(line_period_metric(rolling_df, "date", "tasks_7d_avg", "7-Day Avg Tasks Completed"), accent="#7b61ff"),
                    use_container_width=True,
                    key="rolling_tasks_chart",
                )
            else:
                st.info("No rolling task data yet.")

        with tabs[2]:
            render_insights(insights, prediction, metrics)

    elif current_view == "batches":
        st.markdown("### Completed Batches")
        if batch_df.empty:
            st.info("No tracked batches yet. Start work in Avala and they will appear here.")
        else:
            top1, top2, top3, top4 = st.columns(4)
            top1.metric("Batches Done", len(batch_df))
            top2.metric("Frames Logged", int(batch_df["Frames"].sum()))
            top3.metric("Boxes Added", int(batch_df["Boxes"].sum()))
            top4.metric("Hours Spent", f"{batch_df['Hours'].sum():.2f}")
            st.dataframe(batch_df, use_container_width=True, hide_index=True)

            st.markdown("### Batch Breakdown")
            left, right = st.columns(2)
            with left:
                dataset_breakdown = (
                    batch_df.groupby("Dataset", as_index=False)
                    .agg({"Batch ID": "count", "Frames": "sum", "Hours": "sum"})
                    .rename(columns={"Batch ID": "Batches"})
                    .sort_values(["Batches", "Frames"], ascending=False)
                )
                st.dataframe(dataset_breakdown, use_container_width=True, hide_index=True)
            with right:
                camera_breakdown = (
                    batch_df.groupby("Camera", as_index=False)
                    .agg({"Batch ID": "count", "Frames": "sum", "Hours": "sum"})
                    .rename(columns={"Batch ID": "Batches"})
                    .sort_values(["Batches", "Frames"], ascending=False)
                )
                st.dataframe(camera_breakdown, use_container_width=True, hide_index=True)

    elif current_view == "payments":
        p1, p2, p3, p4 = st.columns(4)
        p1.metric("Earned", money(total_earned))
        p2.metric("Today", money(metrics["earnings"]["daily"]))
        p3.metric("This Week", money(metrics["earnings"]["weekly"]))
        p4.metric("Monthly Projection", money(metrics["earnings"]["monthly_projection"]))
        payments_df = pd.DataFrame(
            [
                {"Window": "Daily", "Amount": metrics["earnings"]["daily"]},
                {"Window": "Weekly", "Amount": metrics["earnings"]["weekly"]},
                {"Window": "Monthly Projection", "Amount": metrics["earnings"]["monthly_projection"]},
                {"Window": "Estimated Paid", "Amount": round(paid_estimate, 2)},
            ]
        )
        st.dataframe(payments_df, use_container_width=True, hide_index=True)

    elif current_view == "quality":
        render_insights(insights, prediction, metrics)

    elif current_view == "profile":
        profile1, profile2, profile3, profile4 = st.columns(4)
        profile1.metric("Tracked Tasks", len(task_rows))
        profile2.metric("Tracked Sessions", len(session_rows))
        profile3.metric("Configured $/Hour", money(rate_per_hour))
        profile4.metric("Configured $/Task", money(rate_per_task))
        config_df = pd.DataFrame(
            [{"Setting": key, "Value": stringify_value(value)} for key, value in config.items()]
        )
        st.dataframe(config_df, use_container_width=True, hide_index=True)

st.markdown(
    "<div class='footer-note'>Runs fully local. Collects analytics only. Does not automate annotation.</div>",
    unsafe_allow_html=True,
)
