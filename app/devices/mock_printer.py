"""Mock thermal printer — the device "brain" (per M4).

Causality:
  - paper_count decreases by ESC/POS LF count in incoming bytes.
  - temperature_c updates lazily: heats during prints, cools when idle.
  - cover_open / jammed / comm_error_active: directly settable via test hooks.

DLE EOT registers (per ESC/POS spec):
  n=1 Printer status     — bit3 online, bit4 always 1, bit5 wait online, bit6 paper feed by FEED
  n=2 Offline status     — bit2 cover open, bit3 FEED pressed, bit5 paper end stop, bit6 error
  n=3 Error status       — bit2 mech error, bit3 cutter error, bit5 unrecoverable, bit6 auto-recoverable
  n=4 Paper sensor       — bit2,3 paper near-end, bit5,6 paper end
  All bytes: bit0 reserved=0, bit1 reserved=1, bit4 reserved=1, bit7 reserved=0.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.core.clock import Clock, SystemClock


# Bits 1 and 4 are reserved=1 in every DLE EOT response byte.
_RESERVED_MASK = 0b00010010


@dataclass
class MockPrinterConfig:
    paper_initial_lines: int = 500
    paper_low_threshold: int = 50
    temp_min: float = 20.0
    temp_max: float = 85.0
    initial_temp: float = 25.0
    cool_rate: float = 0.5            # °C/sec when idle
    heat_rate: float = 2.0            # °C/sec while actively printing
    overheat_stop_c: float = 65.0
    overheat_resume_c: float = 55.0
    # Duration a single send() "actively prints" for, used for temperature model
    print_duration_per_call_s: float = 0.3


class MockPrinter:
    """In-memory simulation of a Cashino-class ESC/POS thermal printer.

    Thread-safety: not designed for concurrent access — caller (MockTransport)
    is expected to serialize via the service-layer asyncio lock.
    """

    def __init__(self, config: MockPrinterConfig | None = None, clock: Clock | None = None):
        self._cfg = config or MockPrinterConfig()
        self._clock: Clock = clock or SystemClock()

        # Causal state
        self._paper_lines: int = self._cfg.paper_initial_lines
        self._temperature_c: float = self._cfg.initial_temp
        self._overheated: bool = False         # hysteresis flag
        self._cover_open: bool = False
        self._jammed: bool = False
        self._comm_error_active: bool = False

        # Temperature timeline: heating from _last_temp_ts until _printing_until,
        # cooling from _printing_until until now.
        now = self._clock.monotonic()
        self._last_temp_ts: float = now
        self._printing_until: float = now      # in the past initially → all cooling

    # ----- Test / control hooks (used by MockTransport facade & /mock endpoints) -----

    def set_paper(self, lines: int) -> None:
        self._paper_lines = max(0, int(lines))

    def set_cover(self, open_: bool) -> None:
        self._cover_open = bool(open_)

    def set_jammed(self, jammed: bool) -> None:
        self._jammed = bool(jammed)

    def set_comm_error(self, active: bool) -> None:
        self._comm_error_active = bool(active)

    def set_temperature(self, celsius: float) -> None:
        """Force temperature to a specific value (test/scenario use)."""
        self._temperature_c = float(celsius)
        self._last_temp_ts = self._clock.monotonic()
        self._update_hysteresis()

    @property
    def comm_error_active(self) -> bool:
        return self._comm_error_active

    @property
    def paper_lines(self) -> int:
        return self._paper_lines

    @property
    def cover_open(self) -> bool:
        return self._cover_open

    @property
    def jammed(self) -> bool:
        return self._jammed

    def temperature(self) -> float:
        """Lazy-updated current temperature."""
        self._advance_temperature()
        return self._temperature_c

    def overheated(self) -> bool:
        self._advance_temperature()
        return self._overheated

    def paper_low(self) -> bool:
        return self._paper_lines <= self._cfg.paper_low_threshold and self._paper_lines > 0

    # ----- ESC/POS data path -----

    def feed_data(self, data: bytes) -> None:
        """Consume bytes; count LFs as printed lines; mark printing-active for temp model.

        Does not raise — paper running out mid-stream is recorded but not exception.
        Caller decides how to react via subsequent read_status_byte().
        """
        lines = self._count_lines(data)
        # Decrement paper, never below 0
        self._paper_lines = max(0, self._paper_lines - lines)

        # Mark a print burst — extends heating window
        self._advance_temperature()   # cool up to now first
        now = self._clock.monotonic()
        burst = max(self._cfg.print_duration_per_call_s,
                    lines * 0.05)     # 50ms per line as rough estimate
        self._printing_until = max(self._printing_until, now) + burst

    def read_status_byte(self, register: int) -> int:
        """Return the DLE EOT n register byte. Includes reserved bits."""
        if register not in (1, 2, 3, 4):
            return _RESERVED_MASK   # any other register: only reserved bits
        self._advance_temperature()

        byte = _RESERVED_MASK
        if register == 1:
            # bit3 = online (we set this when not in fatal states)
            online = not (self._cover_open or self._paper_lines == 0 or self._jammed
                          or self._overheated)
            if online:
                byte |= 1 << 3
        elif register == 2:
            if self._cover_open:
                byte |= 1 << 2
            if self._paper_lines == 0:
                byte |= 1 << 5
            if self._jammed or self._overheated:
                byte |= 1 << 6      # generic error indicator
        elif register == 3:
            if self._jammed:
                byte |= 1 << 2      # mechanical error
            if self._overheated:
                byte |= 1 << 6      # auto-recoverable error
        elif register == 4:
            if self._paper_lines == 0:
                byte |= (1 << 5) | (1 << 6)   # paper-end sensor
            elif self.paper_low():
                byte |= (1 << 2) | (1 << 3)   # near-end sensor
        return byte

    # ----- Internal -----

    def _advance_temperature(self) -> None:
        """Bring temperature_c up to date based on the heat/cool timeline."""
        now = self._clock.monotonic()
        if now <= self._last_temp_ts:
            return

        cfg = self._cfg

        # Heating phase: from _last_temp_ts up to min(now, _printing_until)
        heat_end = min(now, self._printing_until)
        if heat_end > self._last_temp_ts:
            elapsed = heat_end - self._last_temp_ts
            self._temperature_c = min(
                self._temperature_c + elapsed * cfg.heat_rate,
                cfg.temp_max,
            )
            self._last_temp_ts = heat_end

        # Cooling phase: from _last_temp_ts (now == _printing_until) to now
        if now > self._last_temp_ts:
            elapsed = now - self._last_temp_ts
            self._temperature_c = max(
                self._temperature_c - elapsed * cfg.cool_rate,
                cfg.temp_min,
            )
            self._last_temp_ts = now

        self._update_hysteresis()

    def _update_hysteresis(self) -> None:
        cfg = self._cfg
        if self._temperature_c >= cfg.overheat_stop_c:
            self._overheated = True
        elif self._overheated and self._temperature_c <= cfg.overheat_resume_c:
            self._overheated = False

    @staticmethod
    def _count_lines(data: bytes) -> int:
        """Approximate line count from ESC/POS byte stream.

        Counts:
          - LF (0x0A) as one line
          - ESC d n (0x1B 0x64 n) as n lines
        Skips ESC <cmd> param bytes loosely. Good enough for paper tracking.
        """
        i = 0
        lines = 0
        n = len(data)
        while i < n:
            b = data[i]
            if b == 0x0A:           # LF
                lines += 1
                i += 1
            elif b == 0x1B and i + 2 < n and data[i + 1] == 0x64:  # ESC d n
                lines += data[i + 2]
                i += 3
            elif b == 0x1B and i + 1 < n:
                # Generic ESC command — skip ESC + cmd; param length varies.
                # We don't need precision here; advance 2 bytes.
                i += 2
            elif b == 0x1D and i + 1 < n:  # GS commands often have params
                i += 2
            else:
                i += 1
        return lines
