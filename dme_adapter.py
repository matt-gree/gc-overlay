"""Process-memory transport: we poll Dolphin's emulated memory.

Hooks the running Dolphin process and reads MEM1 directly, which is the
technique M'Overlay uses on Windows. One of two peer adapters (see
dolphin_common, which owns the profile and the parsing); this one owns only
the transport.

Its constraint is the mirror image of memorywatcher_adapter's. Reading
another process's memory is free on Windows and Linux and blocked on macOS,
where Dolphin ships a hardened runtime with no get-task-allow entitlement —
so macOS uses MemoryWatcher and this module is for Windows (and Linux, where
either works).

Requires the ``dolphin-memory-engine`` wheel: a ~340 KB compiled extension
that vendors the memory-access core of aldelaro5's Dolphin Memory Engine.
It is a library, not the GUI application — nothing is installed on the
producer's machine and no Dolphin build change is needed.
"""

import threading
import time

import dolphin_memory_engine as dme

# DolphinStatus is NOT re-exported from the package's __init__; reach for it
# in the extension module directly. Members: hooked=0, notRunning=1 (no
# Dolphin process), noEmu=2 (Dolphin open, no game booted), unHooked=3.
from dolphin_memory_engine._dolphin_memory_engine import DolphinStatus

from dolphin_common import FIELD_UPDATERS, DolphinPort, load_game_profile


# The GameCube disc header is mapped at 0x80000000; its first six bytes are
# the game code + maker code, e.g. "GYQE01".
GAME_ID_ADDR = 0x80000000
GAME_ID_LEN = 6

# Poll at twice the server's 120 Hz broadcast rate. Faster buys nothing a
# viewer can see, and every tick is a cross-process read.
DEFAULT_POLL_HZ = 240

# How long to wait between hook attempts while Dolphin is closed or sitting
# at its game list.
HOOK_RETRY_SECONDS = 1.0


class DmeAdapter:
    """Reads controller state by polling Dolphin's emulated memory.

    Usage:
        adapter = DmeAdapter("game_profiles/mario_superstar_baseball.json")
        adapter.start()
        state = adapter.get_state(0)  # port 0
        adapter.stop()
    """

    def __init__(self, profile_path, poll_hz=DEFAULT_POLL_HZ):
        self._profile = load_game_profile(profile_path)

        base = self._profile.get('controller_base')
        if base is None:
            raise ValueError(
                f"No controller_base address in {profile_path}. "
                "Set one with --dolphin-set-addr <hex_address>."
            )
        self._base_addr = int(base, 16) if isinstance(base, str) else base

        self._port_count = self._profile.get('port_count', 4)
        self._stride = self._profile.get('controller_stride', 12)
        self._field_offsets = self._profile.get('field_offsets', {})
        self._expected_game_id = self._profile.get('game_id')

        # Every field of every port sits inside one contiguous span, so a
        # tick is a single cross-process read rather than one per field.
        self._span = (
            (self._port_count - 1) * self._stride
            + max(self._field_offsets.values())
            + 4
        )

        norm = self._profile.get('normalization', {})
        self._ports = [
            DolphinPort(
                norm.get('stick_range', 80),
                norm.get('cstick_range', 80),
                norm.get('trigger_max', 200),
            )
            for _ in range(4)
        ]

        self.connected = False
        self._lock = threading.Lock()
        self._running = False
        self._thread = None
        self._interval = 1.0 / poll_hz

        self._last_status = None
        self._warned_game_id = False

    def start(self):
        """Start the polling thread. Hooks lazily, and retries forever."""
        print(
            f"Watching {len(self._field_offsets)} fields x {self._port_count} "
            f"ports at 0x{self._base_addr:08X} ({self._span} bytes)"
        )
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop polling and release the hook."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        if dme.is_hooked():
            dme.un_hook()

    def get_state(self, port=0):
        """Get controller state for a port (thread-safe)."""
        with self._lock:
            return self._ports[port].to_dict()

    def get_all_states(self):
        """Get states for all 4 ports."""
        with self._lock:
            return [p.to_dict() for p in self._ports]

    def calibrate(self, port=0):
        """No-op, as for MemoryWatcher: the game handles calibration."""
        pass

    def _report_status(self, status):
        """Log status changes once, rather than every tick."""
        if status == self._last_status:
            return
        self._last_status = status
        print({
            DolphinStatus.hooked: "Hooked into Dolphin. Reading controller data.",
            DolphinStatus.notRunning: "Waiting for Dolphin/Project Rio to start...",
            DolphinStatus.noEmu: "Dolphin is running but no game is booted.",
            DolphinStatus.unHooked: "Lost the Dolphin hook. Retrying...",
        }.get(status, f"Dolphin status: {status}"))

    def _check_game_id(self):
        """Warn once if the booted game is not the profile's game.

        Deliberately advisory rather than a gate: the addresses in a profile
        are only meaningful for one game, so a mismatch means the overlay is
        drawing noise — but if this read is ever wrong the producer should
        get a warning in the log, not an overlay that silently never works.
        """
        if self._warned_game_id or not self._expected_game_id:
            return
        try:
            raw = dme.read_bytes(GAME_ID_ADDR, GAME_ID_LEN)
        except RuntimeError:
            return
        booted = raw.decode('ascii', errors='replace')
        if booted != self._expected_game_id:
            self._warned_game_id = True
            print(
                f"Warning: booted game is '{booted}', but this profile is for "
                f"'{self._expected_game_id}'. Controller data will be wrong."
            )

    def _poll_loop(self):
        """Background thread: hook, then read the controller block each tick."""
        next_tick = time.monotonic()

        while self._running:
            if not dme.is_hooked():
                dme.hook()
                if not dme.is_hooked():
                    self.connected = False
                    self._report_status(dme.get_status())
                    time.sleep(HOOK_RETRY_SECONDS)
                    next_tick = time.monotonic()
                    continue
                self._report_status(DolphinStatus.hooked)
                self._warned_game_id = False
                self._check_game_id()

            try:
                block = dme.read_bytes(self._base_addr, self._span)
            except RuntimeError:
                # Game unloaded, or Dolphin went away mid-read.
                dme.un_hook()
                self.connected = False
                self._report_status(dme.get_status())
                continue

            self.connected = True

            with self._lock:
                for port in range(self._port_count):
                    port_base = port * self._stride
                    for field_name, offset in self._field_offsets.items():
                        updater = FIELD_UPDATERS.get(field_name)
                        if updater is None:
                            continue
                        at = port_base + offset
                        updater(
                            self._ports[port],
                            int.from_bytes(block[at:at + 4], 'big'),
                        )

            # Deadline-based so the cadence does not drift with read time.
            next_tick += self._interval
            delay = next_tick - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                next_tick = time.monotonic()
