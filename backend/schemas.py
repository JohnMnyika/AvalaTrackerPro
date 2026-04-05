from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional

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


class ContributionDayPayload(BaseModel):
    contribution_date: datetime
    boxes_count: int = 0
    source: str = "profile"


class ContributionSyncRequest(BaseModel):
    days: List[ContributionDayPayload] = Field(default_factory=list)


class PaymentBatchRequest(BaseModel):
    batch_name: str = Field(..., min_length=3)
    amount_usd: float = 0.0
    source: str = "recent_work"
    timestamp: Optional[str] = None


class PaymentHistoryRequest(BaseModel):
    date: date
    amount_usd: float = 0.0
    amount_kes: Optional[float] = 0.0
    status: str = "completed"


class PaymentSyncRequest(BaseModel):
    recent_work: List[PaymentBatchRequest] = Field(default_factory=list)
    payment_history: List[PaymentHistoryRequest] = Field(default_factory=list)


class PaymentSyncDebugRequest(BaseModel):
    sync_key: str = "payments_dashboard"
    page_url: Optional[str] = None
    page_detected: bool = False
    recent_work_section_found: bool = False
    payment_history_section_found: bool = False
    recent_work_rows: int = 0
    payment_history_rows: int = 0
    last_status: str = "waiting_for_sync"
    last_error: Optional[str] = None
    backend_status_code: Optional[int] = None
    page_fingerprint: Optional[str] = None


class ExtensionHeartbeatRequest(BaseModel):
    client_key: str = "primary"
    page_url: Optional[str] = None
    page_type: str = "unknown"
    source: str = "content_script"
