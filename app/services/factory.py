"""Transport factory — picks Mock or Real based on settings."""
from __future__ import annotations

from app.core.config import Settings
from app.devices.mock_printer import MockPrinter, MockPrinterConfig
from app.devices.mock_transport import MockTransport
from app.devices.real_transport import LanTransport, UsbTransport
from app.devices.serial_transport import SerialTransport
from app.devices.transport import ConnectionMode, DeviceTransport


_shared_mock_printer: MockPrinter | None = None


def get_shared_mock_printer(settings: Settings) -> MockPrinter:
    """Singleton MockPrinter for the running app — /mock/* endpoints poke this."""
    global _shared_mock_printer
    if _shared_mock_printer is None:
        cfg = MockPrinterConfig(
            paper_initial_lines=settings.mock_paper_initial_lines,
            paper_low_threshold=settings.mock_paper_low_threshold,
            temp_min=settings.mock_temp_min,
            temp_max=settings.mock_temp_max,
            initial_temp=settings.mock_initial_temp,
            cool_rate=settings.mock_cool_rate,
            heat_rate=settings.mock_heat_rate,
            overheat_stop_c=settings.overheat_stop_c,
            overheat_resume_c=settings.overheat_resume_c,
        )
        _shared_mock_printer = MockPrinter(config=cfg)
    return _shared_mock_printer


def reset_shared_mock_printer() -> None:
    global _shared_mock_printer
    _shared_mock_printer = None


def make_transport(
    settings: Settings,
    mode: ConnectionMode,
    overrides: dict | None = None,
) -> DeviceTransport:
    """Build a DeviceTransport for the requested mode.

    - TRANSPORT_BACKEND=mock (default): MockTransport for both modes.
    - TRANSPORT_BACKEND=real: LanTransport or UsbTransport based on mode.

    `overrides` carries per-request connect parameters from /connect; when a
    key is present, it wins over the corresponding .env setting (without
    mutating the global settings object).
    """
    ov = overrides or {}
    if settings.transport_backend == "real":
        if mode == "lan":
            host = ov.get("lan_host") or settings.lan_host
            port = ov.get("lan_port") or settings.lan_port
            return LanTransport(host=host, port=port)
        # USB: prefer USB-CDC serial path (incl. mock PTY) when configured,
        # fall back to libusb for vendor-class devices.
        device_path = ov.get("usb_device_path") or settings.usb_device_path
        if device_path:
            return SerialTransport(device_path=device_path)
        return UsbTransport(
            vendor_id=ov.get("usb_vendor_id") or settings.usb_vendor_id,
            product_id=ov.get("usb_product_id") or settings.usb_product_id,
        )
    return MockTransport(printer=get_shared_mock_printer(settings), mode=mode)
