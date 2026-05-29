"""cp857 encoder + transliteration fallback (per R1)."""
from __future__ import annotations

import pytest

from app.core.config import Settings, reset_settings_for_test
from app.services.encoding import encode_text


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    reset_settings_for_test()
    yield Settings()
    reset_settings_for_test()


def test_basic_ascii(settings: Settings):
    assert encode_text("hello", settings) == b"hello"


def test_turkish_letters_native_in_cp857(settings: Settings):
    """ş, ğ, ı, İ, ö, ü, ç all live in cp857 — no substitution."""
    out = encode_text("Eylül", settings)
    # 'E' = 0x45, 'y' = 0x79, 'l' = 0x6c, 'ü' = 0x81 in cp857, 'l' = 0x6c
    assert out == b"E" + b"y" + b"l" + b"\x81" + b"l"


def test_full_turkish_alphabet(settings: Settings):
    s = "şŞğĞıİöÖüÜçÇ"
    out = encode_text(s, settings)
    # Just make sure every char encoded to a single byte (cp857 has them all)
    assert len(out) == len(s)
    # And the bytes match what str.encode('cp857') gives directly
    assert out == s.encode("cp857")


def test_lira_replaced_with_tl_by_default(settings: Settings):
    out = encode_text("Ödül: 3.00 ₺", settings)
    # ₺ should have become "TL"
    assert b"TL" in out
    assert b"\xff" not in out         # no garbage byte


def test_unsupported_char_falls_back(settings: Settings):
    """A character with no cp857 and no transliteration → '?' + warning."""
    out = encode_text("hello 漢", settings)   # CJK not in cp857
    assert b"hello " in out
    assert b"?" in out


def test_currency_transliteration(settings: Settings):
    out = encode_text("price: 10€", settings)
    assert b"EUR" in out


def test_smart_quotes_transliterated(settings: Settings):
    out = encode_text("he said “hi”", settings)
    assert out.count(b'"') == 2


def test_use_lira_symbol_keeps_lira_text(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("USE_LIRA_SYMBOL", "true")
    reset_settings_for_test()
    s = Settings()
    out = encode_text("3.00 ₺", s)
    # With use_lira_symbol=True, encoder emits the UTF-8 byte sequence for ₺
    # so mock_preview can recover the glyph. No "TL" substitution.
    assert b"\xe2\x82\xba" in out
    assert b"TL" not in out
    reset_settings_for_test()
