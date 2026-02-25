# GC Overlay

A macOS-compatible GameCube controller input overlay for OBS, inspired by [M'Overlay](https://github.com/bkacjios/m-overlay).

Reads input directly from a Nintendo GameCube Controller Adapter (WUP-028) via USB and displays a clean overlay that OBS can capture as a Browser Source with full transparency.

![overlay preview](https://img.shields.io/badge/status-beta-yellow)

## How It Works

Unlike M'Overlay (which reads Dolphin's process memory and only works on Windows/Linux), GC Overlay communicates directly with the GC adapter over USB using the same protocol as Dolphin. This means it works on **macOS** and can display inputs whether you're using Dolphin, Project Rio, Slippi, or any other software.

**Architecture:**
- `gc_adapter.py` — Reads the adapter via pyusb/libusb (37-byte interrupt transfers at ~125Hz)
- `server.py` — Serves the overlay page and broadcasts controller state over WebSocket at ~120Hz
- `static/index.html` — Self-contained HTML/CSS/JS overlay with transparent background
- OBS captures it as a Browser Source (native transparency, no chroma key needed)

## Prerequisites

### macOS

1. **Install libusb** (USB communication library):
   ```bash
   brew install libusb
   ```

2. **Install GCAdapterDriver** (prevents macOS from claiming the adapter with its default HID driver):
   - Download from [GCAdapterDriver releases](https://github.com/secretkeysio/GCAdapterDriver/releases)
   - Install the `.pkg` — it's a signed DriverKit extension, no SIP disabling needed
   - This is the same driver Dolphin requires on macOS

### Linux

1. Install libusb:
   ```bash
   sudo apt install libusb-1.0-0-dev
   ```

2. Add a udev rule so you don't need root:
   ```bash
   echo 'SUBSYSTEM=="usb", ATTRS{idVendor}=="057e", ATTRS{idProduct}=="0337", MODE="0666"' | sudo tee /etc/udev/rules.d/51-gcadapter.rules
   sudo udevadm control --reload-rules
   ```

## Setup

```bash
git clone https://github.com/your-username/gc-overlay.git
cd gc-overlay
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Usage

```bash
# Start the overlay server
python main.py

# Options:
python main.py --port 8069        # Change server port (default: 8069)
python main.py --controller 2     # Show port 2 instead of port 1
python main.py --demo             # Demo mode with animated inputs (no adapter needed)
```

Then either:
- **Open in browser**: Navigate to `http://localhost:8069`
- **OBS Browser Source**: Add a Browser Source with URL `http://localhost:8069`, width `512`, height `256`

### OBS Setup

1. Add a **Browser Source** in OBS
2. Set URL to `http://localhost:8069`
3. Set width to `512` and height to `256`
4. The background is transparent by default — no chroma key needed

### Overlay Controls

- **Settings gear** (top-right): Switch controller port, toggle dark background, calibrate sticks
- **Number keys 1-4**: Quick switch between controller ports
- **C key**: Recalibrate stick centers

## Supported Adapters

- Nintendo Wii U GameCube Adapter (WUP-028) — VID `057E`, PID `0337`
- Mayflash GameCube Adapter (in "Wii U" or "Switch" mode) — uses the same VID/PID

## Troubleshooting

**"Waiting for GC Adapter..."**
- Make sure the adapter is plugged in via USB
- On macOS: Verify GCAdapterDriver is installed (`System Settings > General > Login Items & Extensions > Driver Extensions`)
- On macOS Ventura+: You may need to approve the USB accessory when first plugging in

**"No controller on Port X"**
- Plug a GameCube controller into the adapter port you selected
- Try a different port (press 1-4 on keyboard)

**Sticks are off-center**
- Press `C` or use the Settings panel to recalibrate
- Calibration happens automatically when a controller is first detected

**USB access denied**
- macOS: Install GCAdapterDriver
- Linux: Add the udev rule (see Prerequisites)

## Tech Stack

- **Python 3.10+**
- **pyusb** — USB communication via libusb
- **aiohttp** — Async HTTP + WebSocket server
- **HTML/CSS/JS** — Self-contained overlay (no build step)
