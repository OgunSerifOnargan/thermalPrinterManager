"""Config validation tests — fail-fast at startup is non-negotiable."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_defaults_load_successfully(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = Settings()
    assert s.port == 8000
    assert s.printer_codepage == "cp857"
    assert s.paper_width_cols == 32
    assert s.dev_mode is True
    assert s.overheat_stop_c == 65.0
    assert s.overheat_resume_c == 55.0


def test_overheat_resume_must_be_below_stop(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OVERHEAT_STOP_C", "60.0")
    monkeypatch.setenv("OVERHEAT_RESUME_C", "65.0")
    with pytest.raises(ValidationError, match="overheat_resume_c"):
        Settings()


def test_invalid_port_rejected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PORT", "70000")
    with pytest.raises(ValidationError):
        Settings()


def test_invalid_default_mode_rejected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DEFAULT_MODE", "bluetooth")
    with pytest.raises(ValidationError):
        Settings()


def test_log_dir_is_created(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    log_dir = tmp_path / "custom_logs"
    monkeypatch.setenv("LOG_DIR", str(log_dir))
    s = Settings()
    assert log_dir.exists()
    assert s.log_dir == str(log_dir)


def test_overheat_resume_equal_to_stop_rejected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OVERHEAT_STOP_C", "65.0")
    monkeypatch.setenv("OVERHEAT_RESUME_C", "65.0")
    with pytest.raises(ValidationError):
        Settings()


def test_kp301h_profile_resume_60_valid(tmp_path, monkeypatch):
    """KP-301H uses 60°C resume threshold — must be accepted."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OVERHEAT_RESUME_C", "60.0")
    s = Settings()
    assert s.overheat_resume_c == 60.0
