#!/usr/bin/env python3
"""
Normalize existing camera names in the database by removing (CAM XX) patterns.
"""

import sys
import re
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.database import SessionLocal, Base, engine
from backend.models import Task

CAMERA_NORMALIZE_RE = re.compile(r'\s*\(CAM\s*\d+\)\s*$', re.IGNORECASE)

def normalize_camera_name(camera_raw: str | None) -> str | None:
    """Normalize camera names by removing (CAM XX) patterns."""
    if not camera_raw:
        return camera_raw
    return CAMERA_NORMALIZE_RE.sub('', camera_raw.strip())

def normalize_existing_cameras():
    """Update all existing camera names to remove (CAM XX) patterns."""
    db = SessionLocal()
    try:
        # Find all tasks with camera names containing (CAM XX)
        tasks_to_update = db.query(Task).filter(
            Task.camera_name.isnot(None),
            Task.camera_name.op('REGEXP')('\(CAM\s*\d+\)')
        ).all()

        print(f"Found {len(tasks_to_update)} tasks with camera names to normalize")

        updated_count = 0
        for task in tasks_to_update:
            original = task.camera_name
            normalized = normalize_camera_name(original)
            if normalized != original:
                print(f"  '{original}' -> '{normalized}'")
                task.camera_name = normalized
                updated_count += 1

        if updated_count > 0:
            db.commit()
            print(f"\n✓ Updated {updated_count} camera names")
        else:
            print("✓ No camera names needed updating")

    except Exception as e:
        db.rollback()
        print(f"✗ Error during normalization: {e}")
        raise
    finally:
        db.close()

if __name__ == "__main__":
    print("AvalaTrackerPro - Camera Name Normalization\n")
    normalize_existing_cameras()