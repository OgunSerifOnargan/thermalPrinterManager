"""ReceiptRenderer — block composition produces sensible byte streams."""
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
    monkeypatch.setenv("LOGO_PATH", "")    # text-wordmark fallback; keeps render lean
    reset_settings_for_test()
    yield Settings()
    reset_settings_for_test()


def make_req(**overrides) -> PrintTextRequest:
    base = dict(
        machine_id="ACO-TEST-0001",
        items=[
            CategoryItem(product="Glass", quantity=0, reward=0.0),
            CategoryItem(product="Plastic", quantity=2, reward=2.0),
            CategoryItem(product="Metal", quantity=1, reward=1.0),
            CategoryItem(product="Tetrapak", quantity=0, reward=0.0),
        ],
        total_reward=3.0,
        timestamp=datetime(2025, 9, 16, 16, 19, 2, tzinfo=UTC),
        qr_content="ACO-TEST-0001|3.00",
        lang="tr",
        title="Aco Recycling Default Reward",
    )
    base.update(overrides)
    return PrintTextRequest(**base)


def test_renders_to_nonempty_bytes(settings: Settings):
    r = ReceiptRenderer(settings=settings)
    out = r.render_text(make_req())
    assert isinstance(out, bytes)
    assert len(out) > 100


def test_starts_with_init_and_codepage_select(settings: Settings):
    r = ReceiptRenderer(settings=settings)
    out = r.render_text(make_req())
    assert out.startswith(b"\x1b@")            # ESC @ init
    assert b"\x1bt\x0d" in out                 # ESC t 13 (cp857)


def test_includes_machine_id(settings: Settings):
    """Machine ID label is localized; the ID itself is verbatim."""
    r = ReceiptRenderer(settings=settings)
    # TR default → "Makine No:", EN → "MachineID:".
    out_tr = r.render_text(make_req(machine_id="ACO-XYZ", lang="tr"))
    out_en = r.render_text(make_req(machine_id="ACO-XYZ", lang="en"))
    assert "Makine No: ACO-XYZ" in preview(out_tr)
    assert "MachineID: ACO-XYZ" in preview(out_en)


def test_includes_all_categories(settings: Settings):
    r = ReceiptRenderer(settings=settings)
    out = r.render_text(make_req())
    text = preview(out)
    for product in ("Glass", "Plastic", "Metal", "Tetrapak"):
        assert product in text


def test_reward_is_centered_and_doubled(settings: Settings):
    r = ReceiptRenderer(settings=settings)
    out = r.render_text(make_req(total_reward=42.0))
    # ESC ! 0x30 (size double) precedes the reward line
    assert b"\x1b!\x30" in out
    text = preview(out)
    assert "42.00" in text or "42,00" in text


def test_ends_with_cut(settings: Settings):
    r = ReceiptRenderer(settings=settings)
    out = r.render_text(make_req())
    assert out.endswith(b"\x1dV\x01")


def test_qr_command_emitted(settings: Settings):
    r = ReceiptRenderer(settings=settings)
    out = r.render_text(make_req(qr_content="hello-qr"))
    assert b"\x1d(k" in out                    # QR command prefix


def test_lira_substituted_with_tl_in_reward_line(settings: Settings):
    r = ReceiptRenderer(settings=settings)
    out = r.render_text(make_req(total_reward=3.5))
    text = preview(out)
    assert "TL" in text


def test_table_header_localized_en(settings: Settings):
    r = ReceiptRenderer(settings=settings)
    out = r.render_text(make_req(lang="en"))
    text = preview(out)
    assert "Product" in text
    assert "Quantity" in text
    assert "Reward" in text


def test_table_header_localized_tr(settings: Settings):
    r = ReceiptRenderer(settings=settings)
    out = r.render_text(make_req(lang="tr"))
    text = preview(out)
    assert "Ürün" in text
    assert "Adet" in text
    assert "Ödül" in text


def test_no_items_shows_placeholder(settings: Settings):
    r = ReceiptRenderer(settings=settings)
    out = r.render_text(make_req(items=[]))
    text = preview(out)
    assert "(ürün yok)" in text or "(no items)" in text


def test_long_product_name_truncated(settings: Settings):
    """Product longer than column width gets truncated with trailing '.'."""
    r = ReceiptRenderer(settings=settings)
    # cols=32 → name_w = 32 - 12 = 20; pydantic max is 40, so use 35.
    long_name = "X" * 35
    req = make_req(items=[CategoryItem(product=long_name, quantity=1, reward=1.0)])
    out = r.render_text(req)
    text = preview(out)
    # Should never see all 35 Xs in a row — truncated
    assert "X" * 30 not in text
