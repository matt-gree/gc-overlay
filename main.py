#!/usr/bin/env python3
"""
GC Overlay - GameCube Controller Input Display for macOS

Reads controller input from Dolphin (via MemoryWatcher) or a direct
USB GameCube adapter and displays an overlay suitable for OBS capture.

Usage:
    python main.py                # Dolphin mode (default)
    python main.py --demo         # Demo mode with animated inputs
    python main.py --usb          # Direct USB adapter mode

Then open http://localhost:8069 in a browser or add it as an
OBS Browser Source for a transparent overlay.

Requirements:
    - Dolphin mode: Dolphin/Project Rio with MemoryWatcher (built-in)
    - USB mode: pyusb, libusb, GCAdapterDriver (macOS)
    - pip install aiohttp
"""

import argparse
import math
import os
import signal
import sys
import time

from aiohttp import web

from _version import __version__
from dolphin_common import load_game_profile, save_game_profile
from memorywatcher_adapter import MemoryWatcherAdapter
from resources import resource_path
from server import create_app

PROFILES_DIR = resource_path('game_profiles')


class DemoAdapter:
    """Fake adapter that generates animated controller data for testing."""

    def __init__(self):
        self.connected = True
        self._start = time.time()

    def start(self):
        pass

    def stop(self):
        pass

    def get_state(self, port=0):
        t = time.time() - self._start

        # Animate stick in a circle
        stick_x = math.sin(t * 1.5)
        stick_y = math.cos(t * 1.5)

        # C-stick in opposite circle, faster
        cstick_x = math.sin(t * 2.5 + math.pi)
        cstick_y = math.cos(t * 2.5 + math.pi)

        # Cycle through buttons
        cycle = int(t * 3) % 12
        buttons = {
            'a': cycle == 0,
            'b': cycle == 1,
            'x': cycle == 2,
            'y': cycle == 3,
            'z': cycle == 4,
            'start': cycle == 5,
            'l': cycle == 6,
            'r': cycle == 7,
            'dpad_up': cycle == 8,
            'dpad_right': cycle == 9,
            'dpad_down': cycle == 10,
            'dpad_left': cycle == 11,
        }

        # Animate triggers with sine wave
        trigger_l = max(0, math.sin(t * 2.0))
        trigger_r = max(0, math.sin(t * 2.0 + math.pi / 2))

        return {
            'connected': True,
            'buttons': buttons,
            'stick': {'x': round(stick_x, 4), 'y': round(stick_y, 4)},
            'cstick': {'x': round(cstick_x, 4), 'y': round(cstick_y, 4)},
            'trigger_l': round(trigger_l, 4),
            'trigger_r': round(trigger_r, 4),
        }

    def get_all_states(self):
        return [self.get_state(i) for i in range(4)]

    def calibrate(self, port=0):
        pass


def main():
    parser = argparse.ArgumentParser(
        description='GC Overlay - GameCube Controller Input Display',
    )
    parser.add_argument(
        '--version', action='version', version=__version__,
        help='Print the gc-overlay version and exit',
    )
    parser.add_argument(
        '--port', type=int, default=8069,
        help='HTTP server port (default: 8069)',
    )
    parser.add_argument(
        '--controller', type=int, default=1, choices=[1, 2, 3, 4],
        help='Controller port to display (default: 1)',
    )
    parser.add_argument(
        '--demo', action='store_true',
        help='Demo mode with animated controller inputs',
    )
    parser.add_argument(
        '--usb', action='store_true',
        help='Read from a direct USB GC adapter (requires pyusb + libusb)',
    )
    parser.add_argument(
        '--dolphin-dir', type=str, default=None,
        help='Override Dolphin/Project Rio config directory path',
    )
    parser.add_argument(
        '--game', type=str, default='mario_superstar_baseball',
        help='Game profile name (default: mario_superstar_baseball)',
    )
    parser.add_argument(
        '--dolphin-set-addr', type=str, default=None, metavar='HEX_ADDR',
        help='Set controller base address for the game profile (e.g. 803C77B8)',
    )
    args = parser.parse_args()

    profile_path = os.path.join(PROFILES_DIR, f"{args.game}.json")

    # Handle --dolphin-set-addr: update profile and exit
    if args.dolphin_set_addr:
        addr = args.dolphin_set_addr.strip()
        # Validate hex
        try:
            int(addr, 16)
        except ValueError:
            print(f"Error: '{addr}' is not a valid hex address.")
            sys.exit(1)
        if not os.path.exists(profile_path):
            print(f"Error: Game profile not found: {profile_path}")
            sys.exit(1)
        profile = load_game_profile(profile_path)
        profile['controller_base'] = addr
        save_game_profile(profile_path, profile)
        print(f"Set controller_base = 0x{addr.upper()} in {profile_path}")
        sys.exit(0)

    if args.demo:
        adapter = DemoAdapter()
    elif args.usb:
        try:
            from gc_adapter import GCAdapter
        except ImportError:
            print("Error: USB mode requires pyusb. Install it with:")
            print("  pip install pyusb")
            print("\nYou also need libusb:")
            print("  macOS: brew install libusb")
            print("  Linux: sudo apt install libusb-1.0-0-dev")
            sys.exit(1)
        adapter = GCAdapter()
    else:
        # Default: Dolphin mode via MemoryWatcher
        if not os.path.exists(profile_path):
            print(f"Error: Game profile not found: {profile_path}")
            print(f"Available profiles in {PROFILES_DIR}/")
            sys.exit(1)
        adapter = MemoryWatcherAdapter(profile_path, dolphin_dir=args.dolphin_dir)

    adapter.start()
    app = create_app(adapter, port=args.controller - 1)

    if args.demo:
        mode = "DEMO"
    elif args.usb:
        mode = "USB"
    else:
        mode = "DOLPHIN"

    print(f"\n  GC Overlay [{mode}]")
    print(f"  Controller port: {args.controller}")
    print(f"  Overlay URL:     http://localhost:{args.port}")
    print(f"  OBS Browser Source: http://localhost:{args.port} (512x256)")
    print()

    def shutdown(sig, frame):
        adapter.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        web.run_app(app, host='127.0.0.1', port=args.port, print=None)
    finally:
        adapter.stop()


if __name__ == '__main__':
    main()
