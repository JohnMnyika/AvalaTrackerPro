from __future__ import annotations

import re
import sqlite3
from datetime import datetime

DB_PATH = "data/avala.db"
SYN_RE = re.compile(r"^task-[0-9a-f]{5,}$")
GAP_SECONDS = 20 * 60


def parse_ts(ts: str) -> datetime:
    # SQLite default datetime text format used by SQLAlchemy.
    return datetime.fromisoformat(ts)


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys=ON")
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id, task_uid, dataset, COALESCE(camera_name, ''), created_at
        FROM tasks
        ORDER BY dataset, camera_name, created_at ASC, id ASC
        """
    )
    rows = cur.fetchall()

    # Group synthetic fallback tasks by dataset+camera and time clusters.
    groups: dict[tuple[str, str], list[tuple[int, str, str, str, str]]] = {}
    for r in rows:
        tid, uid, dataset, camera, created_at = r
        if not SYN_RE.match(uid or ""):
            continue
        key = (dataset or "unknown-dataset", camera or "")
        groups.setdefault(key, []).append(r)

    keep_to_merge: list[tuple[int, list[int]]] = []
    for _key, items in groups.items():
        cluster_keep = None
        cluster_last_ts = None
        cluster_dups: list[int] = []

        for tid, _uid, _dataset, _camera, created_at in items:
            ts = parse_ts(created_at)
            if cluster_keep is None:
                cluster_keep = tid
                cluster_last_ts = ts
                cluster_dups = []
                continue

            if (ts - cluster_last_ts).total_seconds() <= GAP_SECONDS:
                cluster_dups.append(tid)
                cluster_last_ts = ts
            else:
                if cluster_dups:
                    keep_to_merge.append((cluster_keep, cluster_dups))
                cluster_keep = tid
                cluster_last_ts = ts
                cluster_dups = []

        if cluster_keep is not None and cluster_dups:
            keep_to_merge.append((cluster_keep, cluster_dups))

    merged_tasks = 0
    for keep_id, dup_ids in keep_to_merge:
        q_marks = ",".join("?" for _ in dup_ids)
        cur.execute(f"UPDATE sessions SET task_id = ? WHERE task_id IN ({q_marks})", [keep_id, *dup_ids])
        cur.execute(f"UPDATE frame_logs SET task_id = ? WHERE task_id IN ({q_marks})", [keep_id, *dup_ids])
        cur.execute(f"DELETE FROM tasks WHERE id IN ({q_marks})", dup_ids)
        merged_tasks += len(dup_ids)

    conn.commit()

    cur.execute("SELECT COUNT(*) FROM tasks")
    tasks = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM sessions")
    sessions = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM frame_logs")
    frame_logs = cur.fetchone()[0]

    print(f"Merged duplicate synthetic tasks: {merged_tasks}")
    print(f"Current rows -> tasks: {tasks}, sessions: {sessions}, frame_logs: {frame_logs}")

    conn.close()


if __name__ == "__main__":
    main()
