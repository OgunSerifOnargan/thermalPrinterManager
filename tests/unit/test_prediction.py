"""PaperPredictor — mm tracking + reset behavior."""
from __future__ import annotations

import pytest

from app.core.config import Settings, reset_settings_for_test
from app.services.prediction import PaperPredictor


@pytest.fixture
def settings(tmp_path, monkeypatch) -> Settings:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ROLL_LENGTH_MM", "10000")    # 10 m roll
    reset_settings_for_test()
    yield Settings()
    reset_settings_for_test()


def test_initial_snapshot_uses_default_estimate(settings: Settings):
    p = PaperPredictor(settings=settings)
    snap = p.snapshot()
    assert snap.paper_consumed_mm == 0.0
    assert snap.roll_length_mm == 10000
    assert snap.remaining_mm == 10000.0
    assert snap.estimated_receipts_remaining is not None
    assert snap.estimated_receipts_remaining > 0


def test_record_print_decreases_remaining(settings: Settings):
    p = PaperPredictor(settings=settings)
    # 5 LFs + cut overhead → ~17.5mm + 12mm fixed = ~29.5mm
    payload = b"\n\n\n\n\n\x1dV\x01"
    mm = p.record_print(payload)
    assert mm > 0
    snap = p.snapshot()
    assert snap.paper_consumed_mm == pytest.approx(mm, abs=0.1)
    assert snap.remaining_mm < 10000.0


def test_multiple_prints_accumulate(settings: Settings):
    p = PaperPredictor(settings=settings)
    payload = b"a\nb\nc\n"
    for _ in range(5):
        p.record_print(payload)
    snap = p.snapshot()
    assert snap.paper_consumed_mm > 0
    assert snap.avg_receipt_mm == pytest.approx(snap.paper_consumed_mm / 5, abs=0.1)


def test_reset_clears_state(settings: Settings):
    p = PaperPredictor(settings=settings)
    p.record_print(b"a\nb\nc\n")
    assert p.snapshot().paper_consumed_mm > 0
    p.reset()
    assert p.snapshot().paper_consumed_mm == 0.0


def test_image_height_counted(settings: Settings):
    """GS v 0 raster with height=100 dots adds 12.5mm of paper."""
    p = PaperPredictor(settings=settings)
    # GS v 0 m=0 xL=1 xH=0 yL=100 yH=0 → 1 width byte × 100 height
    payload = b"\x1dv0\x00\x01\x00\x64\x00" + (b"\x00" * 100)
    mm = p.record_print(payload)
    # 100 dots × 0.125 mm = 12.5 + 6 fixed = 18.5
    assert mm == pytest.approx(18.5, abs=0.5)


def test_estimate_uses_actual_avg_after_samples(settings: Settings):
    p = PaperPredictor(settings=settings)
    for _ in range(3):
        p.record_print(b"\n" * 10)   # 10 lines × 3.75mm + 6mm fixed = 43.5mm
    snap = p.snapshot()
    assert snap.avg_receipt_mm == pytest.approx(43.5, abs=1.0)


def test_qr_print_command_adds_fixed_block(settings: Settings):
    """GS ( k ... 31 51 30 (QR print) adds a fixed ~32mm block."""
    p = PaperPredictor(settings=settings)
    # QR store data block + QR print block
    qr_store = b"\x1d(k\x05\x00\x31\x50\x30AB"          # 5 bytes payload
    qr_print = b"\x1d(k\x03\x00\x31\x51\x30"            # the actual paper-advance one
    payload = qr_store + qr_print
    mm = p.record_print(payload)
    # 32 (QR) + 6 (fixed) = 38
    assert mm == pytest.approx(38.0, abs=1.0)


def test_double_height_doubles_line_consumption(settings: Settings):
    """Lines printed while ESC ! has bit 4 set count as 2× height."""
    p = PaperPredictor(settings=settings)
    # ESC ! 0x10 (double-height on) + LF + ESC ! 0x00 (off) + LF
    payload = b"\x1b!\x10\n\x1b!\x00\n"
    mm = p.record_print(payload)
    # 1 double line (7.5mm) + 1 normal line (3.75mm) + 6 fixed = 17.25
    assert mm == pytest.approx(17.25, abs=0.5)
