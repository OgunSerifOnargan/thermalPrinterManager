"""Mock printer brain — paper/temp/cover/jam causality and DLE EOT status bytes."""
from __future__ import annotations

import pytest

from app.core.clock import FakeClock
from app.devices.mock_printer import MockPrinter, MockPrinterConfig


def make_printer(**cfg_overrides) -> tuple[MockPrinter, FakeClock]:
    clock = FakeClock(start=0.0)
    cfg = MockPrinterConfig(**cfg_overrides)
    return MockPrinter(config=cfg, clock=clock), clock


# ---------- Paper ----------

def test_lf_decrements_paper():
    p, _ = make_printer(paper_initial_lines=10)
    p.feed_data(b"hello\nworld\nfoo\n")  # 3 LFs
    assert p.paper_lines == 7


def test_esc_d_n_decrements_n_lines():
    p, _ = make_printer(paper_initial_lines=20)
    p.feed_data(b"\x1bd\x05hello\n")  # ESC d 5 + LF = 6 lines
    assert p.paper_lines == 14


def test_paper_runs_out_stays_at_zero():
    p, _ = make_printer(paper_initial_lines=3)
    p.feed_data(b"\n\n\n\n\n")  # 5 LFs but only 3 lines available
    assert p.paper_lines == 0


def test_dle_eot_n4_paper_end_bits_when_empty():
    p, _ = make_printer(paper_initial_lines=0)
    byte = p.read_status_byte(4)
    # paper-end bits 5 and 6
    assert byte & (1 << 5)
    assert byte & (1 << 6)


def test_dle_eot_n4_near_end_bits_when_low():
    p, _ = make_printer(paper_initial_lines=10, paper_low_threshold=20)
    byte = p.read_status_byte(4)
    assert byte & (1 << 2)
    assert byte & (1 << 3)
    # but NOT paper-end bits
    assert not (byte & (1 << 5))


# ---------- Cover ----------

def test_cover_open_bit_in_n2():
    p, _ = make_printer()
    p.set_cover(True)
    byte = p.read_status_byte(2)
    assert byte & (1 << 2)


def test_cover_closed_no_bit():
    p, _ = make_printer()
    byte = p.read_status_byte(2)
    assert not (byte & (1 << 2))


# ---------- Jam ----------

def test_jam_sets_mech_error_in_n3():
    p, _ = make_printer()
    p.set_jammed(True)
    byte = p.read_status_byte(3)
    assert byte & (1 << 2)


# ---------- Temperature & OVERHEAT hysteresis ----------

def test_temperature_heats_during_print():
    p, clock = make_printer(initial_temp=25.0, heat_rate=10.0,
                            print_duration_per_call_s=1.0)
    p.feed_data(b"x")          # triggers 1.0s of heating
    clock.tick(1.0)            # advance clock past the heat burst
    assert p.temperature() > 25.0


def test_temperature_cools_when_idle():
    p, clock = make_printer(initial_temp=60.0, cool_rate=2.0)
    clock.tick(10.0)           # 10 seconds idle → -20°C
    assert p.temperature() == pytest.approx(40.0, abs=0.5)


def test_overheat_triggers_at_stop_c():
    p, _ = make_printer(initial_temp=25.0, overheat_stop_c=65.0,
                        overheat_resume_c=55.0)
    p.set_temperature(66.0)
    assert p.overheated() is True
    # bit 6 of n=3 → auto-recoverable error
    assert p.read_status_byte(3) & (1 << 6)


def test_overheat_hysteresis_does_not_clear_above_resume():
    p, _ = make_printer(overheat_stop_c=65.0, overheat_resume_c=55.0)
    p.set_temperature(66.0)
    assert p.overheated() is True
    p.set_temperature(60.0)    # below stop but above resume
    assert p.overheated() is True


def test_overheat_clears_at_resume_c():
    p, _ = make_printer(overheat_stop_c=65.0, overheat_resume_c=55.0)
    p.set_temperature(66.0)
    p.set_temperature(54.0)
    assert p.overheated() is False


def test_overheat_recover_via_tick_time():
    """The headline test (per P3) — OVERHEAT clears deterministically with tick."""
    p, clock = make_printer(
        initial_temp=66.0,           # already overheated
        overheat_stop_c=65.0,
        overheat_resume_c=55.0,
        cool_rate=0.1,               # 0.1 °C / sec → 110s to drop 11°C
    )
    p.set_temperature(66.0)
    assert p.overheated() is True
    clock.tick(120.0)                # >110 seconds: should fall below 55°C
    assert p.overheated() is False


# ---------- Online bit ----------

def test_online_bit_set_when_clean():
    p, _ = make_printer()
    byte = p.read_status_byte(1)
    assert byte & (1 << 3)


def test_online_bit_cleared_on_paper_out():
    p, _ = make_printer(paper_initial_lines=0)
    byte = p.read_status_byte(1)
    assert not (byte & (1 << 3))


def test_online_bit_cleared_on_cover_open():
    p, _ = make_printer()
    p.set_cover(True)
    byte = p.read_status_byte(1)
    assert not (byte & (1 << 3))
