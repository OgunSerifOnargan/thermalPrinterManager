"""Every visible string switches between TR and EN based on req.lang."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.core.config import Settings, reset_settings_for_test
from app.devices.mock_preview import preview
from app.models.schemas import CategoryItem, PrintTextRequest
from app.services.receipt_renderer import ReceiptRenderer


@pytest.fixture
def settings(tmp_path, monkeypatch) -> Settings:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LOGO_PATH", "")   # text-wordmark fallback exposes subtitle
    monkeypatch.setenv("LOCAL_TIMEZONE", "Europe/Istanbul")
    reset_settings_for_test()
    yield Settings()
    reset_settings_for_test()


def _req(lang: str, title: str | None = None) -> PrintTextRequest:
    return PrintTextRequest(
        machine_id="ACO-LOC",
        items=[CategoryItem(product="Glass", quantity=1, reward=1.0)],
        total_reward=1.0,
        timestamp=datetime(2025, 9, 16, 16, 19, 2, tzinfo=UTC),
        lang=lang,
        title=title,
    )


def test_tr_strings_used_when_lang_tr(settings):
    r = ReceiptRenderer(settings)
    text = preview(r.render_text(_req("tr")))
    assert "Makine No:" in text
    assert "Aco Recycling Varsayılan Ödül" in text   # default title in TR
    assert "Eylül" in text                            # Turkish month
    assert "ters yönlü geri dönüşüm sistemleri" in text
    # English headers must NOT leak in
    assert "MachineID:" not in text
    assert "Product" not in text
    assert "September" not in text


def test_en_strings_used_when_lang_en(settings):
    r = ReceiptRenderer(settings)
    text = preview(r.render_text(_req("en")))
    assert "MachineID:" in text
    assert "Aco Recycling Default Reward" in text     # default title in EN
    assert "September" in text                        # English month
    assert "reverse vending recycling systems" in text
    # Turkish must NOT leak in
    assert "Makine No:" not in text
    assert "Ürün" not in text
    assert "Eylül" not in text


def test_explicit_title_overrides_default(settings):
    """Caller-supplied title wins over the localized default."""
    r = ReceiptRenderer(settings)
    text = preview(r.render_text(_req("tr", title="Custom Promo Title")))
    assert "Custom Promo Title" in text
    assert "Varsayılan Ödül" not in text
