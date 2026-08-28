# GC Overlay

A cross-platform GameCube controller input overlay for OBS, inspired by [M'Overlay](https://github.com/bkacjios/m-overlay).

Reads controller input from Dolphin (or Project Rio, Slippi, etc.) and displays a clean overlay that OBS can capture as a Browser Source with full transparency. Works during **Netplay** since all players' inputs exist in the local Dolphin process memory.

![overlay preview](https://img.shields.io/badge/status-beta-yellow)

## How It Works

There are two ways to get controller state out of Dolphin, and which one is
available is a property of the platform — not a primary and a fallback:

| Transport | How | macOS | Windows | Linux |
|---|---|:-:|:-:|:-:|
| `memorywatcher` | Dolphin *pushes* memory changes to us over a Unix domain socket (its built-in MemoryWatcher) | ✅ | ❌ | ✅ |
| `dme` | We *poll* the Dolphin process's emulated memory, as M'Overlay does | ❌ | ✅ | ✅ |

MemoryWatcher does not exist on Windows: Dolphin guards `MemoryWatcher.cpp`
with `if(UNIX)`, so a Windows build has no socket to connect to. Conversely,
reading another process's memory is blocked on macOS, where Dolphin ships a
hardened runtime with no `get-task-allow` entitlement — which is why the
macOS path needs no code signing, SIP changes, or process memory hacking.

`--transport auto` (the default) picks the right one for your platform.

**Architecture:**
- `dolphin_common.py` — Game profile + controller decoding, shared by both transports
- `memorywatcher_adapter.py` — MemoryWatcher transport (AF_UNIX socket)
- `dme_adapter.py` — Process-memory transport (polls at ~240Hz)
- `server.py` — Serves the overlay page and broadcasts controller state over WebSocket at ~120Hz
- `static/index.html` — Self-contained HTML/CSS/JS overlay with transparent background
- `game_profiles/` — Per-game memory address configurations (valid for either transport)
- OBS captures it as a Browser Source (native transparency, no chroma key needed)

## Prerequisites

- **Python 3.10+**
- **Dolphin**, **Project Rio**, or **Slippi Dolphin**
- `pip install -r requirements.txt`

No additional drivers or libraries are needed for Dolphin mode on any
platform. The `dme` transport's `dolphin-memory-engine` dependency is a
small self-contained wheel — it is a *library*, not the Dolphin Memory
Engine GUI application, and nothing extra is installed on your machine.

## Setup

```bash
git clone https://github.com/your-username/gc-overlay.git
cd gc-overlay
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Usage

**On macOS and Linux (memorywatcher transport):** start the overlay *before*
launching Dolphin. MemoryWatcher connects at game boot and doesn't retry.
The `dme` transport has no such requirement — it hooks whenever Dolphin
appears, and re-hooks after a restart.

```bash
# Dolphin mode (default) — reads from Dolphin's emulated memory
python main.py

# Options:
python main.py --port 8069        # Change server port (default: 8069)
python main.py --transport dme    # Force a transport (auto|memorywatcher|dme)
python main.py --controller 2     # Show port 2 instead of port 1
python main.py --game mario_superstar_baseball  # Game profile (default)
python main.py --dolphin-dir ~/path/to/dolphin  # Override Dolphin config dir
python main.py --demo             # Demo mode with animated inputs (no Dolphin needed)
python main.py --usb              # Direct USB adapter mode (requires pyusb + libusb)
```

Then either:
- **Open in browser**: Navigate to `http://localhost:8069`
- **OBS Browser Source**: Add a Browser Source with URL `http://localhost:8069`, width `512`, height `256`

### Startup Order (memorywatcher transport only)

1. Start the overlay: `python main.py`
2. Launch Dolphin / Project Rio
3. Boot your game
4. The terminal will print "Receiving controller data from Dolphin." when data starts flowing

If you restart the overlay, you must also restart Dolphin for the connection
to re-establish. (Not so on the `dme` transport, which re-hooks on its own.)

### OBS Setup

1. Add a **Browser Source** in OBS
2. Set URL to `http://localhost:8069`
3. Set width to `512` and height to `256`
4. The background is transparent by default — no chroma key needed

### Overlay Controls

- **Settings gear** (bottom-left): Switch controller port, toggle dark background, calibrate sticks
- **Number keys 1-4**: Quick switch between controller ports
- **C key**: Recalibrate stick centers

## Game Profiles

Game profiles define the memory addresses where controller data is stored. Each game may store this data at a different address.

Profiles are stored in `game_profiles/` as JSON files:

```json
{
  "game_id": "GYQE01",
  "game_name": "Mario Superstar Baseball (NTSC-U v1.0)",
  "controller_base": "803C77B8",
  "controller_stride": 32,
  "port_count": 4,
  "field_offsets": {
    "buttons": 0,
    "plugged": 8,
    "sticks": 16,
    "triggers": 20
  },
  "normalization": {
    "stick_range": 72,
    "cstick_range": 59,
    "trigger_max": 150
  }
}
```

To set the controller base address for a game:
```bash
python main.py --dolphin-set-addr 803C77B8 --game mario_superstar_baseball
```

## Direct USB Mode

For reading directly from a physical GC adapter (without Dolphin), use `--usb` mode. This requires additional dependencies:

### macOS
1. **Install libusb**: `brew install libusb`
2. **Install pyusb**: `pip install pyusb`
3. **Install [GCAdapterDriver](https://github.com/secretkeysio/GCAdapterDriver/releases)** (prevents macOS from claiming the adapter)

### Linux
1. `sudo apt install libusb-1.0-0-dev && pip install pyusb`
2. Add a udev rule:
   ```bash
   echo 'SUBSYSTEM=="usb", ATTRS{idVendor}=="057e", ATTRS{idProduct}=="0337", MODE="0666"' | sudo tee /etc/udev/rules.d/51-gcadapter.rules
   sudo udevadm control --reload-rules
   ```

```bash
python main.py --usb
```

## Troubleshooting

### Dolphin Mode — dme transport (default on Windows)

**"Waiting for Dolphin/Project Rio to start..."**
- The overlay is polling for the process and will hook as soon as it appears.
- "Dolphin is running but no game is booted" means it found Dolphin and is waiting on the game.

**"Warning: booted game is 'XXXXXX', but this profile is for 'GYQE01'"**
- A different game is loaded. The overlay will draw meaningless input until the profile's game is booted.

### Dolphin Mode — memorywatcher transport (default on macOS/Linux)

**"Waiting for controller data..." stays on screen**
- Make sure you started the overlay *before* launching Dolphin
- Quit Dolphin fully (Cmd+Q on macOS), then relaunch and boot your game
- Check that your game profile exists in `game_profiles/`

**"Unable to resolve read address" popup in Dolphin/Project Rio**
- Click "Ignore for Session" — this happens when MemoryWatcher tries to read addresses before the game is fully loaded. It resolves once the game boots.

**No data after 30+ seconds**
- Run the diagnostic tool: `python diag_memorywatcher.py`
- This will tell you if MemoryWatcher is sending any data at all
- You can add custom addresses to test: `python diag_memorywatcher.py --addresses 803C77B8 803C77D8`

**Inputs are wrong or garbled**
- The game profile's `controller_base` address may be wrong for your version
- Use `diag_memorywatcher.py` with candidate addresses to find the right one

### USB Mode

**"Waiting for controller data..."**
- Make sure the adapter is plugged in via USB
- On macOS: Verify GCAdapterDriver is installed
- On macOS Ventura+: Approve the USB accessory when first plugging in

**"No controller on Port X"**
- Plug a GameCube controller into the adapter port you selected
- Try a different port (press 1-4 on keyboard)

**Sticks are off-center**
- Press `C` or use the Settings panel to recalibrate

## Supported Dolphin Variants

The overlay automatically checks for config directories of:
- Project Rio
- Slippi Dolphin
- Dolphin (mainline)

## Tech Stack

- **Python 3.10+** with asyncio
- **aiohttp** — Async HTTP + WebSocket server
- **dolphin-memory-engine** — Process-memory reads (only used by the `dme` transport)
- **pyusb** — USB communication via libusb (only needed for `--usb` mode)
- **HTML/CSS/JS** — Self-contained SVG overlay (no build step)
