#!/usr/bin/env python3
"""
GC Overlay - GameCube Controller Input Display for macOS

Reads input from a GameCube Controller Adapter (WUP-028) via USB
and displays an overlay suitable for OBS capture.

Usage:
    python main.py [--port 8069] [--controller 1]
    python main.py --demo   # Demo mode with animated inputs

Then open http://localhost:8069 in a browser or add it as an
OBS Browser Source for a transparent overlay.

Requirements:
    - macOS: Install GCAdapterDriver (https://github.com/secretkeysio/GCAdapterDriver)
    - Linux: Add a udev rule for the adapter
    - pip install pyusb aiohttp
"""

import argparse
import math
import signal
import sys
import threading
import time

from aiohttp import web

from gc_adapter import GCAdapter
from server import create_app


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
    args = parser.parse_args()

    if args.demo:
        adapter = DemoAdapter()
    else:
        adapter = GCAdapter()

    adapter.start()
    app = create_app(adapter, port=args.controller - 1)

    mode = "DEMO" if args.demo else "LIVE"
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
