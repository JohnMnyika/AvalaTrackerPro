from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from .database import Base


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    task_uid = Column(String, unique=True, index=True, nullable=False)
    dataset = Column(String, nullable=False)
    camera_name = Column(String, nullable=True)
    frame_start = Column(Integer, nullable=False, default=0)
    frame_end = Column(Integer, nullable=False, default=0)
    total_frames = Column(Integer, nullable=False, default=0)
    expected_hours = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    sessions = relationship("Session", back_populates="task", cascade="all, delete-orphan")
    frame_logs = relationship("FrameLog", back_populates="task", cascade="all, delete-orphan")


class Session(Base):
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=False, index=True)
    start_time = Column(DateTime, default=datetime.utcnow, nullable=False)
    end_time = Column(DateTime, nullable=True)
    active_minutes = Column(Float, default=0.0, nullable=False)
    idle_minutes = Column(Float, default=0.0, nullable=False)
    frames_completed = Column(Integer, default=0, nullable=False)
    efficiency_score = Column(Float, default=0.0, nullable=False)
    last_update_time = Column(DateTime, default=datetime.utcnow, nullable=False)

    task = relationship("Task", back_populates="sessions")


class FrameLog(Base):
    __tablename__ = "frame_logs"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=False, index=True)
    frame_number = Column(Integer, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    annotations_created = Column(Integer, default=0, nullable=False)
    annotations_deleted = Column(Integer, default=0, nullable=False)

    task = relationship("Task", back_populates="frame_logs")
