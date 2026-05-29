"""ERROR_POLICY table contract — every ErrorCode mapped, HTTP statuses sane."""
from __future__ import annotations

from app.core.errors import ERROR_POLICY, ErrorCategory, ErrorCode


def test_every_error_code_has_a_policy():
    for code in ErrorCode:
        assert code in ERROR_POLICY, f"missing policy row for {code}"


def test_policy_codes_match_dict_keys():
    for code, policy in ERROR_POLICY.items():
        assert policy.code == code


def test_unknown_command_does_not_enter_error_state():
    """Bad input must not punish the device — service stays IDLE (per D2)."""
    p = ERROR_POLICY[ErrorCode.UNKNOWN_COMMAND]
    assert p.enters_error_state is False
    assert p.category == ErrorCategory.COMMAND
    assert p.http_status == 400


def test_hardware_errors_enter_error_state():
    for code in (ErrorCode.PAPER_OUT, ErrorCode.PAPER_JAM,
                 ErrorCode.COVER_OPEN, ErrorCode.OVERHEAT):
        p = ERROR_POLICY[code]
        assert p.enters_error_state is True
        assert p.category == ErrorCategory.HARDWARE
        assert p.http_status == 503


def test_overheat_is_auto_recoverable():
    p = ERROR_POLICY[ErrorCode.OVERHEAT]
    assert p.auto_recoverable is True
    assert p.keep_polling is True   # need polling to detect cooldown


def test_paper_out_requires_user():
    p = ERROR_POLICY[ErrorCode.PAPER_OUT]
    assert p.auto_recoverable is False
    assert p.keep_polling is True   # polling still detects when user loads paper


def test_comm_error_owned_by_reconcile_not_polling():
    p = ERROR_POLICY[ErrorCode.COMM_ERROR]
    assert p.category == ErrorCategory.COMM
    assert p.keep_polling is False
    assert p.http_status == 503


def test_every_policy_has_localized_messages():
    for code, policy in ERROR_POLICY.items():
        assert policy.user_message_tr.strip(), f"{code} missing TR message"
        assert policy.user_message_en.strip(), f"{code} missing EN message"
