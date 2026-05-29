"""Pydantic Settings — fail-fast configuration loaded from .env at startup."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        validate_assignment=True,    # PATCH /config validates on setattr
    )

    # Server
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    dev_mode: bool = True
    transport_backend: Literal["mock", "real"] = "mock"   # safe default for dev
    # If set, `/mock/*` HTTP endpoints proxy to this URL (a standalone mock
    # device service). When unset, they poke the in-process MockPrinter.
    mock_device_control_url: str = ""

    # Connection
    default_mode: Literal["usb", "lan", ""] = ""
    lan_host: str = ""
    lan_port: int = Field(default=9100, ge=1, le=65535)
    usb_vendor_id: str = ""
    usb_product_id: str = ""
    # USB-CDC / serial path (e.g. /dev/cu.usbserial-XXXX, /tmp/mock-printer-usb).
    # When set, USB mode prefers SerialTransport over libusb.
    usb_device_path: str = ""

    # Reconnect / backoff / breaker
    connect_retry_base_ms: int = Field(default=500, ge=10)
    connect_retry_max_ms: int = Field(default=30_000, ge=100)
    backoff_factor: float = Field(default=2.0, ge=1.0)
    breaker_threshold: int = Field(default=5, ge=1)

    # Polling
    poll_interval_idle_ms: int = Field(default=1000, ge=50)
    poll_interval_printing_ms: int = Field(default=200, ge=10)
    status_retry_max_idle: int = Field(default=3, ge=1)
    status_retry_max_printing: int = Field(default=2, ge=1)

    # Device thresholds
    overheat_stop_c: float = Field(default=65.0, ge=40.0, le=120.0)
    overheat_resume_c: float = Field(default=55.0, ge=20.0, le=120.0)

    # Paper / receipt
    roll_length_mm: int = Field(default=10_000, ge=100)
    paper_low_threshold_mm: int = Field(default=500, ge=0)
    paper_width_cols: int = Field(default=32, ge=16, le=64)
    printer_codepage: str = "cp857"
    use_lira_symbol: bool = False
    # IANA zone for the *receipt* timestamp (e.g. "Europe/Istanbul" = TRT,
    # "UTC", "America/New_York"). Logs and API responses stay in UTC.
    local_timezone: str = "Europe/Istanbul"

    # Receipt header image — embedded above every receipt. Empty string disables.
    logo_path: str = "app/assets/aco_logo.png"

    # Job store
    jobs_db_path: str = "./jobs.db"
    reprint_max_age_hours: int = Field(default=24, ge=1)
    # Sliding-window retention: jobs older than this get hard-deleted from the
    # DB (BLOB + metadata). Keeps jobs.db from growing without bound. Must be
    # >= reprint_max_age_hours so we never delete a row that could still be
    # reprinted.
    job_retention_days: int = Field(default=7, ge=1, le=365)

    # Image upload
    max_image_bytes: int = Field(default=2_097_152, ge=1024)
    # G7: hard cap on the rendered ESC/POS payload. ESC/POS printers' receive
    # buffer is typically 8-64KB; bigger payloads silently lose tail bytes.
    max_receipt_bytes: int = Field(default=30_720, ge=1024, le=131_072)

    # GS r 1 fence: when True, the print path appends GS r 1 to the payload and
    # blocks the device lock until the response byte arrives — i.e. until the
    # printer has physically processed every byte before the fence. Per the
    # Cashino KP-300 user manual, p. 63: GS r is a *buffered* command, so the
    # response gates on buffer drain. Default ON because demos can't be
    # rehearsed live on the real device.
    wait_for_print_done: bool = True
    wait_for_print_timeout_ms: int = Field(default=5_000, ge=100, le=30_000)
    # Mock device simulates ~10 ms per line so unit + UI demo timings match the
    # real Cashino (which prints at 250 mm/s ≈ 30 dots/line / 3 ms ≈ 10 ms/line).
    # Set to 0 in tests that don't care about timing.
    mock_print_delay_ms_per_line: int = Field(default=10, ge=0, le=200)

    # Logging
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["jsonl"] = "jsonl"
    log_dir: str = "./logs"
    log_retention_days: int = Field(default=7, ge=1)

    # UI
    ui_poll_interval_ms: int = Field(default=1500, ge=100)

    # Auth (bonus)
    config_patch_token: str = ""

    # G5: per-IP rate limit on POST /print/*. Defender against accidental floods
    # (script loops) and trivial DoS. Set to 0 to disable.
    print_rate_limit_per_min: int = Field(default=10, ge=0, le=1000)

    # Mock device
    mock_paper_initial_lines: int = Field(default=500, ge=0)
    mock_paper_low_threshold: int = Field(default=50, ge=0)
    mock_temp_min: float = Field(default=20.0, ge=-40.0)
    mock_temp_max: float = Field(default=85.0, le=200.0)
    mock_initial_temp: float = Field(default=25.0)
    mock_cool_rate: float = Field(default=0.5, gt=0)
    mock_heat_rate: float = Field(default=2.0, gt=0)

    @field_validator("overheat_resume_c")
    @classmethod
    def _resume_below_stop(cls, v: float, info) -> float:
        stop = info.data.get("overheat_stop_c")
        if stop is not None and v >= stop:
            raise ValueError(
                f"overheat_resume_c ({v}) must be < overheat_stop_c ({stop}); "
                "needed for hysteresis"
            )
        return v

    @field_validator("log_dir")
    @classmethod
    def _ensure_log_dir(cls, v: str) -> str:
        Path(v).mkdir(parents=True, exist_ok=True)
        return v

    @field_validator("local_timezone")
    @classmethod
    def _validate_timezone(cls, v: str) -> str:
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(v)  # raises ZoneInfoNotFoundError on garbage
        except Exception as exc:                                 # noqa: BLE001
            raise ValueError(
                f"local_timezone {v!r} is not an IANA zone: {exc}"
            ) from exc
        return v


_settings: Settings | None = None


def get_settings() -> Settings:
    """Singleton accessor — initialized on first call, cached thereafter."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings_for_test() -> None:
    """Clear cached settings so tests can re-read env vars."""
    global _settings
    _settings = None
