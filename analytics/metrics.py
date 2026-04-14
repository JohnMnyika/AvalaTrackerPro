from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from backend.batch_matching import extract_batch_anchor, normalize_batch_name
from backend.models import ContributionDay, ExtensionHeartbeat, FrameLog, PaymentBatch, PaymentHistory, PaymentSyncDebug, VisionAnalysis
from backend.models import Session as WorkSession
from backend.models import Task

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.json"
logger = logging.getLogger(__name__)


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {"rate_per_hour": 3.0, "rate_per_task": 0.0}


def _tasks_df(db: Session) -> pd.DataFrame:
    tasks = db.query(Task).all()
    rows = [
        {
            "id": t.id,
            "task_uid": t.task_uid,
            "dataset": t.dataset,
            "normalized_batch_name": t.normalized_batch_name,
            "payment_status": t.payment_status,
            "paid_amount_usd": t.paid_amount_usd,
            "payment_updated_at": t.payment_updated_at,
            "camera_name": t.camera_name,
            "frame_start": t.frame_start,
            "frame_end": t.frame_end,
            "total_frames": t.total_frames,
            "expected_hours": t.expected_hours,
            "created_at": t.created_at,
        }
        for t in tasks
    ]
    return pd.DataFrame(rows)


def _sessions_df(db: Session) -> pd.DataFrame:
    sessions = db.query(WorkSession).all()
    rows = [
        {
            "id": s.id,
            "task_id": s.task_id,
            "start_time": s.start_time,
            "end_time": s.end_time,
            "active_minutes": s.active_minutes,
            "idle_minutes": s.idle_minutes,
            "frames_completed": s.frames_completed,
            "efficiency_score": s.efficiency_score,
        }
        for s in sessions
    ]
    return pd.DataFrame(rows)


def _payment_batches_df(db: Session) -> pd.DataFrame:
    rows = db.query(PaymentBatch).all()
    return pd.DataFrame(
        [
            {
                "batch_name": row.batch_name,
                "normalized_batch_name": row.normalized_batch_name,
                "amount_usd": row.amount_usd,
                "created_at": row.created_at,
                "updated_at": getattr(row, "updated_at", None),
            }
            for row in rows
        ]
    )


def _dedupe_payment_batches(payment_batches: pd.DataFrame) -> pd.DataFrame:
    if payment_batches.empty:
        return payment_batches
    deduped = payment_batches.copy()
    if "normalized_batch_name" not in deduped.columns:
        deduped["normalized_batch_name"] = deduped["batch_name"].map(normalize_batch_name)
    deduped["normalized_batch_name"] = deduped["normalized_batch_name"].fillna("").map(normalize_batch_name)
    deduped["updated_at_sort"] = pd.to_datetime(deduped["updated_at"], errors="coerce")
    deduped["created_at_sort"] = pd.to_datetime(deduped["created_at"], errors="coerce")
    deduped = deduped.sort_values(
        ["normalized_batch_name", "updated_at_sort", "created_at_sort", "amount_usd"],
        ascending=[True, False, False, False],
    )
    deduped = deduped.drop_duplicates(subset=["normalized_batch_name"], keep="first")
    return deduped.drop(columns=["updated_at_sort", "created_at_sort"], errors="ignore")


def _payment_history_df(db: Session) -> pd.DataFrame:
    rows = db.query(PaymentHistory).all()
    return pd.DataFrame(
        [
            {
                "date": row.date,
                "amount_usd": row.amount_usd,
                "amount_kes": row.amount_kes,
                "status": row.status,
            }
            for row in rows
        ]
    )


def _annotation_totals(db: Session, today_str: str) -> dict:
    contribution_rows = db.query(ContributionDay).all()
    if contribution_rows:
        created_total = sum(int(row.boxes_count or 0) for row in contribution_rows)
        created_today = sum(
            int(row.boxes_count or 0)
            for row in contribution_rows
            if row.contribution_date and row.contribution_date.isoformat() == today_str
        )
        return {
            "boxes_annotated_total": int(created_total),
            "boxes_deleted_total": 0,
            "boxes_annotated_today": int(created_today),
            "boxes_deleted_today": 0,
        }

    created_total = db.query(func.sum(FrameLog.annotations_created)).scalar() or 0
    deleted_total = db.query(func.sum(FrameLog.annotations_deleted)).scalar() or 0
    created_today = (
        db.query(func.sum(FrameLog.annotations_created))
        .filter(func.date(FrameLog.timestamp) == today_str)
        .scalar()
        or 0
    )
    deleted_today = (
        db.query(func.sum(FrameLog.annotations_deleted))
        .filter(func.date(FrameLog.timestamp) == today_str)
        .scalar()
        or 0
    )
    return {
        "boxes_annotated_total": int(created_total),
        "boxes_deleted_total": int(deleted_total),
        "boxes_annotated_today": int(created_today),
        "boxes_deleted_today": int(deleted_today),
    }


def _frame_counts(db: Session, today_str: str) -> dict:
    total_frames_logged = db.query(func.count(func.distinct(FrameLog.frame_number))).scalar() or 0
    frames_today = (
        db.query(func.count(func.distinct(FrameLog.frame_number)))
        .filter(func.date(FrameLog.timestamp) == today_str)
        .scalar()
        or 0
    )
    return {
        "frames_logged_total": int(total_frames_logged),
        "frames_logged_today": int(frames_today),
    }


def _database_schema_status(db: Session) -> dict:
    try:
        rows = db.execute(text("PRAGMA table_info(payments_batches)")).fetchall()
        columns = {row[1] for row in rows}
        if "updated_at" in columns and "normalized_batch_name" in columns:
            return {
                "schema_status": "ok",
                "schema_status_message": "Database schema is up to date.",
            }
        return {
            "schema_status": "needs_migration",
            "schema_status_message": "Missing normalized payment batch fields; restart the backend to migrate the schema.",
        }
    except Exception as exc:
        return {
            "schema_status": "unknown",
            "schema_status_message": f"Schema status check failed: {exc}",
        }


def compute_payment_metrics(db: Session) -> dict:
    tasks = _tasks_df(db)
    sessions = _sessions_df(db)
    payment_batches = _payment_batches_df(db)
    payment_batches = _dedupe_payment_batches(payment_batches)
    payment_history = _payment_history_df(db)

    extension_status = {}
    heartbeat_record = db.query(ExtensionHeartbeat).filter(ExtensionHeartbeat.client_key == "primary").first()
    if heartbeat_record is not None:
        last_seen_at = heartbeat_record.last_seen_at.isoformat() if heartbeat_record.last_seen_at else None
        extension_status = {
            "connected": bool(heartbeat_record.last_seen_at and (datetime.utcnow() - heartbeat_record.last_seen_at).total_seconds() <= 180),
            "page_type": heartbeat_record.page_type,
            "page_url": heartbeat_record.page_url,
            "source": heartbeat_record.source,
            "last_seen_at": last_seen_at,
        }

    payment_sync_debug = {}
    debug_record = db.query(PaymentSyncDebug).filter(PaymentSyncDebug.sync_key == "payments_dashboard").first()
    if debug_record is not None:
        payment_sync_debug = {
            "page_url": debug_record.page_url,
            "page_detected": bool(debug_record.page_detected),
            "recent_work_section_found": bool(debug_record.recent_work_section_found),
            "payment_history_section_found": bool(debug_record.payment_history_section_found),
            "recent_work_rows": int(debug_record.recent_work_rows or 0),
            "payment_history_rows": int(debug_record.payment_history_rows or 0),
            "last_status": debug_record.last_status,
            "last_error": debug_record.last_error,
            "backend_status_code": debug_record.backend_status_code,
            "page_fingerprint": debug_record.page_fingerprint,
            "last_attempt_at": debug_record.last_attempt_at.isoformat() if debug_record.last_attempt_at else None,
            "last_success_at": debug_record.last_success_at.isoformat() if debug_record.last_success_at else None,
        }

    vision_rows = db.query(VisionAnalysis).all()
    vision_df = pd.DataFrame(
        [
            {
                "task_uid": row.task_uid,
                "frame_number": row.frame_number,
                "suggestions_count": row.suggestions_count,
                "time_saved_estimate_seconds": row.time_saved_estimate_seconds,
            }
            for row in vision_rows
        ]
    )

    schema_status = _database_schema_status(db)
    if payment_batches.empty and payment_history.empty:
        suggestion_summary = {
            "vision_suggestions_total": int(vision_df["suggestions_count"].sum()) if not vision_df.empty else 0,
            "vision_frames_assisted": int(len(vision_df)) if not vision_df.empty else 0,
            "vision_time_saved_minutes": round(float(vision_df["time_saved_estimate_seconds"].sum()) / 60.0, 2) if not vision_df.empty else 0.0,
            "vision_suggestions_per_task": [] if vision_df.empty else vision_df.groupby("task_uid", as_index=False)["suggestions_count"].sum().rename(columns={"suggestions_count": "suggestions_total"}).to_dict(orient="records"),
        }
        return {
            "total_earnings_usd": 0.0,
            "total_paid_kes": 0.0,
            "earnings_per_day": [],
            "earnings_per_batch": [],
            "average_earning_per_task": 0.0,
            "earnings_per_hour": 0.0,
            "profitability_per_hour": [],
            "best_paying_datasets": [],
            "tracked_batches": [],
            "top_paying_batches": [],
            "usd_vs_kes": [],
            "recent_work_synced_count": 0,
            "payment_history_synced_count": 0,
            "last_recent_work_sync_at": None,
            "last_payment_history_date": None,
            "payment_sync_status": str(payment_sync_debug.get("last_status") or "waiting_for_sync"),
            "payment_sync_debug": payment_sync_debug,
            "extension_status": extension_status,
            "schema_status": schema_status.get("schema_status"),
            "schema_status_message": schema_status.get("schema_status_message"),
            "unpaid_batches": [],
            "unpaid_batch_count": 0,
            "unpaid_frames_total": 0,
            "unpaid_hours_total": 0.0,
            **suggestion_summary,
        }

    total_earnings = float(payment_batches["amount_usd"].sum()) if not payment_batches.empty else 0.0
    total_paid_kes = float(payment_history["amount_kes"].fillna(0).sum()) if not payment_history.empty else 0.0

    if not payment_history.empty:
        payment_history = payment_history.copy()
        payment_history["date"] = pd.to_datetime(payment_history["date"], errors="coerce")
        payment_history = payment_history.dropna(subset=["date"])
        if not payment_history.empty:
            payment_history["day"] = payment_history["date"].dt.strftime("%Y-%m-%d")
            earnings_per_day = (
                payment_history.groupby("day", as_index=False)
                .agg(amount_usd=("amount_usd", "sum"), amount_kes=("amount_kes", "sum"))
                .rename(columns={"day": "date"})
            )
            earnings_per_day_rows = earnings_per_day.to_dict(orient="records")
            usd_vs_kes = earnings_per_day_rows
        else:
            earnings_per_day_rows = []
            usd_vs_kes = []
    else:
        earnings_per_day_rows = []
        usd_vs_kes = []

    if not payment_batches.empty:
        if "normalized_batch_name" not in payment_batches.columns:
            payment_batches["normalized_batch_name"] = payment_batches["batch_name"].map(normalize_batch_name)
        earnings_per_batch_df = payment_batches.groupby("normalized_batch_name", as_index=False).agg(amount_usd=("amount_usd", "sum"))
        earnings_per_batch_df["batch_name"] = earnings_per_batch_df["normalized_batch_name"]
        earnings_per_batch = earnings_per_batch_df.sort_values("amount_usd", ascending=False).to_dict(orient="records")
    else:
        earnings_per_batch_df = pd.DataFrame(columns=["batch_name", "normalized_batch_name", "amount_usd"])
        earnings_per_batch = []

    total_tasks = int(len(tasks)) if not tasks.empty else 0
    total_hours = float(sessions["active_minutes"].fillna(0).sum()) / 60.0 if not sessions.empty else 0.0
    average_earning_per_task = round(total_earnings / total_tasks, 2) if total_tasks else 0.0
    earnings_per_hour = round(total_earnings / total_hours, 2) if total_hours > 0 else 0.0

    profitability_rows = []
    best_paying_datasets = []
    tracked_batches = []
    unpaid_batches = []
    unpaid_batch_count = 0
    unpaid_frames_total = 0
    unpaid_hours_total = 0.0

    task_summary_df = pd.DataFrame(columns=["dataset", "task_count", "frames", "hours", "last_tracked_at"])
    if not tasks.empty:
        task_summary_df = tasks.copy()
        task_summary_df["created_at"] = pd.to_datetime(task_summary_df["created_at"], errors="coerce")
        session_hours_df = pd.DataFrame(columns=["dataset", "hours"])
        if not sessions.empty:
            joined = sessions.merge(tasks[["id", "dataset"]], left_on="task_id", right_on="id", how="left")
            session_hours_df = joined.groupby("dataset", as_index=False).agg(hours=("active_minutes", lambda x: float(x.fillna(0).sum()) / 60.0))
        task_summary_df = task_summary_df.groupby("dataset", as_index=False).agg(
            task_count=("task_uid", "count"),
            frames=("total_frames", "sum"),
            last_tracked_at=("created_at", "max"),
        )
        task_summary_df = task_summary_df.merge(session_hours_df, on="dataset", how="left")
        task_summary_df["hours"] = task_summary_df["hours"].fillna(0.0)

    paid_batch_summary_df = pd.DataFrame(columns=["batch_name", "normalized_batch_name", "amount_usd", "paid_entries"])
    paid_batch_names = set()
    paid_batch_anchor_map: dict[str, list[str]] = {}
    if not payment_batches.empty:
        payment_batches["batch_name"] = payment_batches["batch_name"].astype(str)
        if "normalized_batch_name" not in payment_batches.columns:
            payment_batches["normalized_batch_name"] = payment_batches["batch_name"].map(normalize_batch_name)
        payment_batches["normalized_batch_name"] = payment_batches["normalized_batch_name"].fillna("").map(normalize_batch_name)
        paid_batch_summary_df = payment_batches.groupby(["batch_name", "normalized_batch_name"], as_index=False).agg(
            amount_usd=("amount_usd", "sum"),
            paid_entries=("amount_usd", "count"),
        )
        paid_batch_names = set(paid_batch_summary_df["normalized_batch_name"].tolist())
        for normalized_name in paid_batch_summary_df["normalized_batch_name"].tolist():
            anchor = extract_batch_anchor(normalized_name)
            if not anchor:
                continue
            paid_batch_anchor_map.setdefault(anchor, []).append(normalized_name)

    if not task_summary_df.empty:
        tracked_batches_df = task_summary_df.copy().rename(
            columns={"dataset": "batch_name", "frames": "frames_logged", "hours": "hours_spent"}
        )
        tracked_batches_df["normalized_batch_name"] = tracked_batches_df["batch_name"].map(normalize_batch_name)
        tracked_batches_df["hours_spent"] = tracked_batches_df["hours_spent"].fillna(0.0).round(2)
        tracked_batches_df = tracked_batches_df.merge(
            paid_batch_summary_df[["normalized_batch_name", "amount_usd", "paid_entries"]],
            on="normalized_batch_name",
            how="left",
        )
        tracked_batches_df["amount_usd"] = tracked_batches_df["amount_usd"].fillna(0.0).round(2)
        tracked_batches_df["paid_entries"] = tracked_batches_df["paid_entries"].fillna(0).astype(int)

        def resolve_paid_status(row: pd.Series) -> str:
            normalized_name = normalize_batch_name(row.get("normalized_batch_name"))
            if normalized_name in paid_batch_names:
                return "Paid"
            anchor = extract_batch_anchor(normalized_name)
            if anchor and len(set(paid_batch_anchor_map.get(anchor, []))) == 1:
                candidate_name = paid_batch_anchor_map[anchor][0]
                candidate_row = paid_batch_summary_df[paid_batch_summary_df["normalized_batch_name"] == candidate_name]
                if not candidate_row.empty:
                    tracked_batches_df.loc[row.name, "amount_usd"] = round(float(candidate_row.iloc[0]["amount_usd"]), 2)
                    tracked_batches_df.loc[row.name, "paid_entries"] = int(candidate_row.iloc[0]["paid_entries"])
                    logger.info("Matched %s -> %s", candidate_name, row.get("batch_name"))
                    return "Paid"
            return "Unpaid"

        tracked_batches_df["payment_status"] = tracked_batches_df.apply(resolve_paid_status, axis=1)
        tracked_batches_df["last_tracked_at"] = tracked_batches_df["last_tracked_at"].astype(str)
        tracked_batches_df = tracked_batches_df.sort_values(
            ["payment_status", "last_tracked_at", "frames_logged"],
            ascending=[True, False, False],
        )
        tracked_batches = tracked_batches_df.to_dict(orient="records")

        unpaid_df = tracked_batches_df[tracked_batches_df["payment_status"] == "Unpaid"].copy()
        if not unpaid_df.empty:
            unpaid_batches = unpaid_df.to_dict(orient="records")
            unpaid_batch_count = int(len(unpaid_df))
            unpaid_frames_total = int(unpaid_df["frames_logged"].fillna(0).sum())
            unpaid_hours_total = round(float(unpaid_df["hours_spent"].fillna(0).sum()), 2)

    if not tasks.empty and not payment_batches.empty:
        task_hours = {}
        if not sessions.empty:
            joined = sessions.merge(tasks[["id", "dataset"]], left_on="task_id", right_on="id", how="left")
            task_hours = joined.groupby("dataset", as_index=False).agg(hours=("active_minutes", lambda x: float(x.fillna(0).sum()) / 60.0))
        else:
            task_hours = pd.DataFrame(columns=["dataset", "hours"])
        task_hours["normalized_batch_name"] = task_hours["dataset"].map(normalize_batch_name)

        profitability_df = earnings_per_batch_df.merge(task_hours, on="normalized_batch_name", how="left")
        profitability_df["hours"] = profitability_df["hours"].fillna(0.0)
        profitability_df["profit_per_hour"] = profitability_df.apply(
            lambda row: round(float(row["amount_usd"]) / float(row["hours"]), 2) if float(row["hours"]) > 0 else 0.0,
            axis=1,
        )
        profitability_rows = profitability_df[["batch_name", "amount_usd", "hours", "profit_per_hour"]].sort_values(
            "amount_usd", ascending=False
        ).to_dict(orient="records")

        dataset_df = profitability_df.groupby("batch_name", as_index=False).agg(
            total_earnings_usd=("amount_usd", "sum"),
            total_hours=("hours", "sum"),
        )
        dataset_df["profit_per_hour"] = dataset_df.apply(
            lambda row: round(float(row["total_earnings_usd"]) / float(row["total_hours"]), 2) if float(row["total_hours"]) > 0 else 0.0,
            axis=1,
        )
        best_paying_datasets = dataset_df.sort_values("total_earnings_usd", ascending=False).rename(
            columns={"batch_name": "dataset"}
        ).to_dict(orient="records")

    top_paying_batches = earnings_per_batch[:5]
    recent_work_synced_count = int(len(payment_batches)) if not payment_batches.empty else 0
    payment_history_synced_count = int(len(payment_history)) if not payment_history.empty else 0
    last_recent_work_sync_at = None
    if not payment_batches.empty:
        created_at_series = pd.to_datetime(payment_batches["created_at"], errors="coerce").dropna() if "created_at" in payment_batches.columns else pd.Series(dtype="datetime64[ns]")
        updated_at_series = pd.to_datetime(payment_batches["updated_at"], errors="coerce").dropna() if "updated_at" in payment_batches.columns else pd.Series(dtype="datetime64[ns]")
        all_sync_times = pd.concat([created_at_series, updated_at_series], ignore_index=True)
        if not all_sync_times.empty:
            last_recent_work_sync_at = all_sync_times.max().isoformat()
    last_payment_history_date = None
    if not payment_history.empty and "date" in payment_history.columns:
        date_series = pd.to_datetime(payment_history["date"], errors="coerce").dropna()
        if not date_series.empty:
            last_payment_history_date = date_series.max().date().isoformat()
    extension_status = {}
    heartbeat_record = db.query(ExtensionHeartbeat).filter(ExtensionHeartbeat.client_key == "primary").first()
    if heartbeat_record is not None:
        last_seen_at = heartbeat_record.last_seen_at.isoformat() if heartbeat_record.last_seen_at else None
        extension_status = {
            "connected": bool(heartbeat_record.last_seen_at and (datetime.utcnow() - heartbeat_record.last_seen_at).total_seconds() <= 180),
            "page_type": heartbeat_record.page_type,
            "page_url": heartbeat_record.page_url,
            "source": heartbeat_record.source,
            "last_seen_at": last_seen_at,
        }

    payment_sync_debug = {}
    debug_record = db.query(PaymentSyncDebug).filter(PaymentSyncDebug.sync_key == "payments_dashboard").first()
    if debug_record is not None:
        payment_sync_debug = {
            "page_url": debug_record.page_url,
            "page_detected": bool(debug_record.page_detected),
            "recent_work_section_found": bool(debug_record.recent_work_section_found),
            "payment_history_section_found": bool(debug_record.payment_history_section_found),
            "recent_work_rows": int(debug_record.recent_work_rows or 0),
            "payment_history_rows": int(debug_record.payment_history_rows or 0),
            "last_status": debug_record.last_status,
            "last_error": debug_record.last_error,
            "backend_status_code": debug_record.backend_status_code,
            "page_fingerprint": debug_record.page_fingerprint,
            "last_attempt_at": debug_record.last_attempt_at.isoformat() if debug_record.last_attempt_at else None,
            "last_success_at": debug_record.last_success_at.isoformat() if debug_record.last_success_at else None,
        }

    suggestion_summary = {
        "vision_suggestions_total": int(vision_df["suggestions_count"].sum()) if not vision_df.empty else 0,
        "vision_frames_assisted": int(len(vision_df)) if not vision_df.empty else 0,
        "vision_time_saved_minutes": round(float(vision_df["time_saved_estimate_seconds"].sum()) / 60.0, 2) if not vision_df.empty else 0.0,
        "vision_suggestions_per_task": [] if vision_df.empty else vision_df.groupby("task_uid", as_index=False)["suggestions_count"].sum().rename(columns={"suggestions_count": "suggestions_total"}).to_dict(orient="records"),
    }

    payment_sync_status = "synced" if (recent_work_synced_count or payment_history_synced_count) else "waiting_for_sync"
    if payment_sync_debug and payment_sync_status == "waiting_for_sync":
        payment_sync_status = str(payment_sync_debug.get("last_status") or payment_sync_status)
    if last_recent_work_sync_at:
        try:
            last_sync_dt = datetime.fromisoformat(last_recent_work_sync_at)
            if last_sync_dt.tzinfo is None:
                last_sync_dt = last_sync_dt.replace(tzinfo=timezone.utc)
            now_utc = datetime.now(timezone.utc)
            if (now_utc - last_sync_dt).total_seconds() > 48 * 3600:
                payment_sync_status = "stale"
        except ValueError:
            payment_sync_status = payment_sync_status

    schema_status = _database_schema_status(db)
    return {
        "total_earnings_usd": round(total_earnings, 2),
        "total_paid_kes": round(total_paid_kes, 2),
        "earnings_per_day": earnings_per_day_rows,
        "earnings_per_batch": earnings_per_batch,
        "average_earning_per_task": average_earning_per_task,
        "earnings_per_hour": earnings_per_hour,
        "profitability_per_hour": profitability_rows,
        "best_paying_datasets": best_paying_datasets,
        "tracked_batches": tracked_batches,
        "top_paying_batches": top_paying_batches,
        "usd_vs_kes": usd_vs_kes,
        "recent_work_synced_count": recent_work_synced_count,
        "payment_history_synced_count": payment_history_synced_count,
        "last_recent_work_sync_at": last_recent_work_sync_at,
        "last_payment_history_date": last_payment_history_date,
        "payment_sync_status": payment_sync_status,
        "payment_sync_debug": payment_sync_debug,
        "extension_status": extension_status,
        "schema_status": schema_status.get("schema_status"),
        "schema_status_message": schema_status.get("schema_status_message"),
        "unpaid_batches": unpaid_batches,
        "unpaid_batch_count": unpaid_batch_count,
        "unpaid_frames_total": unpaid_frames_total,
        "unpaid_hours_total": unpaid_hours_total,
        **suggestion_summary,
    }


def compute_core_metrics(db: Session) -> dict:
    cfg = _load_config()
    payment_metrics = compute_payment_metrics(db)

    today = date.today()
    today_str = str(today)
    annotation_stats = _annotation_totals(db, today_str)
    frame_stats = _frame_counts(db, today_str)

    total_tasks = int(db.query(func.count(Task.id)).scalar() or 0)
    if total_tasks == 0:
        return {
            "frames_per_hour": 0.0,
            "tasks_completed": 0,
            "daily_hours_worked": 0.0,
            "efficiency_ratio": 0.0,
            "dataset_distribution": {},
            "camera_distribution": {},
            "frames_annotated_today": 0,
            "tasks_completed_today": 0,
            "hours_worked_today": 0.0,
            "earnings": {"daily": 0.0, "weekly": 0.0, "monthly_projection": 0.0},
            "payments": payment_metrics,
            **annotation_stats,
        }

    tasks_today = int(
        db.query(func.count(Task.id))
        .filter(func.date(Task.created_at) == today_str)
        .scalar()
        or 0
    )
    total_task_frames = float(db.query(func.sum(Task.total_frames)).scalar() or 0)
    today_task_frames = float(
        db.query(func.sum(Task.total_frames))
        .filter(func.date(Task.created_at) == today_str)
        .scalar()
        or 0
    )

    total_active_hours = float(db.query(func.sum(WorkSession.active_minutes)).scalar() or 0.0) / 60.0
    efficiency_ratio = float(db.query(func.avg(WorkSession.efficiency_score)).scalar() or 0.0)
    hours_today = float(
        db.query(func.sum(WorkSession.active_minutes))
        .join(Task, Task.id == WorkSession.task_id)
        .filter(func.date(Task.created_at) == today_str)
        .scalar()
        or 0.0
    ) / 60.0

    dataset_distribution_rows = (
        db.query(Task.dataset, func.count(Task.id))
        .group_by(Task.dataset)
        .all()
    )
    camera_distribution_rows = (
        db.query(Task.camera_name, func.count(Task.id))
        .group_by(Task.camera_name)
        .all()
    )
    dataset_distribution = {
        str(dataset or "unknown"): int(count)
        for dataset, count in dataset_distribution_rows
    }
    camera_distribution = {
        str(camera or "unknown"): int(count)
        for camera, count in camera_distribution_rows
    }

    total_frames = frame_stats["frames_logged_total"] or int(total_task_frames)
    frames_today = frame_stats["frames_logged_today"] or int(today_task_frames)

    frames_per_hour = round(total_frames / max(total_active_hours, 1e-6), 2)

    rate_per_hour = float(cfg.get("rate_per_hour", 3.0))
    rate_per_task = float(cfg.get("rate_per_task", 0.0))
    daily_earnings = (hours_today * rate_per_hour) + (tasks_today * rate_per_task)

    return {
        "frames_per_hour": frames_per_hour,
        "tasks_completed": total_tasks,
        "daily_hours_worked": round(hours_today, 2),
        "efficiency_ratio": round(efficiency_ratio, 3),
        "dataset_distribution": dataset_distribution,
        "camera_distribution": camera_distribution,
        "frames_annotated_today": int(frames_today),
        "tasks_completed_today": tasks_today,
        "hours_worked_today": round(hours_today, 2),
        "earnings": {
            "daily": round(daily_earnings, 2),
            "weekly": round(daily_earnings * 5, 2),
            "monthly_projection": round(daily_earnings * 22, 2),
        },
        "payments": payment_metrics,
        **annotation_stats,
    }
