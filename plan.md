# Implementation Plan: Dolphin Netplay Support via MemoryWatcher

## Goal
Add a `DolphinAdapter` input source that reads controller state from Dolphin (specifically Project Rio) using Dolphin's built-in **MemoryWatcher** Unix socket. This enables the overlay to work during Netplay, where the physical controller may be on a remote machine but Dolphin's emulated memory contains all players' input data locally.

---

## Background: How MemoryWatcher Works

1. **Locations file**: User places hex addresses (one per line) in
   `~/Library/Application Support/<Dolphin variant>/MemoryWatcher/Locations.txt`
2. **Socket**: Dolphin writes changes to a Unix domain socket at
   `~/Library/Application Support/<Dolphin variant>/MemoryWatcher/MemoryWatcher`
3. **Format**: Two lines per change — hex address, then hex value
4. **Rate**: ~500Hz polling (~2ms), only reports *changes* (not continuous state)
5. **Data**: Reads emulated GameCube memory (MEM1) — big-endian, game-specific addresses

### Controller Data in GC Memory (M'Overlay format)
12 bytes per port, 0xC stride, 4 ports contiguous:

| Offset | Type    | Field                        |
|--------|---------|------------------------------|
| 0x00   | u16 BE  | buttons bitmask              |
| 0x02   | sbyte   | stick X (-128..127)          |
| 0x03   | sbyte   | stick Y (-128..127)          |
| 0x04   | sbyte   | C-stick X (-128..127)        |
| 0x05   | sbyte   | C-stick Y (-128..127)        |
| 0x06   | byte    | L trigger (0-255)            |
| 0x07   | byte    | R trigger (0-255)            |
| 0x0A   | byte    | plugged (nonzero = connected) |

Button bitmask: `0x0001=DLeft, 0x0002=DRight, 0x0004=DDown, 0x0008=DUp, 0x0010=Z, 0x0020=R, 0x0040=L, 0x0100=A, 0x0200=B, 0x0400=X, 0x0800=Y, 0x1000=Start`

---

## Step 1: Create Game Address Configuration (`game_profiles/`)

**New file: `game_profiles/mario_superstar_baseball.json`**

```json
{
  "game_id": "GYQE01",
  "game_name": "Mario Superstar Baseball (NTSC-U)",
  "controller_base": null,
  "controller_stride": 12,
  "port_count": 4,
  "format": "padstatus",
  "notes": "Address TBD - must be discovered with dolphin-memory-engine"
}
```

This file defines the memory layout for a game. The `controller_base` address is the main piece that needs to be discovered per-game. The stride (0xC = 12) and format are standard across all GC games that use `PADRead()`.

**Why a file?** Addresses are game-specific and may differ between game revisions. A profile system lets users share and contribute addresses without code changes.

---

## Step 2: Create `dolphin_adapter.py`

**New file: `dolphin_adapter.py`** — The core adapter implementing the standard interface.

### Class: `DolphinAdapter`

**Constructor parameters:**
- `game_profile_path` — path to the game JSON profile
- `dolphin_dir` — override Dolphin config directory (auto-detected if None)

**Key design decisions:**

### 2a. Config path auto-detection
```python
DOLPHIN_DIRS = [
    "~/Library/Application Support/Project Rio",
    "~/Library/Application Support/Slippi Dolphin",
    "~/Library/Application Support/Dolphin",
]
```
Search in priority order. Use the first one that exists, or accept `--dolphin-dir` override.

### 2b. Locations.txt auto-generation
On `start()`, compute the addresses to watch based on the game profile's `controller_base` address and write them into the correct `Locations.txt`. For 4 ports × 12 bytes each, we watch every byte of controller data:

```
80XXYY00   (port 0 buttons high byte)
80XXYY01   (port 0 buttons low byte)
80XXYY02   (port 0 stick X)
...through all 4 ports...
```

Actually, MemoryWatcher watches 32-bit (4-byte) aligned words. So for 12 bytes per port × 4 ports = 48 bytes, we need to watch addresses at 4-byte intervals: `base`, `base+4`, `base+8`, `base+C`, `base+10`, ... `base+2C`. That's 12 addresses total (48 bytes / 4 bytes each).

### 2c. Socket reader (background thread)
A daemon thread that:
1. Connects to the MemoryWatcher Unix domain socket
2. Reads line pairs (address, value)
3. Maps each address back to which port/field it belongs to
4. Updates internal controller state
5. Reconnects automatically if the socket disconnects (Dolphin restart)

### 2d. State maintenance
Since MemoryWatcher only reports **changes**, we maintain a `_state` dict for each of the 4 ports, initialized to "disconnected + zeroed." Each incoming change updates the relevant field. `get_state(port)` returns a snapshot.

### 2e. Thread safety
Same pattern as `GCAdapter`: use `threading.Lock` around state reads/writes since the socket reader runs in a thread but `get_state()` is called from asyncio at 120Hz.

### 2f. Parsing logic

When a value comes in for a watched address:
1. Compute `offset = address - controller_base`
2. `port = offset // 12`, `field_offset = offset % 12`
3. MemoryWatcher reports 4 bytes at a time (big-endian), so a single report may span multiple fields
4. Parse the 4-byte value and update the relevant fields for that port

**Translation to normalized format:**
- Buttons (u16 BE bitmask) → individual bools via bit tests
- Stick X/Y (sbyte -128..127) → float -1.0..1.0 by dividing by 128 (or by ~80 for the physical range)
- C-stick X/Y (sbyte) → same normalization
- Triggers (byte 0-255) → float 0.0..1.0 by dividing by 255
- Plugged (byte) → `connected: bool`

### Pseudocode structure:

```python
class DolphinAdapter:
    def __init__(self, game_profile, dolphin_dir=None):
        self.connected = False          # True once socket is open
        self._controllers = [PortState() for _ in range(4)]
        self._lock = threading.Lock()
        self._running = False
        self._thread = None
        self._socket_path = ...         # Resolved from dolphin_dir
        self._locations_path = ...
        self._profile = load_profile(game_profile)
        self._base_addr = self._profile['controller_base']
        self._addr_map = {}             # address → (port, field_offset)

    def start(self):
        if self._base_addr is None:
            print("No controller address configured. Run --dolphin-scan to find it.")
            return
        self._write_locations_file()
        self._build_addr_map()
        self._running = True
        self._thread = Thread(target=self._reader_loop, daemon=True)
        self._thread.start()

    def stop(self): ...
    def get_state(self, port=0): ...     # Lock + return snapshot dict
    def calibrate(self, port=0): ...     # Reset stick centers

    def _reader_loop(self):
        while self._running:
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
                sock.connect(self._socket_path)
                self.connected = True
                buffer = b""
                while self._running:
                    data = sock.recv(4096)
                    buffer += data
                    # Parse complete line pairs from buffer
                    self._process_buffer(buffer)
            except (ConnectionError, FileNotFoundError):
                self.connected = False
                time.sleep(1)  # Retry

    def _process_buffer(self, buffer):
        # Split into lines, take pairs, parse address+value
        ...

    def _update_from_memory(self, address, value_bytes):
        port, offset = self._addr_map.get(address, (None, None))
        if port is None: return
        with self._lock:
            self._controllers[port].update_field(offset, value_bytes)
```

---

## Step 3: Address Discovery Helper (`--dolphin-scan`)

**Problem:** The controller data base address in GC memory is game-specific and not known in advance for Mario Superstar Baseball.

**New CLI mode: `python main.py --dolphin-scan`**

This guided workflow helps the user find the correct address:

1. Start listening on the MemoryWatcher socket
2. Prompt the user: "In Dolphin, press and release the A button on port 1"
3. Watch for any address whose value changes to contain `0x0100` (A button bitmask) and back to `0x0000`
4. Candidate addresses are presented to the user
5. Validate by asking for another button press (B = `0x0200`)
6. Once confirmed, compute `controller_base` and save to the game profile

**Alternative approach:** If scanning the full address space is too slow via MemoryWatcher (since it only reports addresses you're already watching), the workflow would be:

1. Instruct the user to use **dolphin-memory-engine** to search for the A button bitmask value
2. Narrow down candidates with successive button presses
3. Enter the found base address into the config: `python main.py --dolphin-set-addr 80XXYYZZ`

This is a one-time setup per game. The address is saved to the game profile JSON.

---

## Step 4: CLI Integration in `main.py`

Add new arguments:

```python
parser.add_argument('--dolphin', action='store_true',
    help='Read from Dolphin via MemoryWatcher (for Netplay)')
parser.add_argument('--dolphin-dir', type=str, default=None,
    help='Override Dolphin config directory path')
parser.add_argument('--game', type=str, default='mario_superstar_baseball',
    help='Game profile name (default: mario_superstar_baseball)')
parser.add_argument('--dolphin-set-addr', type=str, default=None,
    help='Set the controller base address for the current game profile (hex)')
```

Adapter selection becomes:
```python
if args.demo:
    adapter = DemoAdapter()
elif args.dolphin:
    profile_path = f"game_profiles/{args.game}.json"
    adapter = DolphinAdapter(profile_path, dolphin_dir=args.dolphin_dir)
else:
    adapter = GCAdapter()
```

---

## Step 5: Update `requirements.txt`

No new dependencies needed — `socket` and `json` are stdlib. The MemoryWatcher connection uses standard Unix domain sockets.

---

## Step 6: Find Mario Superstar Baseball Controller Addresses

This is **manual research work** that must happen alongside or after the code is written:

### Option A: Use dolphin-memory-engine
1. Install dolphin-memory-engine (available for macOS)
2. Launch Project Rio with Mario Superstar Baseball
3. Start a game to ensure controller data is being written
4. Search for known button patterns (press A → search for bytes containing 0x01 in the button field)
5. Narrow down by pressing different buttons
6. The base address found gets saved to the game profile

### Option B: Look for existing addresses
- Check M'Overlay's game configs for Mario Superstar Baseball
- Search cheat code databases (GeckoCodes) for related addresses
- Check Project Rio community resources

### Option C: Derive from PADRead
- Use Dolphin's debugger to find where `PADRead()` writes its results
- Set a memory breakpoint on a known PAD function address
- Trace to the output buffer location

Once found, update `game_profiles/mario_superstar_baseball.json` with the `controller_base` value.

---

## Implementation Order

| Phase | What | Files | Blocked by |
|-------|------|-------|------------|
| **1** | Game profile JSON schema + MSB stub | `game_profiles/mario_superstar_baseball.json` | Nothing |
| **2** | DolphinAdapter core class | `dolphin_adapter.py` | Nothing |
| **3** | MemoryWatcher socket reader + parser | `dolphin_adapter.py` | Phase 2 |
| **4** | CLI integration | `main.py` | Phases 1-3 |
| **5** | Address discovery tooling | `dolphin_adapter.py` or separate script | Phase 3 |
| **6** | Find actual MSB addresses | Manual research | Phases 2-3 (to validate) |
| **7** | Test end-to-end with Project Rio | — | Phases 4+6 |

Phases 1-4 can be coded now. Phase 5 is the discovery UX. Phase 6 is the research/detective work of finding the actual memory address for Mario Superstar Baseball. Phase 7 is integration testing with real hardware.

---

## Open Questions / Risks

1. **MemoryWatcher socket type**: Is it `SOCK_DGRAM` or `SOCK_STREAM`? Dolphin source says it's a Unix datagram socket — need to verify for Project Rio's fork.

2. **Project Rio config path**: Need to verify exact path on macOS. Could be `~/Library/Application Support/Project Rio/`, `~/Library/Application Support/Dolphin/` (if they share config), or something else.

3. **MemoryWatcher availability in Project Rio**: The feature exists in stock Dolphin but Project Rio could have removed or modified it. Need to verify it's present.

4. **4-byte alignment**: MemoryWatcher watches 32-bit words. The controller data struct (12 bytes) doesn't align perfectly on 4-byte boundaries across ports. Need to handle partial field coverage in reported values.

5. **Address stability**: The game-specific address should be stable across runs of the same game version, but may differ between NTSC-U, PAL, and JP versions of the game.

6. **M'Overlay uses pointer indirection**: Some games store controller data behind a pointer (e.g., `*(base_ptr) + offset`). MemoryWatcher supports this syntax. Need to check if MSB uses a stable direct address or requires pointer following.
