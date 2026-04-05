#!/usr/bin/env python3
"""
Remove all unknown-dataset and unknown camera entries from the tracker database.
This script deletes tasks, sessions, and related records for unknown datasets and cameras.
"""

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.database import SessionLocal, Base, engine
from backend.models import Task, Session as WorkSession, FrameLog
from sqlalchemy import delete, or_


def cleanup_unknown_entries():
    """Remove all unknown-dataset and unknown camera entries from database."""
    db = SessionLocal()
    try:
        # Find all tasks with unknown-dataset or unknown camera
        unknown_tasks = db.query(Task).filter(
            or_(Task.dataset == "unknown-dataset", Task.camera_name == "unknown")
        ).all()
        print(f"Found {len(unknown_tasks)} unknown dataset/camera tasks")

        if unknown_tasks:
            task_ids = [t.id for t in unknown_tasks]

            # Count related records
            frame_logs = db.query(FrameLog).filter(FrameLog.task_id.in_(task_ids)).count()
            sessions = db.query(WorkSession).filter(WorkSession.task_id.in_(task_ids)).count()

            print(f"  - {frame_logs} frame logs")
            print(f"  - {sessions} sessions")

            # Delete in correct order (foreign keys)
            print("\nDeleting records...")

            # Delete frame logs first
            db.query(FrameLog).filter(FrameLog.task_id.in_(task_ids)).delete(synchronize_session=False)
            print(f"✓ Deleted {frame_logs} frame logs")

            # Delete sessions
            db.query(WorkSession).filter(WorkSession.task_id.in_(task_ids)).delete(synchronize_session=False)
            print(f"✓ Deleted {sessions} sessions")

            # Delete tasks
            db.query(Task).filter(
                or_(Task.dataset == "unknown-dataset", Task.camera_name == "unknown")
            ).delete(synchronize_session=False)
            print(f"✓ Deleted {len(unknown_tasks)} tasks")

            db.commit()
            print("\n✓ Cleanup complete!")
        else:
            print("✓ No unknown dataset/camera entries found - database is clean!")

    except Exception as e:
        db.rollback()
        print(f"✗ Error during cleanup: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    print("AvalaTrackerPro - Unknown Dataset/Camera Cleanup\n")
    cleanup_unknown_entries()
