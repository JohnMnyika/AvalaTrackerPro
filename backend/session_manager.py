from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from .database import SessionLocal
from .models import Session as WorkSession
from .models import Task
from tracker.activity_monitor import ActivityMonitor
from tracker.idle_detector import IdleDetector


class SessionManager:
    def __init__(self, idle_threshold_seconds: int = 300):
        self._lock = threading.Lock()
        self._active_task_uid: Optional[str] = None
        self.activity_monitor = ActivityMonitor()
        self.idle_detector = IdleDetector(
            activity_provider=self.activity_monitor.last_activity_seconds,
            idle_threshold_seconds=idle_threshold_seconds,
        )
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self.activity_monitor.start()
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        self.activity_monitor.stop()

    def mark_activity(self) -> None:
        self.activity_monitor.mark_activity()

    def start_session(self, db: Session, task_uid: str) -> WorkSession:
        with self._lock:
            task = db.query(Task).filter(Task.task_uid == task_uid).first()
            if task is None:
                raise ValueError(f"Task {task_uid} not found")

            open_session = (
                db.query(WorkSession)
                .filter(WorkSession.task_id == task.id, WorkSession.end_time.is_(None))
                .first()
            )
            if open_session:
                self._active_task_uid = task_uid
                return open_session

            now = datetime.utcnow()
            work_session = WorkSession(
                task_id=task.id,
                start_time=now,
                last_update_time=now,
            )
            db.add(work_session)
            db.commit()
            db.refresh(work_session)
            self._active_task_uid = task_uid
            return work_session

    def end_session(self, db: Session, task_uid: str) -> Optional[WorkSession]:
        with self._lock:
            task = db.query(Task).filter(Task.task_uid == task_uid).first()
            if task is None:
                return None

            session = (
                db.query(WorkSession)
                .filter(WorkSession.task_id == task.id, WorkSession.end_time.is_(None))
                .first()
            )
            if session is None:
                return None

            self._update_session_stats(session)
            session.end_time = datetime.utcnow()
            session.efficiency_score = self._calculate_efficiency(session, task.expected_hours)
            db.commit()
            db.refresh(session)
            if self._active_task_uid == task_uid:
                self._active_task_uid = None
            return session

    def refresh_open_sessions(self) -> None:
        with SessionLocal() as db:
            open_sessions = db.query(WorkSession).filter(WorkSession.end_time.is_(None)).all()
            for session in open_sessions:
                self._update_session_stats(session)
                task = db.query(Task).filter(Task.id == session.task_id).first()
                if task:
                    session.efficiency_score = self._calculate_efficiency(
                        session, task.expected_hours
                    )
            db.commit()

    def _loop(self) -> None:
        while self._running:
            try:
                self.refresh_open_sessions()
            except Exception:
                pass
            time.sleep(30)

    def _update_session_stats(self, session: WorkSession) -> None:
        now = datetime.utcnow()
        elapsed_minutes = max(
            0.0, (now - session.last_update_time).total_seconds() / 60.0
        )
        if elapsed_minutes <= 0:
            return

        if self.idle_detector.is_idle():
            session.idle_minutes += elapsed_minutes
        else:
            session.active_minutes += elapsed_minutes

        session.last_update_time = now

    @staticmethod
    def _calculate_efficiency(session: WorkSession, expected_hours: Optional[float]) -> float:
        actual_hours = max((session.active_minutes + session.idle_minutes) / 60.0, 1e-6)
        if expected_hours is None or expected_hours <= 0:
            return 0.0
        return round(expected_hours / actual_hours, 3)
