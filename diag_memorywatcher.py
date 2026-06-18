#!/usr/bin/env python3
"""
MemoryWatcher diagnostic tool for troubleshooting Dolphin connectivity.

Tests whether Dolphin's MemoryWatcher is sending data to the socket.
Useful when the overlay isn't receiving controller data.

Usage:
    1. python diag_memorywatcher.py
    2. Quit Dolphin fully (Cmd+Q on macOS), then relaunch and boot your game
    3. Move sticks / press buttons and watch for output
    4. Ctrl+C to quit and see a summary

Troubleshooting:
    - "Still waiting..." after 30s: MemoryWatcher isn't connecting.
      Make sure you fully quit and relaunched Dolphin AFTER starting this script.
    - Data from "baseline" only: The game's controller memory addresses
      may be wrong for your version. Try different addresses.
    - No Dolphin directory found: Use --dolphin-dir to specify the path.
"""

import argparse
import os
import socket
import sys
import time

from dolphin_adapter import DOLPHIN_DIRS, find_dolphin_dir


# Basic addresses that should change during any game
BASELINE_ADDRS = {
    "80000000": "baseline  game-header",
    "80003100": "baseline  os-context (should change frequently)",
}


def main():
    parser = argparse.ArgumentParser(
        description='MemoryWatcher diagnostic — test Dolphin connectivity',
    )
    parser.add_argument(
        '--dolphin-dir', type=str, default=None,
        help='Override Dolphin config directory path',
    )
    parser.add_argument(
        '--addresses', type=str, nargs='+', default=None,
        help='Additional hex addresses to watch (e.g. 803C77B8 803C77C8)',
    )
    args = parser.parse_args()

    # Find all Dolphin directories
    dirs = [d for d in DOLPHIN_DIRS if os.path.isdir(d)]
    if args.dolphin_dir:
        expanded = os.path.expanduser(args.dolphin_dir)
        if os.path.isdir(expanded) and expanded not in dirs:
            dirs.insert(0, expanded)

    if not dirs:
        print("No Dolphin config directories found!")
        print("Checked:")
        for d in DOLPHIN_DIRS:
            print(f"  {d}")
        print("\nUse --dolphin-dir to specify the path.")
        sys.exit(1)

    # Build address set
    all_addrs = dict(BASELINE_ADDRS)
    if args.addresses:
        for addr in args.addresses:
            addr = addr.upper().replace("0X", "")
            all_addrs[addr] = f"custom  {addr}"

    # Write Locations.txt to all directories
    locations_content = "\n".join(sorted(all_addrs.keys())) + "\n"
    socket_paths = []

    for dolphin_dir in dirs:
        mw_dir = os.path.join(dolphin_dir, "MemoryWatcher")
        os.makedirs(mw_dir, exist_ok=True)

        loc_path = os.path.join(mw_dir, "Locations.txt")
        with open(loc_path, 'w') as f:
            f.write(locations_content)
        print(f"Wrote Locations.txt: {loc_path}")
        socket_paths.append(os.path.join(mw_dir, "MemoryWatcher"))

    print(f"\nWatching {len(all_addrs)} addresses")

    # Bind sockets in all directories
    import select
    socks = []
    for socket_path in socket_paths:
        if os.path.exists(socket_path):
            try:
                os.unlink(socket_path)
            except OSError:
                pass
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        sock.setblocking(False)
        sock.bind(socket_path)
        socks.append((sock, socket_path))
        print(f"Socket ready: {socket_path}")

    print(f"\nQuit Dolphin (Cmd+Q), relaunch, and boot your game.")
    print(f"Press buttons / move sticks. Ctrl+C to quit.\n")

    addr_counts = {}
    total = 0
    start = time.time()

    try:
        while True:
            readable, _, _ = select.select(
                [s for s, _ in socks], [], [], 2.0
            )
            if not readable:
                elapsed = time.time() - start
                if total == 0 and int(elapsed) % 15 == 0 and int(elapsed) > 0:
                    print(f"  Still waiting... ({int(elapsed)}s, no data)")
                continue

            for sock in readable:
                data = sock.recv(4096)
                if not data:
                    continue

                text = data.replace(b'\x00', b'').decode('ascii', errors='replace').strip()
                if not text:
                    continue

                lines = text.split('\n')
                i = 0
                while i + 1 < len(lines):
                    addr_str = lines[i].strip()
                    val_str = lines[i + 1].strip()
                    i += 2

                    label = all_addrs.get(addr_str, f"unknown ({addr_str})")
                    addr_counts[addr_str] = addr_counts.get(addr_str, 0) + 1
                    total += 1

                    elapsed = time.time() - start
                    print(f"[{elapsed:6.1f}s] {addr_str} = {val_str:>12s}  <- {label}")

    except KeyboardInterrupt:
        elapsed = time.time() - start
        print(f"\n{'='*60}")
        print(f"Results after {elapsed:.0f}s ({total} total updates):")
        print(f"{'='*60}")
        if total == 0:
            print("  No data received.")
            print("  - Make sure you quit and relaunched Dolphin AFTER starting this script")
            print("  - Check that MemoryWatcher is compiled into your Dolphin build")
        else:
            for addr, count in sorted(addr_counts.items()):
                label = all_addrs.get(addr, "unknown")
                print(f"  {addr}: {count:5d} updates  ({label})")
    finally:
        for sock, socket_path in socks:
            try:
                sock.close()
            except OSError:
                pass
            try:
                os.unlink(socket_path)
            except OSError:
                pass


if __name__ == '__main__':
    main()
