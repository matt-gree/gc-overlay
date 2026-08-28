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
- `server.py` — Serves the overlay page, broadcasts controller state over WebSocket at ~120Hz, and exposes the JSON API under `/api`
- `overlay_settings.py` — Display settings schema, validation, and query-string aliases
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

# Display defaults (also settable per-source via URL params, see below):
python main.py --bg transparent   # Start with a transparent background
python main.py --no-gear          # Hide the settings gear
python main.py --no-port-label    # Hide the "P1" label
python main.py --no-status        # Hide the "Waiting for controller data..." text
python main.py --no-labels        # Hide the A/B/X/Y/Z/ST/L/R letters
python main.py --no-keyline       # Drop the black keyline behind every stroke
python main.py --idle-fill        # Fill unpressed buttons with a dark tint
```

Then either:
- **Open in browser**: Navigate to `http://localhost:8069`
- **OBS Browser Source**: Add a Browser Source with URL `http://localhost:8069?bg=transparent`, width `512`, height `180`

### Startup Order (memorywatcher transport only)

1. Start the overlay: `python main.py`
2. Launch Dolphin / Project Rio
3. Boot your game
4. The terminal will print "Receiving controller data from Dolphin." when data starts flowing

If you restart the overlay, you must also restart Dolphin for the connection
to re-establish. (Not so on the `dme` transport, which re-hooks on its own.)

### OBS Setup

1. Add a **Browser Source** in OBS
2. Set URL to `http://localhost:8069?bg=transparent`
3. Set width to `512` and height to `180`
4. `?bg=transparent` gives you native transparency — no chroma key needed

The overlay is drawn as a single SVG that scales to fill the browser source, so
512x180 is a *ratio* rather than a fixed size. Any size with that 128:45 aspect
works; anything else letterboxes rather than distorting.

For a clean broadcast source, hide the interactive chrome:

```
http://localhost:8069?bg=transparent&gear=0&portlabel=0&status=0
```

Add `&labels=0` to drop the A/B/X/Y/Z/ST/L/R letters and show shapes only.
`show_labels` covers the controller glyphs only — the port label and the status
text keep their own settings.

### Legibility over a bright scene

Every stroke and letter is drawn over a **black keyline** — the overlay is
readable on a green field or a bright game capture, where a thin grey outline
would otherwise disappear once OBS scales the source down. It costs nothing on
a dark background, where it simply isn't visible, so it is on by default.
`&keyline=0` turns it off.

If the shapes still read as too airy, `&idlefill=1` fills every unpressed
button with a dark tint of its own colour, so buttons show as filled chips
rather than hollow rings. It is off by default, since it is a large change to
the look:

```
http://localhost:8069?bg=transparent&gear=0&portlabel=0&status=0&labels=0&idlefill=1
```

### Overlay Controls

- **Settings gear** (bottom-left): Switch controller port, toggle background, show/hide the port label, status text, button letters, keyline and idle fill, calibrate sticks
- **Number keys 1-4**: Quick switch between controller ports
- **C key**: Recalibrate stick centers

## Display Settings

Display settings can be set three ways, each layering on the one before:

1. **CLI flags** at startup (`--bg`, `--no-gear`, …) set the server defaults.
2. **URL query params** on a browser source override the defaults *for that
   source only*. Two OBS sources can therefore show different ports.
3. **`POST /api/settings`** at runtime changes the server defaults *and* pushes
   to every connected source, with no page reload.

| Setting | Query param | Values | Default |
|---|---|---|---|
| `port` | `port` | `1`–`4` | `1` |
| `background` | `bg` | `dark`, `transparent` | `dark` |
| `show_gear` | `gear` | boolean | `true` |
| `show_port_label` | `portlabel` | boolean | `true` |
| `show_status` | `status` | boolean | `true` |
| `show_labels` | `labels` | boolean | `true` |
| `show_keyline` | `keyline` | boolean | `true` |
| `show_idle_fill` | `idlefill` | boolean | `false` |

Booleans accept `1/0`, `true/false`, `yes/no`, `on/off`. The canonical key works
as a query param too, so `?gear=0` and `?show_gear=0` are equivalent. Unknown or
malformed query params are ignored rather than breaking the page.

### HTTP API

The server listens on `127.0.0.1` and allows cross-origin calls to `/api`, so an
external controller (e.g. PRSH) can drive the overlay. Ports are 1-indexed.

**`GET /api/settings`** — current server defaults.

```bash
curl http://localhost:8069/api/settings
# {"settings": {"port": 1, "background": "dark", "show_gear": true,
#               "show_port_label": true, "show_status": true,
#               "show_labels": true, "show_keyline": true,
#               "show_idle_fill": false}, "clients": 1}
```

**`POST /api/settings`** — apply a *partial* patch. Only the keys you send
change, so you can drive shared chrome without disturbing per-source settings
like `port`. Returns the same shape as `GET`. Invalid keys or values return
`400` and change nothing.

```bash
# Hide the gear and the port label on every connected source
curl -X POST http://localhost:8069/api/settings \
  -H 'Content-Type: application/json' \
  -d '{"show_gear": false, "show_port_label": false}'
```

The body may also be wrapped as `{"settings": {...}}` if that reads better.

**`GET /api/state?port=1`** — current controller state for one port, the same
payload the WebSocket broadcasts. Useful for polling or debugging without a
browser.

```bash
curl 'http://localhost:8069/api/state?port=1'
```

**`POST /api/calibrate?port=1`** — reset that port's stick centers.

### WebSocket

`ws://localhost:8069/ws` carries the same query params as the page URL. Messages
are JSON tagged with `type`:

- `{"type": "state", ...}` — controller state, ~120Hz
- `{"type": "settings", "settings": {...}}` — sent on connect and whenever
  settings change

Clients may send `{"settings": {...}}` to change their *own* settings (this does
not touch the server defaults or other sources) or `{"calibrate": true}`.

### A note for PRSH

Two patterns work, and they compose:

- **Static**: bake the settings into each browser source URL
  (`?port=1&bg=transparent&gear=0&portlabel=0`). Nothing else to wire up.
- **Live**: `POST /api/settings` whenever something should change on screen.
  Because patches are partial, posting `{"show_port_label": false}` leaves each
  source's `port` alone.

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
