from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

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
    Base.metadata.create_all(bind=engine)
    index_statements = [
        "CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_tasks_dataset_created_at ON tasks(dataset, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_tasks_camera_created_at ON tasks(camera_name, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_sessions_start_time ON sessions(start_time)",
        "CREATE INDEX IF NOT EXISTS idx_sessions_task_start ON sessions(task_id, start_time)",
        "CREATE INDEX IF NOT EXISTS idx_frame_logs_task_timestamp ON frame_logs(task_id, timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_frame_logs_timestamp_frame ON frame_logs(timestamp, frame_number)",
        "CREATE INDEX IF NOT EXISTS idx_payments_history_status_date ON payments_history(status, date)",
        "CREATE INDEX IF NOT EXISTS idx_payments_batches_created_at ON payments_batches(created_at)",
    ]
    column_migrations = {
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
            "is_flagged": "ALTER TABLE payments_batches ADD COLUMN is_flagged INTEGER DEFAULT 0",
            "flag_reason": "ALTER TABLE payments_batches ADD COLUMN flag_reason STRING",
            "flagged_at": "ALTER TABLE payments_batches ADD COLUMN flagged_at DATETIME",
        },
        "payments_history": {
            "is_flagged": "ALTER TABLE payments_history ADD COLUMN is_flagged INTEGER DEFAULT 0",
            "flag_reason": "ALTER TABLE payments_history ADD COLUMN flag_reason STRING",
            "flagged_at": "ALTER TABLE payments_history ADD COLUMN flagged_at DATETIME",
        },
    }
    with engine.begin() as conn:
        for table_name, migrations in column_migrations.items():
            existing_columns = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()}
            for column_name, statement in migrations.items():
                if column_name not in existing_columns:
                    conn.execute(text(statement))
        for statement in index_statements:
            conn.execute(text(statement))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
