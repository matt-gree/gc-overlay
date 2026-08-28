"""MemoryWatcher transport: Dolphin pushes memory changes to us.

Reads controller input from Dolphin's emulated GameCube memory via the
built-in MemoryWatcher Unix domain socket. Works during Netplay since all
players' inputs exist in the local Dolphin process memory.

One of two peer adapters (see dolphin_common), which owns the profile and the
parsing. This one owns only the transport, and its constraint is Dolphin's:
MemoryWatcher.cpp is guarded by ``if(UNIX)`` in Dolphin's build, so it does
not exist at all in a Windows build. On Windows use ``dme_adapter``.

Requires:
  - A game profile with the controller_base address and field_offsets set
  - Dolphin's MemoryWatcher Locations.txt configured with the right addresses
  - Dolphin running with the game loaded

Protocol:
  - MemoryWatcher uses AF_UNIX SOCK_DGRAM
  - Our code binds to the socket path; Dolphin sends datagrams to it
  - Each datagram: "address\nvalue\n" pairs in hex (u32 big-endian logical order)
"""

import os
import socket
import threading

from dolphin_common import (
    FIELD_BUTTONS,
    FIELD_PLUGGED,
    FIELD_STICKS,
    FIELD_TRIGGERS,
    FIELD_UPDATERS,
    DolphinPort,
    load_game_profile,
)

# Standard Dolphin config directories on macOS, checked in priority order
DOLPHIN_DIRS = [
    os.path.expanduser("~/Library/Application Support/Project Rio"),
    os.path.expanduser("~/Library/Application Support/ProjectRio"),
    os.path.expanduser("~/Library/Application Support/Slippi Dolphin"),
    os.path.expanduser("~/Library/Application Support/Dolphin"),
]

def find_dolphin_dir(override=None):
    """Find the Dolphin/Project Rio config directory."""
    if override:
        expanded = os.path.expanduser(override)
        if os.path.isdir(expanded):
            return expanded
        print(f"Warning: specified Dolphin dir does not exist: {expanded}")
        return None

    for d in DOLPHIN_DIRS:
        if os.path.isdir(d):
            return d

    return None

class MemoryWatcherAdapter:
    """Reads controller state from Dolphin via MemoryWatcher.

    MemoryWatcher is a built-in Dolphin feature that watches emulated
    GameCube memory addresses and reports changes over a Unix domain socket.

    The game profile defines which memory addresses to watch and how to
    parse them. Different games store controller data at different addresses
    and struct layouts.

    Usage:
        adapter = MemoryWatcherAdapter("game_profiles/mario_superstar_baseball.json")
        adapter.start()
        state = adapter.get_state(0)  # port 0
        adapter.stop()
    """

    def __init__(self, profile_path, dolphin_dir=None):
        self._profile_path = profile_path
        self._profile = load_game_profile(profile_path)
        self._dolphin_dir = find_dolphin_dir(dolphin_dir)
        self._base_addr = self._profile.get('controller_base')
        self._port_count = self._profile.get('port_count', 4)
        self._stride = self._profile.get('controller_stride', 12)

        # Field offsets within each port's struct (byte offsets from port base)
        self._field_offsets = self._profile.get('field_offsets', {
            FIELD_BUTTONS: 0,
            FIELD_PLUGGED: 8,
            FIELD_STICKS: 16,
            FIELD_TRIGGERS: 20,
        })

        # Per-game normalization constants
        norm = self._profile.get('normalization', {})
        stick_range = norm.get('stick_range', 80)
        cstick_range = norm.get('cstick_range', 80)
        trigger_max = norm.get('trigger_max', 200)

        self.connected = False
        self._ports = [
            DolphinPort(stick_range, cstick_range, trigger_max)
            for _ in range(4)
        ]
        self._lock = threading.Lock()
        self._running = False
        self._thread = None

        self._got_data = False

        # Map: integer address → (port_index, field_name)
        self._addr_map = {}

    def start(self):
        """Start the MemoryWatcher socket reader."""
        if self._dolphin_dir is None:
            print(
                "\nCould not find Dolphin config directory.\n"
                "Checked:\n" +
                "\n".join(f"  {d}" for d in DOLPHIN_DIRS) +
                "\n\nUse --dolphin-dir to specify the path.\n"
            )
            return

        if self._base_addr is None:
            print(
                "\nNo controller base address configured for this game.\n"
                "Use --dolphin-set-addr <hex_address> to set it.\n"
                "Example: python main.py --dolphin-set-addr 803C77B8\n"
            )
            return

        # Parse base address
        if isinstance(self._base_addr, str):
            self._base_addr_int = int(self._base_addr, 16)
        else:
            self._base_addr_int = self._base_addr

        self._build_addr_map()
        self._write_locations_file()

        self._running = True
        self._thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the socket reader and clear watched addresses."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        self._clear_locations_file()

    def get_state(self, port=0):
        """Get controller state for a port (thread-safe)."""
        with self._lock:
            return self._ports[port].to_dict()

    def get_all_states(self):
        """Get states for all 4 ports."""
        with self._lock:
            return [p.to_dict() for p in self._ports]

    def calibrate(self, port=0):
        """No-op for Dolphin adapter (game handles calibration)."""
        pass

    def _build_addr_map(self):
        """Build mapping from watched addresses to (port, field_name).

        Uses field_offsets from the game profile to determine which
        addresses to watch for each port. Each field is a u32 read.
        """
        self._addr_map = {}
        for port in range(self._port_count):
            port_base = self._base_addr_int + (port * self._stride)
            for field_name, offset in self._field_offsets.items():
                addr = port_base + offset
                self._addr_map[addr] = (port, field_name)

    def _write_locations_file(self):
        """Write the MemoryWatcher Locations.txt to all found Dolphin dirs.

        Writes to every known Dolphin config directory (not just the primary
        one) so that any Dolphin variant picks up the addresses.
        """
        lines = []
        for addr in sorted(self._addr_map.keys()):
            lines.append(f"{addr:08X}")
        content = "\n".join(lines) + "\n"

        # Write to all Dolphin directories that exist
        all_dirs = [self._dolphin_dir]
        for d in DOLPHIN_DIRS:
            if os.path.isdir(d) and d != self._dolphin_dir:
                all_dirs.append(d)

        for dolphin_dir in all_dirs:
            mw_dir = os.path.join(dolphin_dir, "MemoryWatcher")
            os.makedirs(mw_dir, exist_ok=True)

            locations_path = os.path.join(mw_dir, "Locations.txt")

            # Only write if changed
            existing = ""
            if os.path.exists(locations_path):
                with open(locations_path, 'r') as f:
                    existing = f.read()

            if existing != content:
                with open(locations_path, 'w') as f:
                    f.write(content)
                print(f"Wrote MemoryWatcher locations to: {locations_path}")
            else:
                print(f"MemoryWatcher locations already configured: {locations_path}")

        # Log the addresses being watched
        print(f"Watching {len(self._addr_map)} addresses "
              f"({len(self._field_offsets)} fields x {self._port_count} ports)")

    def _clear_locations_file(self):
        """Clear Locations.txt in all Dolphin dirs so MemoryWatcher stops watching.

        This prevents Dolphin from trying to resolve game-specific addresses
        when the overlay isn't running (which causes error popups).
        """
        all_dirs = [self._dolphin_dir]
        for d in DOLPHIN_DIRS:
            if os.path.isdir(d) and d != self._dolphin_dir:
                all_dirs.append(d)

        for dolphin_dir in all_dirs:
            locations_path = os.path.join(dolphin_dir, "MemoryWatcher", "Locations.txt")
            if os.path.exists(locations_path):
                with open(locations_path, 'r') as f:
                    if f.read().strip():  # Only clear if non-empty
                        with open(locations_path, 'w') as fw:
                            fw.write("")
                        print(f"Cleared MemoryWatcher locations: {locations_path}")

    def _get_all_socket_paths(self):
        """Get MemoryWatcher socket paths for all known Dolphin dirs."""
        paths = []
        seen = set()
        for d in [self._dolphin_dir] + DOLPHIN_DIRS:
            if d and os.path.isdir(d) and d not in seen:
                seen.add(d)
                paths.append(os.path.join(d, "MemoryWatcher", "MemoryWatcher"))
        return paths

    def _reader_loop(self):
        """Background thread: bind all MemoryWatcher sockets and read changes.

        Binds a socket in every known Dolphin config directory so that
        whichever Dolphin variant the user launches will find a listener.
        Uses select() to read from all sockets simultaneously.
        """
        import select

        socket_paths = self._get_all_socket_paths()
        socks = []

        try:
            for socket_path in socket_paths:
                # Ensure directory exists
                mw_dir = os.path.dirname(socket_path)
                os.makedirs(mw_dir, exist_ok=True)

                # Clean up stale socket
                if os.path.exists(socket_path):
                    try:
                        os.unlink(socket_path)
                    except OSError:
                        pass

                sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
                sock.setblocking(False)
                sock.bind(socket_path)
                socks.append((sock, socket_path))
                print(f"MemoryWatcher socket ready: {socket_path}")

            self.connected = True
            print("Waiting for Dolphin to send controller data...")

            while self._running:
                # Wait for data on any socket (1s timeout for shutdown check)
                readable, _, _ = select.select(
                    [s for s, _ in socks], [], [], 1.0
                )
                for sock in readable:
                    try:
                        data = sock.recv(4096)
                        if data:
                            self._process_datagram(data)
                    except OSError as e:
                        if self._running:
                            print(f"Socket read error: {e}")

        except OSError as e:
            if self._running:
                print(f"MemoryWatcher socket error: {e}")
        finally:
            self.connected = False
            for sock, socket_path in socks:
                try:
                    sock.close()
                except OSError:
                    pass
                try:
                    os.unlink(socket_path)
                except OSError:
                    pass

    def _process_datagram(self, data):
        """Parse a MemoryWatcher datagram and update port state.

        Format: "address\nvalue\n" pairs, both in hex.
        A single datagram may contain multiple address/value pairs.
        """
        # Strip null bytes (some Dolphin forks send \x00 padding) and whitespace
        text = data.replace(b'\x00', b'').decode('ascii', errors='ignore').strip()
        if not text:
            return

        if not self._got_data:
            self._got_data = True
            print("Receiving controller data from Dolphin.")

        lines = text.split('\n')

        # Process pairs of lines: address, value
        i = 0
        while i + 1 < len(lines):
            addr_str = lines[i].strip()
            val_str = lines[i + 1].strip()
            i += 2

            if not addr_str or not val_str:
                continue

            # Some Dolphin forks format hex values with commas (e.g. "1,000,100")
            val_str = val_str.replace(',', '')

            try:
                addr = int(addr_str, 16)
                value = int(val_str, 16)
            except ValueError:
                continue

            lookup = self._addr_map.get(addr)
            if lookup is None:
                continue

            port_idx, field_name = lookup
            updater = FIELD_UPDATERS.get(field_name)
            if updater is None:
                continue

            with self._lock:
                updater(self._ports[port_idx], value)
