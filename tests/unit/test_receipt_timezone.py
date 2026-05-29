"""Receipt timestamp follows LOCAL_TIMEZONE (Europe/Istanbul by default)."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.core.config import Settings, reset_settings_for_test
from app.models.schemas import CategoryItem, PrintTextRequest
from app.devices.mock_preview import preview
from app.services.receipt_renderer import ReceiptRenderer


@pytest.fixture
def settings(tmp_path, monkeypatch) -> Settings:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LOGO_PATH", "")
    reset_settings_for_test()
    yield Settings()
    reset_settings_for_test()


def _req(ts: datetime, lang: str = "tr") -> PrintTextRequest:
    return PrintTextRequest(
        machine_id="TZ-TEST",
        items=[CategoryItem(product="x", quantity=1, reward=1.0)],
        total_reward=1.0,
        timestamp=ts,
        lang=lang,
    )


def test_default_zone_shifts_utc_to_istanbul_summer(settings):
    # 16 Sep 2025 16:19:02 UTC → 19:19:02 TRT (Europe/Istanbul is UTC+3 all year)
    r = ReceiptRenderer(settings)
    out = r.render_text(_req(datetime(2025, 9, 16, 16, 19, 2, tzinfo=UTC), "tr"))
    text = preview(out)
    assert "16 Eylül 2025 19:19:02" in text
    # tzname for Europe/Istanbul on a modern tzdata is "+03" or "GMT+3"
    assert "+03" in text or "TRT" in text or "GMT+3" in text


def test_utc_zone_keeps_utc_time(settings, monkeypatch):
    monkeypatch.setenv("LOCAL_TIMEZONE", "UTC")
    reset_settings_for_test()
    s = Settings()
    r = ReceiptRenderer(s)
    # Use TR so the month name matches "Eylül" for a stable assertion.
    out = r.render_text(_req(datetime(2025, 9, 16, 16, 19, 2, tzinfo=UTC), "tr"))
    text = preview(out)
    assert "16 Eylül 2025 16:19:02 UTC" in text
    reset_settings_for_test()


def test_naive_input_treated_as_utc(settings):
    """A timestamp without tzinfo gets UTC, then local conversion."""
    r = ReceiptRenderer(settings)
    naive = datetime(2025, 9, 16, 16, 19, 2)        # no tzinfo
    out = r.render_text(_req(naive, "tr"))
    text = preview(out)
    assert "19:19:02" in text   # +03 shift applied


def test_invalid_timezone_rejected_at_startup(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LOCAL_TIMEZONE", "Not/A/Zone")
    reset_settings_for_test()
    with pytest.raises(Exception, match="local_timezone"):
        Settings()
    reset_settings_for_test()
