"""Fail-closed upload time checks; never correct a measurement's sampling time."""
from dataclasses import dataclass
import subprocess
import threading
import time


@dataclass(frozen=True)
class ClockState:
    trusted: bool
    category: str


class Clock:
    def __init__(self, mode: str, grace: int = 86400, probe=None):
        self.mode, self.grace = mode, grace
        self.probe = probe or self.ntp_synchronized
        self.last_wall = self.last_mono = self.last_sync = self.last_probe = None
        self.ntp_ok = False
        self.quarantine_until = 0.0
        self.lock = threading.Lock()

    @staticmethod
    def ntp_synchronized() -> bool:
        try:
            result = subprocess.run(['timedatectl', 'show', '--property=NTPSynchronized', '--value'],
                                    capture_output=True, text=True, timeout=2, check=False)
            return result.returncode == 0 and result.stdout.strip() == 'yes'
        except (OSError, subprocess.TimeoutExpired):
            return False

    def state(self) -> ClockState:
        with self.lock:
            return self._state()

    def _state(self) -> ClockState:
        if self.mode != 'real':
            return ClockState(True, 'development_clock_only')
        wall, mono = time.time(), time.monotonic()
        jumped = self.last_wall is not None and abs((wall-self.last_wall)-(mono-self.last_mono)) > 2
        self.last_wall, self.last_mono = wall, mono
        if jumped:
            self.last_sync = self.last_probe = None
            self.ntp_ok = False
            self.quarantine_until = mono+30
            return ClockState(False, 'clock_jump_detected')
        if mono < self.quarantine_until:
            return ClockState(False, 'clock_stabilizing')
        if self.last_probe is None or mono-self.last_probe >= 30:
            self.ntp_ok = self.probe()
            self.last_probe = mono
            if self.ntp_ok:
                self.last_sync = mono
        if self.ntp_ok or (self.last_sync is not None and mono-self.last_sync <= self.grace):
            return ClockState(True, 'ntp_synchronized' if self.ntp_ok else 'ntp_holdover')
        return ClockState(False, 'ntp_not_synchronized')
