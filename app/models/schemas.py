"""Pydantic request/response models. Grows per phase."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(UTC)


class HealthResponse(BaseModel):
    ok: bool
    service: str
    version: str
    ts: datetime
    dev_mode: bool


class ErrorResponse(BaseModel):
    ok: Literal[False] = False
    error_code: str
    detail: str = ""
    ts: datetime = Field(default_factory=utcnow)


# ----- Connection -----

class ConnectRequest(BaseModel):
    mode: Literal["usb", "lan", "lan_direct"]
    # Optional per-request overrides. When omitted, .env values are used.
    # For `lan_direct` every override is ignored — the service auto-detects
    # the printer on any active wired interface.
    lan_host: str | None = Field(default=None, max_length=255)
    lan_port: int | None = Field(default=None, ge=1, le=65535)
    usb_vendor_id: str | None = Field(default=None, max_length=10)
    usb_product_id: str | None = Field(default=None, max_length=10)
    usb_device_path: str | None = Field(default=None, max_length=255)


class ConnectionInfo(BaseModel):
    mode: Literal["usb", "lan"] | None
    connected: bool
    state: str
    target_mode: Literal["usb", "lan"] | None
    disconnect_reason: str | None
    last_seen_ts: datetime | None


class DeviceInfo(BaseModel):
    paper: str
    cover: str
    temperature_c: float | None
    overheated: bool
    jammed: bool


class LastJobInfo(BaseModel):
    job_id: str | None
    op: str | None
    status: str | None
    error_code: str | None
    ts: datetime | None


class ActivityInfo(BaseModel):
    busy: bool
    current_job_id: str | None = None
    pending_count: int = 0


class ReconnectInfo(BaseModel):
    """Auto-reconnect state — populated when the reconcile loop is retrying."""
    failure_count: int
    breaker_threshold: int
    breaker_open: bool
    next_retry_in_ms: int | None
    last_attempt_ts: datetime | None
    last_attempt_error: str | None


class StatusResponse(BaseModel):
    ok: bool
    ts: datetime = Field(default_factory=utcnow)
    printer_state: str
    connection: ConnectionInfo
    device: DeviceInfo
    last_job: LastJobInfo
    activity: ActivityInfo
    reconnect: ReconnectInfo | None = None
    prediction: "PredictionInfo | None" = None
    eta: "EtaInfo | None" = None


class ConnectResponse(BaseModel):
    ok: bool
    mode: Literal["usb", "lan"]
    state: str
    ts: datetime = Field(default_factory=utcnow)


class DisconnectResponse(BaseModel):
    ok: bool
    state: str
    ts: datetime = Field(default_factory=utcnow)


# ----- Print / Reprint -----

class CategoryItem(BaseModel):
    product: str = Field(..., min_length=1, max_length=40)
    quantity: int = Field(..., ge=0)
    reward: float = Field(..., ge=0)


class PrintTextRequest(BaseModel):
    machine_id: str = Field(..., min_length=1, max_length=64)
    items: list[CategoryItem] = Field(default_factory=list, max_length=20)
    total_reward: float = Field(default=0.0, ge=0)
    timestamp: datetime | None = None
    qr_content: str | None = Field(default=None, max_length=500)
    lang: Literal["tr", "en"] = "tr"
    idempotency_key: str | None = Field(default=None, max_length=128)
    title: str | None = Field(default=None, max_length=80)


class PrintResponse(BaseModel):
    ok: bool
    job_id: str
    status: str
    error_code: str | None = None
    idempotent_replay: bool = False
    duration_ms: int | None = None
    ts: datetime = Field(default_factory=utcnow)


class ReprintRequest(BaseModel):
    job_id: str = Field(..., min_length=1, max_length=64)
    idempotency_key: str | None = Field(default=None, max_length=128)


class PrintImageRequest(PrintTextRequest):
    image_base64: str = Field(..., min_length=1,
                              description="Base64-encoded PNG/JPEG/BMP/GIF, max MAX_IMAGE_BYTES")


class PreviewResponse(BaseModel):
    preview: str
    bytes_len: int


# ----- Prediction / ETA -----

class PredictionInfo(BaseModel):
    paper_consumed_mm: float
    roll_length_mm: int
    remaining_mm: float
    avg_receipt_mm: float
    estimated_receipts_remaining: int | None


class EtaInfo(BaseModel):
    text_ms: int | None
    image_ms: int | None
    samples_text: int
    samples_image: int


# ----- Runtime config patching -----

class ConfigPatchRequest(BaseModel):
    """Subset of Settings that can be tuned at runtime (other fields are read-only)."""
    poll_interval_idle_ms: int | None = Field(default=None, ge=50, le=60_000)
    poll_interval_printing_ms: int | None = Field(default=None, ge=10, le=10_000)
    reprint_max_age_hours: int | None = Field(default=None, ge=1, le=720)
    paper_low_threshold_mm: int | None = Field(default=None, ge=0, le=10_000)
    breaker_threshold: int | None = Field(default=None, ge=1, le=100)
    backoff_factor: float | None = Field(default=None, ge=1.0, le=10.0)
    use_lira_symbol: bool | None = None
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] | None = None


class ConfigPatchResponse(BaseModel):
    ok: bool
    updated: dict
    ts: datetime = Field(default_factory=utcnow)
