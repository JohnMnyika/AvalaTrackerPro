from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

from backend.batch_matching import normalize_batch_name

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "avala.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def ensure_schema() -> None:
    logger.info("Ensuring database schema for Avala Tracker Pro")
    try:
        Base.metadata.create_all(bind=engine)

        index_statements = [
            "CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at)",
            "CREATE INDEX IF NOT EXISTS idx_tasks_dataset_created_at ON tasks(dataset, created_at)",
            "CREATE INDEX IF NOT EXISTS idx_tasks_camera_created_at ON tasks(camera_name, created_at)",
            "CREATE INDEX IF NOT EXISTS idx_tasks_normalized_batch_name ON tasks(normalized_batch_name)",
            "CREATE INDEX IF NOT EXISTS idx_sessions_start_time ON sessions(start_time)",
            "CREATE INDEX IF NOT EXISTS idx_sessions_task_start ON sessions(task_id, start_time)",
            "CREATE INDEX IF NOT EXISTS idx_frame_logs_task_timestamp ON frame_logs(task_id, timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_frame_logs_timestamp_frame ON frame_logs(timestamp, frame_number)",
            "CREATE INDEX IF NOT EXISTS idx_payments_history_status_date ON payments_history(status, date)",
            "CREATE INDEX IF NOT EXISTS idx_payments_batches_created_at ON payments_batches(created_at)",
            "CREATE INDEX IF NOT EXISTS idx_payments_batches_updated_at ON payments_batches(updated_at)",
            "CREATE INDEX IF NOT EXISTS idx_payments_batches_last_seen_at ON payments_batches(last_seen_at)",
            "CREATE INDEX IF NOT EXISTS idx_payments_batches_normalized_batch_name ON payments_batches(normalized_batch_name)",
            "CREATE INDEX IF NOT EXISTS idx_payments_history_last_seen_at ON payments_history(last_seen_at)",
        ]

        column_migrations = {
            "tasks": {
                "normalized_batch_name": "ALTER TABLE tasks ADD COLUMN normalized_batch_name STRING",
                "payment_status": "ALTER TABLE tasks ADD COLUMN payment_status STRING DEFAULT 'unpaid'",
                "paid_amount_usd": "ALTER TABLE tasks ADD COLUMN paid_amount_usd FLOAT DEFAULT 0.0",
                "payment_updated_at": "ALTER TABLE tasks ADD COLUMN payment_updated_at DATETIME",
            },
            "payments_sync_debug": {
                "page_fingerprint": "ALTER TABLE payments_sync_debug ADD COLUMN page_fingerprint STRING",
                "backend_status_code": "ALTER TABLE payments_sync_debug ADD COLUMN backend_status_code INTEGER",
                "last_attempt_at": "ALTER TABLE payments_sync_debug ADD COLUMN last_attempt_at DATETIME",
                "last_success_at": "ALTER TABLE payments_sync_debug ADD COLUMN last_success_at DATETIME",
            },
            "extension_heartbeats": {
                "page_type": "ALTER TABLE extension_heartbeats ADD COLUMN page_type STRING DEFAULT 'unknown'",
                "source": "ALTER TABLE extension_heartbeats ADD COLUMN source STRING DEFAULT 'content_script'",
                "last_seen_at": "ALTER TABLE extension_heartbeats ADD COLUMN last_seen_at DATETIME",
            },
            "payments_batches": {
                "normalized_batch_name": "ALTER TABLE payments_batches ADD COLUMN normalized_batch_name STRING",
                "is_flagged": "ALTER TABLE payments_batches ADD COLUMN is_flagged INTEGER DEFAULT 0",
                "flag_reason": "ALTER TABLE payments_batches ADD COLUMN flag_reason STRING",
                "flagged_at": "ALTER TABLE payments_batches ADD COLUMN flagged_at DATETIME",
                "updated_at": "ALTER TABLE payments_batches ADD COLUMN updated_at DATETIME",
                "last_seen_at": "ALTER TABLE payments_batches ADD COLUMN last_seen_at DATETIME",
                "is_updated": "ALTER TABLE payments_batches ADD COLUMN is_updated INTEGER DEFAULT 0",
            },
            "payments_history": {
                "is_flagged": "ALTER TABLE payments_history ADD COLUMN is_flagged INTEGER DEFAULT 0",
                "flag_reason": "ALTER TABLE payments_history ADD COLUMN flag_reason STRING",
                "flagged_at": "ALTER TABLE payments_history ADD COLUMN flagged_at DATETIME",
                "created_at": "ALTER TABLE payments_history ADD COLUMN created_at DATETIME",
                "updated_at": "ALTER TABLE payments_history ADD COLUMN updated_at DATETIME",
                "last_seen_at": "ALTER TABLE payments_history ADD COLUMN last_seen_at DATETIME",
                "is_updated": "ALTER TABLE payments_history ADD COLUMN is_updated INTEGER DEFAULT 0",
            },
        }

        with engine.begin() as conn:
            for table_name, migrations in column_migrations.items():
                existing_columns = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()}
                for column_name, statement in migrations.items():
                    if column_name not in existing_columns:
                        logger.info("Applying migration: %s on %s", column_name, table_name)
                        conn.execute(text(statement))
                        if table_name == "payments_batches" and column_name == "updated_at":
                            conn.execute(text("UPDATE payments_batches SET updated_at = created_at WHERE updated_at IS NULL"))
                        if table_name == "payments_batches" and column_name == "last_seen_at":
                            conn.execute(text("UPDATE payments_batches SET last_seen_at = COALESCE(updated_at, created_at) WHERE last_seen_at IS NULL"))
                        if table_name == "payments_history" and column_name == "created_at":
                            conn.execute(text("UPDATE payments_history SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL"))
                        if table_name == "payments_history" and column_name == "updated_at":
                            conn.execute(text("UPDATE payments_history SET updated_at = COALESCE(created_at, CURRENT_TIMESTAMP) WHERE updated_at IS NULL"))
                        if table_name == "payments_history" and column_name == "last_seen_at":
                            conn.execute(text("UPDATE payments_history SET last_seen_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP) WHERE last_seen_at IS NULL"))
            conn.execute(text("UPDATE payments_batches SET last_seen_at = COALESCE(last_seen_at, updated_at, created_at)"))
            conn.execute(text("UPDATE payments_history SET created_at = COALESCE(created_at, CURRENT_TIMESTAMP)"))
            conn.execute(text("UPDATE payments_history SET updated_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP)"))
            conn.execute(text("UPDATE payments_history SET last_seen_at = COALESCE(last_seen_at, updated_at, created_at, CURRENT_TIMESTAMP)"))
            task_rows = conn.execute(text("SELECT id, dataset FROM tasks")).fetchall()
            for task_id, dataset in task_rows:
                conn.execute(
                    text(
                        "UPDATE tasks SET normalized_batch_name = :normalized_batch_name, "
                        "payment_status = COALESCE(payment_status, 'unpaid'), "
                        "paid_amount_usd = COALESCE(paid_amount_usd, 0.0) "
                        "WHERE id = :task_id"
                    ),
                    {"task_id": task_id, "normalized_batch_name": normalize_batch_name(dataset)},
                )
            payment_batch_rows = conn.execute(text("SELECT id, batch_name FROM payments_batches")).fetchall()
            for batch_id, batch_name in payment_batch_rows:
                normalized_batch_name = normalize_batch_name(batch_name)
                conn.execute(
                    text(
                        "UPDATE payments_batches SET "
                        "batch_name = :canonical_batch_name, "
                        "normalized_batch_name = :normalized_batch_name "
                        "WHERE id = :batch_id"
                    ),
                    {
                        "batch_id": batch_id,
                        "canonical_batch_name": normalized_batch_name or batch_name,
                        "normalized_batch_name": normalized_batch_name,
                    },
                )
            for statement in index_statements:
                conn.execute(text(statement))

        logger.info("Database schema ensured successfully")
        print("Database schema ensured successfully")
    except Exception:
        logger.exception("Database schema migration failed")
        print("Database schema migration failed")
        raise


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
