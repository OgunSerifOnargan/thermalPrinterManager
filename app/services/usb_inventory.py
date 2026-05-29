"""Enumerate USB devices visible to the host.

Two flavors:
  - **USB-CDC serial ports** via `serial.tools.list_ports` (macOS /dev/cu.*,
    Linux /dev/ttyUSB*, Windows COM*). Also includes our PTY symlink at
    /tmp/mock-printer-usb when the mock device server is running.
  - **libusb devices** via pyusb (raw USB enumeration, all classes).

The UI uses this to populate dropdowns so users don't memorize VID/PID hex
strings — they pick from the list of plugged-in devices.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any


log = logging.getLogger(__name__)


# Common stable paths the mock device server may use
_MOCK_PTY_PATHS: tuple[str, ...] = ("/tmp/mock-printer-usb",)


def list_serial_ports() -> list[dict[str, Any]]:
    """Return USB-CDC / serial ports the OS exposes, plus any mock PTY symlink."""
    out: list[dict[str, Any]] = []

    # 1) Mock PTY first (visually prominent)
    for path in _MOCK_PTY_PATHS:
        try:
            if os.path.lexists(path):
                real = os.readlink(path) if os.path.islink(path) else path
                out.append({
                    "path": path,
                    "label": "Aco Mock Device (virtual USB-CDC)",
                    "manufacturer": "Aco (mock)",
                    "product": f"PTY → {real}",
                    "vid_hex": None,
                    "pid_hex": None,
                    "serial_number": None,
                    "is_mock": True,
                })
        except OSError as exc:
            log.warning("mock PTY probe failed for %s: %s", path, exc)

    # 2) Real USB-CDC ports via pyserial — keep only those that look like
    #    actual USB devices (VID present) or whose name suggests USB.
    try:
        from serial.tools import list_ports
        for p in list_ports.comports():
            if any(p.device == m["path"] for m in out):
                continue
            is_usb = bool(p.vid) or any(
                tag in (p.device or "").lower()
                for tag in ("usbserial", "usbmodem", "ttyusb", "ttyacm")
            )
            if not is_usb:
                # Skip noise like /dev/cu.Bluetooth-*, debug-console
                continue
            out.append({
                "path": p.device,
                "label": p.description or p.name or p.device,
                "manufacturer": p.manufacturer,
                "product": p.product,
                "vid_hex": f"0x{p.vid:04x}" if p.vid else None,
                "pid_hex": f"0x{p.pid:04x}" if p.pid else None,
                "serial_number": p.serial_number,
                "is_mock": False,
            })
    except Exception as exc:                                     # noqa: BLE001
        log.warning("serial port enumeration failed: %s", exc)

    return out


def list_libusb_devices() -> list[dict[str, Any]]:
    """Return raw USB devices (all classes) via libusb. Strings best-effort."""
    out: list[dict[str, Any]] = []
    try:
        import usb.core
        import usb.util
    except ImportError:
        log.info("pyusb not available — skipping libusb enumeration")
        return out

    try:
        devices = list(usb.core.find(find_all=True))
    except Exception as exc:                                     # noqa: BLE001
        log.warning("libusb find failed (libusb backend missing?): %s", exc)
        return out

    for dev in devices:
        manufacturer = product = None
        # String descriptor lookup needs to open the device; some require
        # elevated permissions. Fail soft — VID/PID are always enough.
        try:
            if dev.iManufacturer:
                manufacturer = usb.util.get_string(dev, dev.iManufacturer)
        except Exception:                                        # noqa: BLE001
            pass
        try:
            if dev.iProduct:
                product = usb.util.get_string(dev, dev.iProduct)
        except Exception:                                        # noqa: BLE001
            pass

        out.append({
            "vendor_id": f"0x{dev.idVendor:04x}",
            "product_id": f"0x{dev.idProduct:04x}",
            "manufacturer": manufacturer,
            "product": product,
            "bus": getattr(dev, "bus", None),
            "address": getattr(dev, "address", None),
        })

    return out


def inventory() -> dict[str, Any]:
    return {
        "serial_ports": list_serial_ports(),
        "libusb_devices": list_libusb_devices(),
    }
