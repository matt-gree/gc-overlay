# GC Overlay

macOS-compatible GameCube controller input overlay for OBS, inspired by [M'Overlay](https://github.com/bkacjios/m-overlay).

## Architecture

```
gc-overlay/
├── main.py              # Entry point, CLI args, DemoAdapter for --demo mode
├── dolphin_adapter.py   # Dolphin MemoryWatcher adapter (default mode)
├── gc_adapter.py        # USB communication with GC adapter via pyusb (--usb mode)
├── server.py            # aiohttp WebSocket + HTTP server (~120Hz broadcast)
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

### Source 1: Dolphin MemoryWatcher (dolphin_adapter.py) — IMPLEMENTED (default)
Reads controller state from Dolphin's emulated GameCube memory via the built-in MemoryWatcher Unix domain socket. Works during Netplay since all players' inputs exist in the local Dolphin process memory. Requires a game profile with the correct memory addresses. No additional drivers or libraries needed beyond aiohttp.

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
python main.py                # Dolphin mode (default)
python main.py --demo         # Demo mode (no hardware needed)
python main.py --usb          # Direct USB adapter mode (requires pyusb)
python main.py --port 8080    # Custom port
python main.py --controller 2 # Show port 2
python main.py --game mario_superstar_baseball  # Game profile
```

Start the overlay before launching Dolphin. MemoryWatcher connects at game boot.

OBS Browser Source URL: `http://localhost:8069?bg=transparent` (512x256)

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
The overlay uses a single `<svg viewBox="0 0 512 256">` with all elements as SVG shapes. This gives precise positioning and crisp rendering at any scale.

### Button Layout (pixel coordinates in 512x256 SVG space)
| Element | Position | Size |
|---------|----------|------|
| L Trigger | (30, 14) | 125x22 rect |
| R Trigger | (357, 14) | 125x22 rect |
| Main Stick Gate | center (95, 122) | octagon, r=55 |
| Main Stick Head | center (95, 122) | circle, r=20 |
| C-Stick Gate | center (235, 122) | octagon, r=40 |
| C-Stick Head | center (235, 122) | circle, r=15 |
| D-Pad | center (95, 218) | cross, 16px arms |
| A Button | center (390, 118) | circle, r=26 |
| B Button | center (338, 162) | circle, r=15 |
| X Button | arc right of A | radius 37, -55° to 55°, stroke 14px |
| Y Button | arc above A | radius 37, -145° to -35°, stroke 14px |
| Z Button | (426, 46) | 50x26 pill |
| Start | center (308, 130) | circle, r=11 |

### Regular Octagon Computation
For flat-top orientation, vertices at angles -67.5° + i×45° (i=0..7):
```
Main (cx=95, cy=122, r=55):  "116,71 146,101 146,143 116,173 74,173 44,143 44,101 74,71"
C-Stick (cx=235, cy=122, r=40): "250,85 272,107 272,137 250,159 220,159 198,137 198,107 220,85"
```

### X/Y Bean Arc Computation
Both arcs centered on A button (390, 118) at radius 37 with stroke-width 14 and round linecaps:
- **X (right of A):** `M 411.2 87.7 A 37 37 0 0 1 411.2 148.3` — spans -55° to 55°
- **Y (above A):** `M 359.7 96.8 A 37 37 0 0 1 420.3 96.8` — spans -145° to -35°
- Inner edge of stroke is ~3px from A's circle (radius 37 - 7 = 30; A radius = 26)

### Color Scheme (all fully opaque)
| Element | Unpressed | Pressed |
|---------|-----------|---------|
| A | stroke #00E196 | fill #00E196 |
| B | stroke #E63E3E | fill #E63E3E |
| X arc | stroke #999999 | stroke #FFFFFF |
| Y arc | stroke #999999 | stroke #FFFFFF |
| Z | stroke #B36CD6 | fill #B36CD6 |
| Start | stroke #AAAAAA | fill #FFFFFF |
| D-pad | stroke #AAAAAA | fill #FFFFFF |
| Main gate | stroke #AAAAAA | — |
| C-stick gate | stroke #B8960F | — |
| Main stick | fill #FFFFFF | — |
| C-stick | fill #FFD43B | — |
| Triggers | fill #FFFFFF | — |
