from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class TaskStartRequest(BaseModel):
    task_uid: str = Field(..., min_length=4)
    dataset: str
    sequence_id: Optional[str] = None
    camera: Optional[str] = None
    frame_start: int = 0
    frame_end: int = 0
    total_frames: int = 0
    expected_hours: Optional[float] = None


class TaskUpdateRequest(BaseModel):
    task_uid: str = Field(..., min_length=4)
    dataset: Optional[str] = None
    sequence_id: Optional[str] = None
    camera: Optional[str] = None
    frame_start: Optional[int] = None
    frame_end: Optional[int] = None
    total_frames: Optional[int] = None
    expected_hours: Optional[float] = None


class TaskEndRequest(BaseModel):
    task_uid: str


class FrameLogRequest(BaseModel):
    task_uid: str
    frame_number: int
    annotations_created: int = 0
    annotations_deleted: int = 0


class ActivityPingRequest(BaseModel):
    source: str = "extension"
    active: bool = True


class SessionResponse(BaseModel):
    session_id: int
    task_uid: str
    start_time: datetime
    end_time: Optional[datetime] = None
    active_minutes: float
    idle_minutes: float
    frames_completed: int
    efficiency_score: float


class GenericResponse(BaseModel):
    status: str
    detail: str
