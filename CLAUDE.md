# GC Overlay

Cross-platform GameCube controller input overlay for OBS, inspired by [M'Overlay](https://github.com/bkacjios/m-overlay).

## Architecture

```
gc-overlay/
├── main.py              # Entry point, CLI args, transport selection, DemoAdapter
├── dolphin_common.py    # Game profile + controller decoding (transport-agnostic)
├── memorywatcher_adapter.py # MemoryWatcher transport (AF_UNIX; macOS, Linux)
├── dme_adapter.py       # Process-memory transport (Windows, Linux)
├── gc_adapter.py        # USB communication with GC adapter via pyusb (--usb mode)
├── server.py            # aiohttp WebSocket + HTTP server (~120Hz broadcast) + /api
├── overlay_settings.py  # Display settings schema, validation, query aliases
├── diag_memorywatcher.py # Diagnostic tool for MemoryWatcher troubleshooting
├── static/
│   └── index.html       # Self-contained SVG overlay (inline CSS/JS)
├── game_profiles/       # Per-game memory address configurations
│   └── mario_superstar_baseball.json
├── requirements.txt     # aiohttp (pyusb only needed for --usb mode)
└── README.md
```

## Input Sources

The app supports multiple input source backends. Each must implement:
- `start()` / `stop()` — lifecycle
- `get_state(port)` — returns dict with `connected`, `buttons`, `stick`, `cstick`, `trigger_l`, `trigger_r`
- `calibrate(port)` — reset stick centers

**The two Dolphin transports are peers, not a primary and a fallback.** Both read
the same emulated memory and share `dolphin_common.py` (profile + `DolphinPort` +
`FIELD_UPDATERS`); they differ only in how bytes are obtained, and which one is
available is a property of the platform. Neither module imports the other — a new
transport is a third peer, never a graft onto one of these two. `main.resolve_transport`
is the one place that decides.

### Source 1: Dolphin MemoryWatcher (memorywatcher_adapter.py) — IMPLEMENTED (default on macOS/Linux)
Dolphin *pushes* memory changes to us over its built-in MemoryWatcher Unix domain socket. Works during Netplay since all players' inputs exist in the local Dolphin process memory. Requires a game profile with the correct memory addresses. No additional drivers or libraries needed beyond aiohttp.

**Not available on Windows, and cannot be made so by a build flag.** Dolphin guards
`MemoryWatcher.cpp` with `if(UNIX)` (and `USE_MEMORYWATCHER` at every call site), and
the class is `sys/socket.h` + `AF_UNIX` + `SOCK_DGRAM` — a type Windows' AF_UNIX does
not support at all. Making it work there means a new transport in Dolphin (named
pipes, as FasterMelee/Ishiiruka did), not a flag.

### Source 1b: Dolphin process memory (dme_adapter.py) — IMPLEMENTED (default on Windows)
We *poll* Dolphin's emulated memory directly, as M'Overlay does on Windows, via the
`dolphin-memory-engine` wheel (a compiled extension vendoring aldelaro5's memory-access
core — a library, **not** the DME GUI app; nothing is installed on the producer's
machine and no Dolphin change is needed). One contiguous read per tick covers every
port. Blocked on macOS, where Dolphin ships a hardened runtime with no `get-task-allow`
entitlement — the reason the MemoryWatcher path exists at all.

Note `DolphinStatus` is **not** re-exported from `dolphin_memory_engine.__init__`;
import it from `dolphin_memory_engine._dolphin_memory_engine`.

### Source 2: Direct USB (gc_adapter.py) — IMPLEMENTED (--usb flag)
Reads the Nintendo WUP-028 GameCube Controller Adapter directly over USB. Works when a physical controller is plugged into the local machine's adapter. Requires pyusb + libusb + GCAdapterDriver (macOS).

### Source 3: Demo Mode (main.py DemoAdapter) — IMPLEMENTED (--demo flag)
Generates animated fake controller data for testing without hardware.

## Tech Stack

- **Python 3.10+** with asyncio
- **aiohttp 3.9+** — async HTTP + WebSocket server
- **pyusb 1.2+** — USB communication, only needed for `--usb` mode (wraps libusb)
- **HTML/CSS/JS** — SVG-based overlay, no build step

## Running

```bash
source venv/bin/activate
python main.py                # Dolphin mode (default), transport auto-selected
python main.py --demo         # Demo mode (no hardware needed)
python main.py --usb          # Direct USB adapter mode (requires pyusb)
python main.py --port 8080    # Custom port
python main.py --controller 2 # Show port 2
python main.py --transport dme # Force a transport (auto|memorywatcher|dme)
python main.py --game mario_superstar_baseball  # Game profile

# Display defaults (per-source overrides go in the URL, see below)
python main.py --bg transparent --no-gear --no-port-label --no-status
```

On the memorywatcher transport, start the overlay before launching Dolphin —
MemoryWatcher connects at game boot and does not retry. The dme transport hooks
whenever Dolphin appears and re-hooks after a restart.

OBS Browser Source URL: `http://localhost:8069?bg=transparent` (512x180)

## Display Settings API

Settings layer: `overlay_settings.DEFAULTS` → CLI flags (server defaults) → URL
query params (per browser source) → `POST /api/settings` (live, all sources).
Ports are 1-indexed everywhere except `adapter.get_state()`, which is 0-indexed.

| Setting | Query param | Values |
|---|---|---|
| `port` | `port` | `1`–`4` |
| `background` | `bg` | `dark`, `transparent` |
| `show_gear` | `gear` | boolean |
| `show_port_label` | `portlabel` | boolean |
| `show_status` | `status` | boolean |
| `show_labels` | `labels` | boolean |

Endpoints (CORS-open on `/api`, server binds `127.0.0.1` only):

| Route | Purpose |
|---|---|
| `GET /api/settings` | `{"settings": {...}, "clients": N}` |
| `POST /api/settings` | Partial patch; updates defaults + pushes to all clients |
| `GET /api/state?port=1` | Same payload the WebSocket broadcasts |
| `POST /api/calibrate?port=1` | Reset that port's stick centers |

`POST /api/settings` applies **only the keys present**, so shared chrome can be
driven without disturbing per-source settings like `port`. Invalid keys/values
return `400` and change nothing.

WebSocket messages are tagged: `{"type": "state", ...}` at ~120Hz and
`{"type": "settings", "settings": {...}}` on connect and on change. The page
forwards its own query string to `/ws` so the server resolves each client's
effective settings in one place. Clients may send `{"settings": {...}}` (scoped
to that client only) or `{"calibrate": true}`.

---

## GC Adapter USB Protocol Reference

### Device Identification
| Adapter | Vendor ID | Product ID |
|---------|-----------|------------|
| Nintendo WUP-028 | `0x057E` | `0x0337` |
| Mayflash (Wii U/Switch mode) | `0x057E` | `0x0337` (same) |

### USB Communication
- **Interface:** 0
- **IN Endpoint:** `0x81` (EP1 IN, interrupt)
- **OUT Endpoint:** `0x02` (EP2 OUT, interrupt)
- **Library:** libusb (via pyusb)

### Commands (sent to OUT endpoint)
| Command | Bytes | Description |
|---------|-------|-------------|
| `0x13` | 1 | Start polling (sent during init) |
| `0x14` | 1 | Stop polling |
| `0x11` | 5 | Set rumble: `[0x11, P0, P1, P2, P3]` (0=off, 1=on) |

### Input Report Format (37 bytes from IN endpoint)
```
Byte 0:    Report type (0x21 = polling data)
Bytes 1-9: Port 0 data
Bytes 10-18: Port 1 data
Bytes 19-27: Port 2 data
Bytes 28-36: Port 3 data
```

### Per-Port Data (9 bytes)
```
Byte 0: Status — bit4=wired controller, bit5=wireless (WaveBird)
Byte 1: Buttons 1
  bit0=A, bit1=B, bit2=X, bit3=Y
  bit4=DPad Left, bit5=DPad Right, bit6=DPad Down, bit7=DPad Up
Byte 2: Buttons 2
  bit0=Start, bit1=Z, bit2=R(digital), bit3=L(digital)
Byte 3: Main Stick X  (0-255, center ~128)
Byte 4: Main Stick Y  (0-255, center ~128)
Byte 5: C-Stick X     (0-255, center ~128)
Byte 6: C-Stick Y     (0-255, center ~128)
Byte 7: L Trigger      (0-255, analog)
Byte 8: R Trigger      (0-255, analog)
```

### macOS-Specific: Kernel Driver Issue
On macOS, the default HID driver (`AppleUSBHIDDriver`) claims the adapter, blocking libusb access. **GCAdapterDriver** (a DriverKit null driver) must be installed to prevent this. Dolphin has the same requirement.

- `libusb_detach_kernel_driver()` does NOT work on macOS without root/entitlements
- Dolphin skips the detach call entirely on macOS (assumes GCAdapterDriver is installed)
- Our code does the same in `gc_adapter.py`

Install: https://github.com/secretkeysio/GCAdapterDriver

### Initialization Sequence (matching Dolphin's GCAdapter.cpp)
1. `usb.core.find(idVendor=0x057E, idProduct=0x0337)`
2. Skip kernel driver detach on macOS
3. `dev.set_configuration()`
4. Find IN/OUT endpoints from interface 0
5. Nyko compatibility: `dev.ctrl_transfer(0x21, 11, 0x0001, 0, None)` (ignore errors)
6. Send `0x13` to OUT endpoint (start polling)
7. Continuously read 37 bytes from IN endpoint

---

## Dolphin Netplay Controller Access (Research)

### The Problem
During Dolphin Netplay, the physical controller may be connected to a remote machine. The local USB adapter has no data. However, Dolphin's local process **does** have all controller data (both local and remote) because Netplay synchronizes inputs between machines.

### How Dolphin Netplay Works
Dolphin netplay is **input-based synchronization**. Both machines run identical emulation state and **only controller inputs are transmitted** over the network. By staying in lockstep, the emulators never diverge (assuming perfect determinism).

- Each player's local controller inputs (port 1) are sent to all other players
- Dolphin assigns one GC controller per player from their local port settings
- Bandwidth can reach ~300 Kbps upload (e.g., Mario Party 5) plus overhead
- **Critical insight**: Since both players' inputs must exist locally for emulation to advance, all controller port data is available in the local Dolphin process memory during netplay

### The Core Netplay Function: GetNetPads()
```cpp
bool NetPlayClient::GetNetPads(int pad_nb, bool from_vi, GCPadStatus* pad_status)
// Located in Source/Core/Core/NetPlayClient.cpp
```

- Called from the CPU thread to retrieve controller input during netplay
- Populates `GCPadStatus` for each pad regardless of local vs remote origin
- Uses `m_pad_map` for controller mapping (which GC port maps to which in-game controller)
- Waits for actual pad data before buffering/sending — prevents wrong calibration from default `GCPadStatus` values

### Where Controller Data Lives in Dolphin

**A. Dolphin host memory (GCPadStatus struct):**
```c
struct GCPadStatus {
    u16 button;       // Bitmask (see below)
    u8 stickX, stickY;       // 0-255, 128 = center
    u8 substickX, substickY; // C-stick
    u8 triggerLeft, triggerRight; // 0-255
    u8 analogA, analogB;     // Analog button pressure
    u8 switches;              // Triforce arcade switches
    bool isConnected;
};
// Button bitmask:
// 0x0001=DLeft, 0x0002=DRight, 0x0004=DDown, 0x0008=DUp
// 0x0010=Z, 0x0020=R, 0x0040=L
// 0x0100=A, 0x0200=B, 0x0400=X, 0x0800=Y, 0x1000=Start
```
Address is NOT stable — changes with every Dolphin build (PIE binary). Populated by `Pad::GetStatus(int pad_num)` in `GCPad.cpp`. During netplay, `GetNetPads()` fills this with local or network-received data.

**B. Emulated GameCube memory (MEM1):**
24MB region (0x1800000 bytes) at virtual address 0x80000000 from the game's perspective. Controller data address is **game-specific** — each game stores PADStatus at a different location determined by where `PADRead()` writes. GameCube memory is **big-endian**, requiring byte swapping for multi-byte values.

**The distinction**: Host memory gives raw input *before* game processing. MEM1 gives data *as the game sees it*, at game-specific addresses.

**M'Overlay's MEM1 controller data format** (from `core.lua` — 12 bytes per port, 0xC stride, 4 ports):

| Offset | Type   | Name              |
|--------|--------|-------------------|
| 0x00   | u16    | buttons.pressed   |
| 0x02   | sbyte  | joystick.x (-128..127) |
| 0x03   | sbyte  | joystick.y        |
| 0x04   | sbyte  | cstick.x          |
| 0x05   | sbyte  | cstick.y          |
| 0x06   | byte   | analog.l (0-255)  |
| 0x07   | byte   | analog.r          |
| 0x0A   | byte   | plugged           |

### How M'Overlay Reads Dolphin Memory (Platform-Specific)
M'Overlay does NOT read USB. It reads **Dolphin's process memory** to extract controller state from the emulated game's RAM.

**Windows** (`memory/windows.lua`):
- Uses `Kernel32.dll` + `Psapi.dll` via LuaJIT FFI
- Enumerates processes to find Dolphin (`Dolphin.exe`, `Slippi Dolphin.exe`, `Project Rio.exe`, etc.)
- Opens process with `PROCESS_VM_OPERATION + PROCESS_VM_READ + PROCESS_QUERY_INFORMATION`
- Scans virtual address space with `VirtualQueryEx` for a 32MB `MEM_MAPPED` region
- Reads via `ReadProcessMemory`

**Linux** (`memory/linux.lua`):
- Scans `/proc/` for process names like `dolphin-emu`, `slippi-r18-netplay`, etc.
- Reads `/proc/<pid>/maps` for `/dev/shm/dolphinmem` shared memory regions >= 32MB
- Uses `process_vm_readv` syscall (requires `CAP_SYS_PTRACE`)

**macOS** (`memory/osx.lua`):
- Uses `proc_listpids` + `proc_pidinfo` to find Dolphin processes
- Attempts `task_for_pid` via Mach kernel APIs
- **`read()` and `write()` return `false` unconditionally** — macOS support is non-functional
- Developer has stated macOS support "probably never will be"

### Dolphin Built-In Interfaces for External Access

#### MemoryWatcher (works on macOS — recommended)
Exports emulated memory changes over a Unix domain socket:
- **Locations file:** `~/Library/Application Support/Dolphin/MemoryWatcher/Locations.txt`
  - One hex address per line (e.g., `80123456`)
  - Supports pointer following: `80ABCD EF` watches `*(0x80ABCD) + 0xEF`
- **Socket:** `~/Library/Application Support/Dolphin/MemoryWatcher/MemoryWatcher` (Unix domain socket)
- **Output format:** Two lines per change — hex address, then hex value
- **Polling rate:** ~2ms (~500Hz)
- Works on stock Dolphin, no code signing or root needed
- **Limitation**: Only reads emulated GameCube memory (not Dolphin host-side data). Requires game-specific memory addresses. Only reports *changes*, not continuous state.

#### Named Pipes Controller Interface (write-only)
- Located at `~/Library/Application Support/Dolphin/Pipes/` on macOS
- Commands: `PRESS A`, `RELEASE B`, `SET MAIN 0.5 0.5`, `SET L 0.7`
- **Write-only** — can SEND inputs to Dolphin but CANNOT READ current controller state

#### Felk's Python Scripting Fork (Windows x64 only)
- `dolphin.controller.get_gc_buttons(controller_id)` returns dict with all button/stick/trigger state
- Cleanest API but not merged into mainline Dolphin, Windows only

#### Slippi Network Protocol (Melee-specific)
- Exposes game state including controller inputs on port 51441
- `libmelee` library connects to read `GameState.player[i].controller_state`
- Melee-specific, won't work for other games without modification

#### Dolphin Built-In Input Display
- "Show Input Display" under Movie/TAS features
- Reads from `Pad::GetStatus()` → OSD formats into `s_InputDisplay` string in `Movie.cpp`
- During netplay, includes **both local and remote** controller data (comes from `GetNetPads()`)
- Generated **inside** Dolphin's process — no external API to access it
- `s_InputDisplay` address changes with every build

### Approaches Ranked by Feasibility for macOS

#### Approach A: MemoryWatcher + Game-Specific Addresses (Best for macOS)
- Works with stock Dolphin, no code signing needed, no process memory access
- Must find game-specific addresses, only reports changes (not continuous state), ~2ms polling
- **Best starting point for implementation**

#### Approach B: Direct Process Memory Reading (dolphin-memory-engine approach)
Uses macOS Mach APIs to read Dolphin's emulated memory directly:
1. Find Dolphin PID via `sysctl(KERN_PROC_ALL)`
2. `task_for_pid()` to get Mach task port
3. `mach_vm_region()` to find MEM1 (24MB region, offset=0, SM_TRUESHARED)
4. `vm_read_overwrite()` to read specific addresses

**Permissions required:**
- Dolphin must be re-signed with `com.apple.security.get-task-allow` entitlement
- The reading process needs root OR the same entitlement
- SIP does NOT need to be fully disabled, but code signing is mandatory
- Must be repeated after each Dolphin update
- Reference: `dolphin-memory-engine/Source/DolphinProcess/Mac/MacDolphinProcess.cpp` (includes `MacSetup.sh` for signing)

#### Approach C: Fork ProjectRio's Dolphin to Export Controller Data
Add a Unix domain socket or TCP server in Dolphin that broadcasts `GCPadStatus` for all 4 ports each frame. Hook into `Pad::GetStatus()` or `GetNetPads()`. Most robust long-term — works at Dolphin host level (not game-specific), gets raw input before game processing. Requires maintaining a Dolphin fork.

#### Approach D: Port Felk's Python Scripting to macOS
Cleanest API (`get_gc_buttons()`) but significant porting effort from Windows x64.

### Project Rio Specifics
Project Rio is a Dolphin fork for Mario Superstar Baseball with:
- Netplay with stat tracking and QoL improvements
- **Auto Golf Mode**: One player gets 0 input delay, other has greater latency
- **Fair Input Delay**: Alternative delay balancing
- Game data tracked in real time, logged to JSON at game end in `Documents/Project Rio/StatFiles/`
- Pre-built with gecko codes for different game modes
- GCAdapter header (`GCAdapter.h`) declares `GCPadStatus Input(int chan)` with notes about button state init on new connections for netplay and CSI device integration

### Game-Specific Memory Addresses
For Mario Superstar Baseball, the controller data addresses in MEM1 need to be found via:
- Dolphin's built-in memory search / dolphin-memory-engine
- Reverse engineering the game's PADRead call site
- Checking existing cheat code databases for known addresses

### Relevant Source Code References
- **Dolphin GCAdapter:** `Source/Core/InputCommon/GCAdapter.cpp` and `GCAdapter.h`
- **GCPadStatus struct:** `Source/Core/InputCommon/GCPadStatus.h`
- **Netplay input sync:** `Source/Core/Core/NetPlayClient.cpp` (`GetNetPads()`)
- **Netplay server:** `Source/Core/Core/NetPlayServer.cpp`
- **Netplay header:** `Source/Core/Core/NetPlayClient.h`
- **MemoryWatcher:** `Source/Core/Core/MemoryWatcher.cpp` and `MemoryWatcher.h`
- **Named Pipes:** `Source/Core/InputCommon/ControllerInterface/Pipes/Pipes.h`
- **macOS memory reading:** `dolphin-memory-engine/Source/DolphinProcess/Mac/MacDolphinProcess.cpp`
- **M'Overlay memory detection:** `m-overlay/source/modules/memory/` (Windows, Linux, and non-functional macOS stubs)
- **M'Overlay game config:** `m-overlay/source/modules/games/core.lua` (controller data struct layout)
- **Dolphin GCPad:** `Source/Core/Core/HW/GCPad.cpp` (input display data source)
- **Dolphin Movie/TAS:** `Source/Core/Core/Movie.cpp` (input display string generation)
- **GameCube SDK pad:** `libogc/gc/ogc/pad.h` (PADStatus struct in game code)

---

## Overlay Design Notes

### SVG-Based Rendering
The overlay is a single `<svg viewBox="0 0 512 180">` scaled to fill the browser
source (`width/height: 100%`, `preserveAspectRatio="xMidYMid meet"`), so 512x180
is a ratio rather than a fixed size. Status text lives inside the SVG so it
scales with everything else.

### Layout (pixel coordinates in 512x180 SVG space)
The D-pad sits in the diagonal wedge below and between the two stick gates
rather than below the main stick, which is what lets the whole overlay fit in
180px instead of 256px. Both gates share a centerline at y=96; the wedge exists
because the octagons taper as they descend, so the horizontal gap between them
widens below the centerline.

| Element | Position | Size |
|---------|----------|------|
| L Trigger | (27, 6) | 130x30 rect, rx 6 |
| R Trigger | (287, 6) | 130x30 rect, rx 6 |
| Z Button | (429, 6) | 56x30 pill, rx 15 |
| Main Stick Gate | center (84, 100) | octagon, circumradius 54 |
| Main Stick Head | center (84, 100) | circle, r=29, travel 23 |
| C-Stick Gate | center (240, 100) | octagon, circumradius 38 |
| C-Stick Head | center (240, 100) | circle, r=20, travel 16 |
| D-Pad | center (170, 138) | plus, 20px arms, 62x62 overall |
| A Button | center (398, 122) | circle, r=36 |
| B Button | center (334, 148) | circle, r=21 |
| X Button | bean right of A | centreline r 60, half-thickness 16, -30° to 10° |
| Y Button | bean above A | centreline r 60, half-thickness 16, -128° to -88° |
| Start | center (302, 112) | circle, r=14 |
| Port label | (502, 172) | text-anchor end |

**L, R and Z share the top row**, with R bumped left (x=287, not 355) so Z can
take the top-right corner. Z used to sit at y=45..73, directly above the X bean
and in the middle of the A cluster's space; moving it into the trigger row is
what freed X to grow.

The A/X/Y cluster is sized to fill the diagonal toward B. X straddles A's
centerline (rather than sitting entirely above it) so the group reads as a
diagonal band running from Z down to B.

With Z out of the way, **the Y bean under the top row is what pins A's y.** Y's
topmost point is at angle -90°, which its span includes, so:

```
Y top    = A.y - arc_radius - stroke/2
arc_radius = A.r + 2 (A's half-stroke) + 5 (gap) + stroke/2

=> Y top = A.y - A.r - 7 - stroke
```

Y must clear the trigger row's visual bottom of 37.75 by ~6, so:

```
Y visual top = A.y - R - h - 2  =  A.y - A.r - 2h - gap - 6
A.y >= A.r + 2h + gap + 49.75
```

At A.r=36, h=16, gap=4 that floor is 121.75; A sits at 122, i.e. 0.25 of slack
(the measured R-to-Y clearance is 6.25 against a 6 baseline). **Every increase
to A's radius or the bean thickness raises that floor by one or two px
respectively**, and A cannot absorb it by moving down — B's visual bottom is
already 171 of 180. Note the top row spans the full width, so no amount of
shuffling L/R/Z lifts this limit; only a shorter Y span that excludes -90°
would.

There is now ~37px of slack between Z's bottom and the X bean's top cap, since
X no longer has anything above it. Rotating X's span upward (e.g. -45°..-5°)
would fill it, at the cost of X no longer straddling A's centerline.

### Regular Octagon Computation
**Orientation matters and is easy to get wrong.** The gates have vertices on
the cardinals and the diagonals (angles `i x 45°`), so there is a point at the
top, bottom, left and right — these are the notches of a real GC gate, and this
is the look PRSH ships. The earlier source wrote flat-side points and then
applied `transform="rotate(22.5 cx cy)"` to land on the same orientation; the
vertices are now baked into the `points` attribute instead, so what you read is
what renders. Do not "simplify" these back to a flat-top octagon.

Because the vertices are on the cardinals, the bounding box is `2 x
circumradius` (not `2 x apothem`), which is what sets the vertical clearance
against the trigger bars.

```
Vertex i = (cx + r x cos(45i°), cy + r x sin(45i°)), i = 0..7

Main (cx=84, cy=100, r=54):
  "138,100 122.2,138.2 84,154 45.8,138.2 30,100 45.8,61.8 84,46 122.2,61.8"
C-Stick (cx=240, cy=100, r=38):
  "278,100 266.9,126.9 240,138 213.1,126.9 202,100 213.1,73.1 240,62 266.9,73.1"
```

The gates sit at cy=100 rather than higher because the main gate's top vertex
has to clear the L trigger: at cy=96 with the 30px-tall triggers that gap was
only 2.25px. Dropping both gates 4px opens it to 6.25 without pushing the D-pad
(which follows the wedge between them) past the bottom edge.

### Stick Head vs Travel
`head_radius + travel` is the invariant: it's the radius the head's edge sweeps
at full deflection, and it alone decides containment. It is set to the midpoint
between the gate's notches (`circumradius`) and its flats
(`apothem = circumradius x cos(22.5°)`):

| | circumradius | apothem | head + travel |
|---|---|---|---|
| Main | 54 | 49.9 | **52** (r 29 + 23) |
| C-stick | 38 | 35.1 | **36** (r 20 + 16) |

So **making the head bigger costs travel one-for-one** — trade within the sum
and containment is untouched; raise the sum and the head starts breaking the
gate line. Head diameter is ~54% of gate width at these values, matching the
proportion PRSH ships.

At the flats (22.5° off a cardinal) the head's edge sits ~1.4px past the gate
stroke, but only for magnitude-1.0 input. A real gate limits magnitude to
`cos(22.5°) = 0.924` there, which pulls the head back inside; the poke is only
visible in `--demo`, whose stick traces a true circle.

### D-Pad Construction
A single `.dpad-outline` plus-shaped path — plain, square inner corners — sits
over four fill-only `.dpad-arm` polygons. Each arm polygon is the whole arm
*plus a 45° point into the middle*, so a held direction reads as an arrow aimed
at the centre:

```
up:    160,107  180,107  180,128  170,138  160,128
down:  160,169  180,169  180,148  170,138  160,148
left:  139,128  139,148  160,148  170,138  160,128
right: 201,128  201,148  180,148  170,138  180,128
```

The four points meet exactly at the centre (170,138) and tile the centre square,
so a diagonal press joins into one clean L with a diagonal seam and no dark hole.
The 45° belongs to the **fill only** — do not chamfer the grey outline.

### X/Y Bean Construction
X and Y are **outlined** capsules, not thick stroked arcs, so they fill on press
like A/B/Z/Start. A stroked arc gives you the capsule's *silhouette* and can
only change colour; an outline needs the capsule's actual boundary as a closed
path.

Given A's centre `C`, centreline radius `R=60`, half-thickness `h=16`, and a
span `θ1..θ2`, the boundary is four arcs — outer, end cap, inner (reversed),
start cap:

```
M   outer(θ1)                          outer(θ) = C + (R+h)·(cos θ, sin θ)
A   R+h R+h 0 0 1  outer(θ2)           inner(θ) = C + (R-h)·(cos θ, sin θ)
A   h   h   0 0 1  inner(θ2)           cap radius = h, sweep 1
A   R-h R-h 0 0 0  inner(θ1)           inner arc runs backwards => sweep 0
A   h   h   0 0 1  outer(θ1)  Z
```

- **X** (-30°..10°): `M 463.82 84.0 A 76 76 0 0 1 472.85 135.2 A 16 16 0 0 1 441.33 129.64 A 44 44 0 0 0 436.11 100.0 A 16 16 0 0 1 463.82 84.0 Z`
- **Y** (-128°..-88°): `M 351.21 62.11 A 76 76 0 0 1 400.65 46.05 A 16 16 0 0 1 399.54 78.03 A 44 44 0 0 0 370.91 87.33 A 16 16 0 0 1 351.21 62.11 Z`

Labels still sit at the centreline midpoint angle (radius `R`, not `R±h`).

The 40° span is the intended bean *length* — widen these by raising `h`, not by
extending the span. `R` is derived, not free:

```
R = A.r + 4 + gap + h      (4 = A's half-stroke + the bean's half-stroke)
```

so `h` and `R` move together. At A.r=36, gap=4, h=16 that gives R=60.

Endpoints are `(cx + r x cos θ, cy + r x sin θ)`. Both spans are under 180°, so
large-arc-flag is 0; both run clockwise in SVG's y-down space, so sweep-flag
is 1.

### Stroke Weights
Strokes are deliberately heavy so the overlay stays legible when scaled down
into a stream layout: gates and button shapes 4, D-pad outline 4, trigger
outline and Start 3.5, stick heads 2.5, X/Y bean outlines 4.

### Trigger Fill
The bar shows the analog value, and the **digital click bottoms the bar out** —
`b.l ? 1 : state.trigger_l`. There is no separate click marker; a real trigger is
fully depressed when the click fires, so a full bar is the honest reading. (An
earlier build drew a 7px sliver at the inner end for the digital bit.)

### Verifying Clearances
`getBBox()` comparisons **lie** here: a square box around an octagon corner or a
curved bean reports overlap where the shapes are comfortably apart (the A/X and
D-pad/gate pairs both read as -3 by bbox while actually holding 5px and 7.7px).
Measure real geometry instead — sample both outlines with `getPointAtLength()`,
take the minimum pairwise distance, and subtract each element's half stroke.

### No Alpha
**Every shape is fully opaque.** No `opacity`, `fill-opacity`, `stroke-opacity`,
`rgba()`, or 8-digit hex anywhere in the SVG — a partially transparent shape
composites against whatever is behind the browser source in OBS and shifts
colour per scene. `fill: none` / `stroke: none` are fine: those paint nothing
rather than painting something translucent. The only intentional transparency
is the page background under `background: transparent`.

### Color Scheme (all fully opaque)
| Element | Unpressed | Pressed |
|---------|-----------|---------|
| A | stroke #00E196 | fill #00E196 |
| B | stroke #E63E3E | fill #E63E3E |
| X bean | stroke #AAAAAA | fill #FFFFFF |
| Y bean | stroke #AAAAAA | fill #FFFFFF |
| Z | stroke #B36CD6 | fill #B36CD6 |
| Start | stroke #AAAAAA | fill #FFFFFF |
| D-pad | stroke #AAAAAA | arm fill #FFFFFF |
| Main gate | stroke #AAAAAA | — |
| C-stick gate | stroke #B8960F | — |
| Main stick | fill #FFFFFF | — |
| C-stick | fill #FFD43B | — |
| Triggers | fill #FFFFFF | — |

Pressed labels use a very dark tint of their own hue rather than the page
background, so they stay readable on a transparent background.

All glyphs (A/B/X/Y/Z/ST/L/R) carry `.btn-label`, `.arc-label` or
`.trigger-label`, and the `show_labels` setting hides them by toggling
`.no-labels` on the SVG root. The port label and the status text are
deliberately not covered — they have their own settings.
