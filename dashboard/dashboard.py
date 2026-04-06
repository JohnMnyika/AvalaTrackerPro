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
from backend.models import ContributionDay, FrameLog
from backend.models import Session as WorkSession
from backend.models import Task
from charts import (
    bar_batch_profitability,
    bar_camera_progress,
    bar_frames_per_hour,
    bar_period_metric,
    heatmap_tasks,
    line_earnings_over_time,
    line_efficiency,
    line_frame_speed,
    line_period_metric,
    line_tasks_per_day,
    pie_distribution,
    usd_kes_comparison,
)
from audit_dashboard import display_audit_dashboard
from tracker.frame_tracker import calculate_frame_speed

CONFIG_PATH = PROJECT_ROOT / "config" / "settings.json"


def load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {"rate_per_hour": 3.0, "rate_per_task": 0.0}


def money(value: float) -> str:
    return f"${value:,.2f}"


def status_tone(status: str | None) -> str:
    mapping = {
        "synced": "green",
        "ok": "green",
        "scraped_rows": "blue",
        "sections_found": "orange",
        "waiting_for_sync": "orange",
        "backend_error": "orange",
        "stale": "orange",
    }
    return mapping.get((status or "").lower(), "purple")


def style_payment_status_table(df: pd.DataFrame):
    if df.empty or "Status" not in df.columns:
        return df

    def highlight_status(value: object) -> str:
        normalized = str(value or "").strip().lower()
        if normalized == "paid":
            return "background-color: rgba(34, 197, 94, 0.18); color: #bbf7d0; font-weight: 600;"
        if normalized == "unpaid":
            return "background-color: rgba(249, 115, 22, 0.18); color: #fed7aa; font-weight: 600;"
        return ""

    return df.style.map(highlight_status, subset=["Status"])


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
    allowed = {"dashboard", "batches", "payments", "quality", "profile", "audit"}
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


@st.cache_data(ttl=5, show_spinner=False)
def load_dashboard_snapshot() -> dict:
    ensure_schema()
    with SessionLocal() as db:
        task_rows = db.query(Task).all()
        session_rows = db.query(WorkSession).all()
        frame_logs = db.query(FrameLog).all()
        contribution_days = db.query(ContributionDay).all()
        joined_frame_logs = (
            db.query(FrameLog, Task)
            .join(Task, Task.id == FrameLog.task_id)
            .order_by(FrameLog.timestamp.asc())
            .all()
        )
        task_map = {t.id: t for t in task_rows}

        metrics = compute_core_metrics(db)
        try:
            insights = build_performance_insights(db, tasks=task_rows, sessions=session_rows)
        except TypeError:
            insights = build_performance_insights(db)
        try:
            heatmap_data = build_heatmap_data(db, tasks=task_rows)
        except TypeError:
            heatmap_data = build_heatmap_data(db)
        try:
            frame_speed = calculate_frame_speed(db, joined_logs=joined_frame_logs)
        except TypeError:
            frame_speed = calculate_frame_speed(db)
        try:
            prediction = productivity_trend_prediction(db, sessions=session_rows)
        except TypeError:
            prediction = productivity_trend_prediction(db)
        try:
            period_summaries = build_period_summaries(
                db,
                tasks=task_rows,
                sessions=session_rows,
                logs=frame_logs,
                contribution_days=contribution_days,
            )
        except TypeError:
            period_summaries = build_period_summaries(db)

        speed_df = pd.DataFrame(frame_speed)
        tasks_per_day = pd.DataFrame(heatmap_data)
        batch_df = build_batch_breakdown(task_rows, session_rows, frame_logs)
        camera_progress_df = pd.DataFrame()
        if not batch_df.empty:
            camera_progress_df = (
                batch_df.groupby("Camera", as_index=False)
                .agg({"Frames": "sum", "Boxes": "sum", "Hours": "sum"})
                .sort_values(["Frames", "Boxes"], ascending=False)
            )

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

        dataset_minutes = {}
        for session in session_rows:
            task = task_map.get(session.task_id)
            if not task:
                continue
            dataset_minutes[task.dataset] = dataset_minutes.get(task.dataset, 0.0) + float(session.active_minutes or 0)

        total_hours = sum((float(s.active_minutes or 0) + float(s.idle_minutes or 0)) for s in session_rows) / 60.0

        return {
            "metrics": metrics,
            "insights": insights,
            "heatmap_data": heatmap_data,
            "frame_speed": frame_speed,
            "prediction": prediction,
            "period_summaries": period_summaries,
            "speed_df": speed_df,
            "tasks_per_day": tasks_per_day,
            "batch_df": batch_df,
            "camera_progress_df": camera_progress_df,
            "efficiency_df": efficiency_df,
            "dataset_minutes": {key: round(value / 60.0, 2) for key, value in dataset_minutes.items()},
            "task_count": len(task_rows),
            "session_count": len(session_rows),
            "frame_log_count": len(frame_logs),
            "total_hours": total_hours,
        }


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
            st.dataframe(best_hours_df, width='stretch', hide_index=True)

        st.markdown("**Slowest Tasks**")
        if slowest_tasks_df.empty:
            st.info("No slow-task data yet.")
        else:
            st.dataframe(slowest_tasks_df, width='stretch', hide_index=True)

        st.markdown("**Most Difficult Cameras**")
        if difficult_cameras_df.empty:
            st.info("No camera difficulty data yet.")
        else:
            st.dataframe(difficult_cameras_df, width='stretch', hide_index=True)

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
        st.dataframe(details_df, width='stretch', hide_index=True)

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
        ("Audit", "audit"),
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
            if st.button(label, key=f"nav_{view}", width='stretch'):
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

snapshot = load_dashboard_snapshot()
metrics = snapshot["metrics"]
insights = snapshot["insights"]
heatmap_data = snapshot["heatmap_data"]
frame_speed = snapshot["frame_speed"]
prediction = snapshot["prediction"]
period_summaries = snapshot["period_summaries"]
payment_metrics = metrics.get("payments", {})
earnings_over_time_df = pd.DataFrame(payment_metrics.get("earnings_per_day", []))
earnings_per_batch_df = pd.DataFrame(payment_metrics.get("earnings_per_batch", []))
profitability_per_hour_df = pd.DataFrame(payment_metrics.get("profitability_per_hour", []))
top_paying_batches_df = pd.DataFrame(payment_metrics.get("top_paying_batches", []))
best_paying_datasets_df = pd.DataFrame(payment_metrics.get("best_paying_datasets", []))
usd_kes_df = pd.DataFrame(payment_metrics.get("usd_vs_kes", []))
tracked_batches_df = pd.DataFrame(payment_metrics.get("tracked_batches", []))
unpaid_batches_df = pd.DataFrame(payment_metrics.get("unpaid_batches", []))
payment_sync_debug = payment_metrics.get("payment_sync_debug", {})
extension_status = payment_metrics.get("extension_status", {})

speed_df = snapshot["speed_df"]
tasks_per_day = snapshot["tasks_per_day"]
batch_df = snapshot["batch_df"]
camera_progress_df = snapshot["camera_progress_df"]
efficiency_df = snapshot["efficiency_df"]
dataset_minutes = snapshot["dataset_minutes"]
task_count = int(snapshot["task_count"])
session_count = int(snapshot["session_count"])
frame_log_count = int(snapshot["frame_log_count"])
total_hours = float(snapshot["total_hours"])

total_earned = (total_hours * rate_per_hour) + (task_count * rate_per_task)
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
                width='stretch',
                key="frames_per_hour_chart",
            )
        with right:
            st.plotly_chart(
                style_figure(line_tasks_per_day(tasks_per_day), accent="#1f8cff"),
                width='stretch',
                key="tasks_per_day_chart",
            )

        left, right = st.columns(2)
        with left:
            st.plotly_chart(
                style_figure(line_efficiency(efficiency_df)),
                width='stretch',
                key="efficiency_trends_chart",
            )
        with right:
            st.plotly_chart(
                style_figure(pie_distribution(metrics["camera_distribution"], "Camera Distribution"), accent="#1f8cff"),
                width='stretch',
                key="camera_distribution_chart",
            )

        left, right = st.columns(2)
        with left:
            st.plotly_chart(
                style_figure(line_frame_speed(speed_df), accent="#00c16a"),
                width='stretch',
                key="frame_speed_chart",
            )
        with right:
            if dataset_minutes:
                st.plotly_chart(
                    style_figure(
                        pie_distribution(dataset_minutes, "Hours per Dataset"),
                        accent="#ff9800",
                    ),
                    width='stretch',
                    key="dataset_hours_chart",
                )
            else:
                st.info("No session data yet.")

        st.plotly_chart(
            style_figure(heatmap_tasks(tasks_per_day), accent="#b026ff"),
            width='stretch',
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
                    width='stretch',
                    key="weekly_tasks_chart",
                )
            with right:
                st.plotly_chart(
                    style_figure(bar_period_metric(weekly_df, "period", "frames_annotated", "Frames Annotated Per Week"), accent="#1f8cff"),
                    width='stretch',
                    key="weekly_frames_chart",
                )

            left, right = st.columns(2)
            with left:
                st.plotly_chart(
                    style_figure(bar_period_metric(weekly_df, "period", "hours_worked", "Hours Worked Per Week"), accent="#00c16a"),
                    width='stretch',
                    key="weekly_hours_chart",
                )
            with right:
                st.plotly_chart(
                    style_figure(bar_period_metric(weekly_df, "period", "boxes_annotated", "Boxes Annotated Per Week"), accent="#ff9800"),
                    width='stretch',
                    key="weekly_boxes_chart",
                )

            st.plotly_chart(
                style_figure(line_period_metric(weekly_df, "period", "efficiency_ratio", "Efficiency Ratio Per Week"), accent="#ff5f7a"),
                width='stretch',
                key="weekly_efficiency_chart",
            )
            st.dataframe(weekly_df, width='stretch', hide_index=True)
        else:
            st.info("No weekly data yet.")

        st.markdown("### Monthly Progress")
        if not monthly_df.empty:
            left, right = st.columns(2)
            with left:
                st.plotly_chart(
                    style_figure(bar_period_metric(monthly_df, "period", "tasks_completed", "Tasks Completed Per Month")),
                    width='stretch',
                    key="monthly_tasks_chart",
                )
            with right:
                st.plotly_chart(
                    style_figure(bar_period_metric(monthly_df, "period", "frames_annotated", "Frames Annotated Per Month"), accent="#1f8cff"),
                    width='stretch',
                    key="monthly_frames_chart",
                )

            left, right = st.columns(2)
            with left:
                st.plotly_chart(
                    style_figure(bar_period_metric(monthly_df, "period", "hours_worked", "Hours Worked Per Month"), accent="#00c16a"),
                    width='stretch',
                    key="monthly_hours_chart",
                )
            with right:
                st.plotly_chart(
                    style_figure(bar_period_metric(monthly_df, "period", "boxes_annotated", "Boxes Annotated Per Month"), accent="#ff9800"),
                    width='stretch',
                    key="monthly_boxes_chart",
                )

            st.plotly_chart(
                style_figure(line_period_metric(monthly_df, "period", "efficiency_ratio", "Efficiency Ratio Per Month"), accent="#ff5f7a"),
                width='stretch',
                key="monthly_efficiency_chart",
            )
            st.dataframe(monthly_df, width='stretch', hide_index=True)
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
                width='stretch',
                key="rolling_tasks_chart",
            )
        else:
            st.info("No rolling task data yet.")

        st.markdown("### Camera Progress")
        if not camera_progress_df.empty:
            st.plotly_chart(
                style_figure(bar_camera_progress(camera_progress_df), accent="#1f8cff"),
                width='stretch',
                key="camera_progress_chart",
            )
            st.dataframe(camera_progress_df, width='stretch', hide_index=True)
        else:
            st.info("No camera progress data yet.")

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
        st.dataframe(batch_df, width='stretch', hide_index=True)

        st.markdown("### Batch Breakdown")
        left, right = st.columns(2)
        with left:
            dataset_breakdown = (
                batch_df.groupby("Dataset", as_index=False)
                .agg({"Batch ID": "count", "Frames": "sum", "Hours": "sum"})
                .rename(columns={"Batch ID": "Batches"})
                .sort_values(["Batches", "Frames"], ascending=False)
            )
            st.dataframe(dataset_breakdown, width='stretch', hide_index=True)
        with right:
            camera_breakdown = (
                batch_df.groupby("Camera", as_index=False)
                .agg({"Batch ID": "count", "Frames": "sum", "Hours": "sum"})
                .rename(columns={"Batch ID": "Batches"})
                .sort_values(["Batches", "Frames"], ascending=False)
            )
            st.dataframe(camera_breakdown, width='stretch', hide_index=True)

elif current_view == "payments":
    current_month_usd = 0.0
    if not earnings_over_time_df.empty:
        earnings_over_time_df["date"] = pd.to_datetime(earnings_over_time_df["date"])
        current_month = pd.Timestamp.now().strftime("%Y-%m")
        current_month_usd = float(
            earnings_over_time_df[
                earnings_over_time_df["date"].dt.strftime("%Y-%m") == current_month
            ]["amount_usd"].sum()
        )

    sync_status_value = str(payment_metrics.get("payment_sync_status", "waiting_for_sync")).replace("_", " ").title()
    sync_tone = status_tone(payment_metrics.get("payment_sync_status"))
    st.markdown(
        dedent(
            f"""
        <div class="section-card" style="margin-bottom:1rem;">
          {card("Payment Sync", f"Status: {sync_status_value}", "◎", sync_tone)}
          <div class="stats-grid">
            <div>
              <div class="stat-kicker">Extension</div>
              <div class="stat-value">{"Connected" if extension_status.get('connected') else "Offline"}</div>
            </div>
            <div>
              <div class="stat-kicker">Recent Work Synced</div>
              <div class="stat-value">{int(payment_metrics.get('recent_work_synced_count', 0))}</div>
            </div>
            <div>
              <div class="stat-kicker">History Rows</div>
              <div class="stat-value">{int(payment_metrics.get('payment_history_synced_count', 0))}</div>
            </div>
            <div>
              <div class="stat-kicker">Last Batch Sync</div>
              <div class="stat-value" style="font-size:1.15rem;">{payment_metrics.get('last_recent_work_sync_at', '—') or '—'}</div>
            </div>
          </div>
        </div>
        """
        ),
        unsafe_allow_html=True,
    )
    if payment_metrics.get("last_payment_history_date"):
        st.caption(f"Latest payment history date: {payment_metrics['last_payment_history_date']}")
    else:
        st.caption("Open pay.avala.ai/dashboard and wait for the Recent Work Added and Payment History sections to render fully.")

    has_payment_data = bool(
        float(payment_metrics.get("total_earnings_usd", 0.0)) > 0
        or int(payment_metrics.get("recent_work_synced_count", 0)) > 0
        or int(payment_metrics.get("payment_history_synced_count", 0)) > 0
    )

    if not has_payment_data:
        stage_label = str(payment_sync_debug.get("last_status") or "waiting_for_sync").replace("_", " ").title()
        st.markdown(
            dedent(
                f"""
            <div class="section-card" style="margin-bottom:1rem; border:1px solid rgba(255,255,255,0.08);">
              <div style="display:flex; justify-content:space-between; gap:1rem; align-items:flex-start; flex-wrap:wrap;">
                <div>
                  <div class="summary-title" style="margin-bottom:0.35rem;">Payment Sync Setup</div>
                  <div class="summary-subtitle">The tracker has not captured payment rows yet. Use the checklist below, then refresh this page.</div>
                </div>
                <div class="summary-subtitle" style="font-weight:700; color:#cdd6ea;">Stage: {stage_label}</div>
              </div>
              <div class="timeline-grid" style="margin-top:1rem;">
                <div>
                  <div class="timeline-label">1. Reload Extension</div>
                  <div class="timeline-note">Open chrome://extensions and click Reload on Avala Tracker Pro.</div>
                </div>
                <div>
                  <div class="timeline-label">2. Open Pay Dashboard</div>
                  <div class="timeline-note">Visit pay.avala.ai/dashboard and keep it open for a few seconds.</div>
                </div>
                <div>
                  <div class="timeline-label">3. Confirm Page Detection</div>
                  <div class="timeline-note">The debug panel below should switch Pay Page Detected to Yes.</div>
                </div>
                <div>
                  <div class="timeline-label">4. Refresh Tracker</div>
                  <div class="timeline-note">Refresh Streamlit after the payment rows appear in the debug counters.</div>
                </div>
              </div>
            </div>
            """
            ),
            unsafe_allow_html=True,
        )

    with st.expander("Payment Sync Debug", expanded=not has_payment_data):
        debug1, debug2, debug3, debug4 = st.columns(4)
        debug1.metric("Extension", "Connected" if extension_status.get("connected") else "Offline")
        debug2.metric("Pay Page Detected", "Yes" if payment_sync_debug.get("page_detected") else "No")
        debug3.metric("Recent Work Section", "Found" if payment_sync_debug.get("recent_work_section_found") else "Missing")
        debug4.metric("Payment History Section", "Found" if payment_sync_debug.get("payment_history_section_found") else "Missing")
        debug_meta_df = pd.DataFrame([
            {
                "Extension Page Type": extension_status.get("page_type") or "—",
                "Extension Last Seen": extension_status.get("last_seen_at") or "—",
                "Backend Status": str(payment_sync_debug.get("backend_status_code") or "—"),
            }
        ])
        st.dataframe(debug_meta_df, width='stretch', hide_index=True)
        debug_df = pd.DataFrame([
            {
                "Last Stage": str(payment_sync_debug.get("last_status") or "waiting_for_sync").replace("_", " ").title(),
                "Recent Work Rows": int(payment_sync_debug.get("recent_work_rows") or 0),
                "Payment History Rows": int(payment_sync_debug.get("payment_history_rows") or 0),
                "Last Attempt": payment_sync_debug.get("last_attempt_at") or "—",
                "Last Success": payment_sync_debug.get("last_success_at") or "—",
                "Last Error": payment_sync_debug.get("last_error") or "",
                "Page URL": payment_sync_debug.get("page_url") or "",
            }
        ])
        st.dataframe(debug_df, width='stretch', hide_index=True)
        if payment_sync_debug.get("page_fingerprint"):
            st.code(payment_sync_debug.get("page_fingerprint"), language="json")

    p1, p2, p3, p4 = st.columns(4)
    p1.metric("This Month", money(current_month_usd))
    p2.metric("Total Earnings", money(float(payment_metrics.get("total_earnings_usd", 0.0))))
    p3.metric("Avg / Task", money(float(payment_metrics.get("average_earning_per_task", 0.0))))
    p4.metric("Earnings / Hour", money(float(payment_metrics.get("earnings_per_hour", 0.0))))

    left, right = st.columns(2)
    with left:
        st.plotly_chart(
            style_figure(line_earnings_over_time(earnings_over_time_df), accent="#00c16a"),
            width='stretch',
            key="earnings_over_time_chart",
        )
    with right:
        st.plotly_chart(
            style_figure(bar_batch_profitability(earnings_per_batch_df), accent="#ff9800"),
            width='stretch',
            key="batch_profitability_chart",
        )

    left, right = st.columns(2)
    with left:
        st.plotly_chart(
            style_figure(usd_kes_comparison(usd_kes_df), accent="#1f8cff"),
            width='stretch',
            key="usd_kes_chart",
        )
    with right:
        st.markdown("### Top Paying Batches")
        if top_paying_batches_df.empty:
            st.info("Open pay.avala.ai/dashboard to sync payment data.")
        else:
            st.dataframe(top_paying_batches_df.head(5), width='stretch', hide_index=True)

    left, right = st.columns(2)
    with left:
        st.markdown("### Profitability Per Hour")
        if profitability_per_hour_df.empty:
            st.info("Profitability will appear after batch payments and tracked hours overlap.")
        else:
            st.dataframe(profitability_per_hour_df, width='stretch', hide_index=True)
    with right:
        st.markdown("### Best Paying Datasets")
        if best_paying_datasets_df.empty:
            st.info("Dataset profitability will appear once payments match tracked work.")
        else:
            st.dataframe(best_paying_datasets_df, width='stretch', hide_index=True)

    st.markdown("### Tracked Batch Payment Status")
    if tracked_batches_df.empty:
        st.info("Tracked batches will appear here after you work on Avala tasks.")
    else:
        status_filter = st.segmented_control(
            "Batch Status Filter",
            options=["All", "Paid", "Unpaid"],
            default="All",
            key="tracked_batch_status_filter",
        )
        tracked_display_df = tracked_batches_df.rename(
            columns={
                "batch_name": "Batch",
                "payment_status": "Status",
                "task_count": "Tasks",
                "frames_logged": "Frames",
                "hours_spent": "Hours",
                "amount_usd": "Paid USD",
                "paid_entries": "Payment Entries",
                "last_tracked_at": "Last Tracked",
            }
        )
        if status_filter and status_filter != "All":
            tracked_display_df = tracked_display_df[tracked_display_df["Status"] == status_filter]
        st.dataframe(style_payment_status_table(tracked_display_df), width='stretch', hide_index=True)

    st.markdown("### Unpaid Tracked Batches")
    unpaid1, unpaid2, unpaid3 = st.columns(3)
    unpaid1.metric("Unpaid Batches", int(payment_metrics.get("unpaid_batch_count", 0)))
    unpaid2.metric("Unpaid Frames", int(payment_metrics.get("unpaid_frames_total", 0)))
    unpaid3.metric("Unpaid Hours", float(payment_metrics.get("unpaid_hours_total", 0.0)))
    if unpaid_batches_df.empty:
        st.success("Every tracked batch currently has a matching synced payment batch, or payments have not been synced yet.")
    else:
        unpaid_display_df = unpaid_batches_df.rename(
            columns={
                "batch_name": "Batch",
                "payment_status": "Status",
                "task_count": "Tasks",
                "frames_logged": "Frames",
                "hours_spent": "Hours",
                "amount_usd": "Paid USD",
                "paid_entries": "Payment Entries",
                "last_tracked_at": "Last Tracked",
            }
        )
        st.dataframe(style_payment_status_table(unpaid_display_df), width='stretch', hide_index=True)

elif current_view == "quality":
    render_insights(insights, prediction, metrics)

elif current_view == "profile":
    profile1, profile2, profile3, profile4 = st.columns(4)
    profile1.metric("Tracked Tasks", task_count)
    profile2.metric("Tracked Sessions", session_count)
    profile3.metric("Configured $/Hour", money(rate_per_hour))
    profile4.metric("Configured $/Task", money(rate_per_task))
    config_df = pd.DataFrame(
        [{"Setting": key, "Value": stringify_value(value)} for key, value in config.items()]
    )
    st.dataframe(config_df, width='stretch', hide_index=True)

elif current_view == "audit":
    st.markdown("## Payment Audit & Reconciliation System")
    st.markdown("""
    This section monitors duplicate payment entries, logs all changes to the audit trail, 
    and manages reconciliation of conflicting payments.
    
    **Features:**
    - 🔍 Automated duplicate detection
    - 📝 Complete audit trail of all changes
    - 🚩 Flagging system for payments with updated values
    - ⚙️ Reconciliation workflow for duplicate resolution
    """)
    
    st.divider()
    
    try:
        import urllib.request
        import urllib.error

        api_base = "http://localhost:8000"

        def fetch_json(method: str, url: str, data=None):
            request = urllib.request.Request(url, method=method)
            if data is not None:
                request.add_header("Content-Type", "application/json")
                request.data = json.dumps(data).encode("utf-8")
            with urllib.request.urlopen(request, timeout=5) as response:
                return json.loads(response.read().decode("utf-8"))

        try:
            st.session_state.api_response_stats = fetch_json("GET", f"{api_base}/payments/audit-stats")
        except Exception:
            st.session_state.api_response_stats = None

        try:
            st.session_state.api_response_duplicates = fetch_json("POST", f"{api_base}/payments/detect-duplicates")
        except Exception:
            st.session_state.api_response_duplicates = None

        try:
            st.session_state.api_response_flagged = fetch_json("GET", f"{api_base}/payments/flagged")
        except Exception:
            st.session_state.api_response_flagged = None

        try:
            log_query = {
                "payment_type": None,
                "action": None,
                "is_duplicate_update": None,
                "limit": 100,
                "offset": 0,
            }
            st.session_state.api_response_audit = fetch_json(
                "POST", f"{api_base}/payments/audit-log", data=log_query
            )
        except Exception:
            st.session_state.api_response_audit = None

        display_audit_dashboard()
    except Exception as exc:
        st.error("Failed to load audit dashboard components")
        st.write(str(exc))

st.markdown(
"<div class='footer-note'>Runs fully local. Collects analytics only. Does not automate annotation.</div>",
unsafe_allow_html=True,
)
