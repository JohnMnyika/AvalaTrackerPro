from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

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


class PaymentFullSyncRequest(BaseModel):
    batches: List[PaymentBatchRequest] = Field(default_factory=list)
    history: List[PaymentHistoryRequest] = Field(default_factory=list)


class PaymentSyncResult(BaseModel):
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    total: int = 0


class PaymentFullSyncResponse(BaseModel):
    status: str
    detail: str
    batches: PaymentSyncResult
    history: PaymentSyncResult
    snapshot_hash: Optional[str] = None
    message: Optional[str] = None
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class BoundingBox(BaseModel):
    label: Optional[str] = None
    box: List[float]


class VisionAnalyzeRequest(BaseModel):
    image_base64: str
    existing_boxes: List[BoundingBox] = Field(default_factory=list)
    width: int
    height: int
    auto_detect: bool = False
    task_uid: Optional[str] = None
    frame_number: Optional[int] = None
    sensitivity: float = Field(0.5, ge=0.0, le=1.0)


class VisionSuggestion(BaseModel):
    label: str
    confidence: float
    detected_box: List[float]
    existing_box: Optional[List[float]] = None
    iou: float
    area_improvement: float


class VisionAnalyzeResponse(BaseModel):
    detected_boxes: List[Dict[str, Any]] = Field(default_factory=list)
    suggestions: List[VisionSuggestion] = Field(default_factory=list)
    suggestions_count: int
    time_saved_estimate_seconds: float
    processed_at: str


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


# ============= AUDIT SYSTEM SCHEMAS =============


class PaymentAuditLogEntry(BaseModel):
    id: int
    payment_type: str
    payment_id: Optional[int]
    action: str
    old_values: Optional[str]
    new_values: Optional[str]
    change_summary: Optional[str]
    audit_timestamp: datetime
    audit_user: str
    audit_source: Optional[str]
    is_duplicate_update: int

    class Config:
        from_attributes = True


class PaymentDuplicateInfo(BaseModel):
    id: int
    primary_payment_type: str
    primary_payment_id: int
    duplicate_payment_type: str
    duplicate_payment_id: int
    match_key: str
    similarity_score: float
    has_value_difference: int
    value_difference_summary: Optional[str]
    reconciliation_status: str
    detected_at: datetime
    reconciled_at: Optional[datetime]
    reconciliation_notes: Optional[str]

    class Config:
        from_attributes = True


class DuplicateDetectionResult(BaseModel):
    total_duplicates: int
    pending_review: int
    merged: int
    ignored: int
    duplicates: List[PaymentDuplicateInfo]


class AuditLogQuery(BaseModel):
    payment_type: Optional[str] = None  # 'batch', 'history', or None for all
    action: Optional[str] = None  # 'created', 'updated', 'flagged', etc.
    is_duplicate_update: Optional[int] = None
    limit: int = Field(100, ge=1, le=1000)
    offset: int = Field(0, ge=0)


class ReconciliationAction(BaseModel):
    duplicate_id: int
    action_type: str = Field(..., pattern="^(kept_primary|use_higher_value|manual_merge|ignore)$")
    action_notes: Optional[str] = None
    final_value_used: Optional[str] = None  # JSON
