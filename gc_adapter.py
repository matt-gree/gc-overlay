"""
GameCube Controller Adapter USB communication.

Reads input from a Nintendo WUP-028 (or compatible) GameCube Controller Adapter
using pyusb/libusb. On macOS, requires GCAdapterDriver to be installed so the
kernel HID driver doesn't claim the device.

Protocol reference: Dolphin's GCAdapter.cpp
"""

import sys
import time
import threading
import usb.core
import usb.util

VENDOR_ID = 0x057E
PRODUCT_ID = 0x0337

CMD_START_POLLING = 0x13
REPORT_POLLING = 0x21

# Per-port byte layout within the 37-byte input report
# Byte 0: status (bit4=wired, bit5=wireless)
# Byte 1: buttons_1 (A,B,X,Y,DL,DR,DD,DU)
# Byte 2: buttons_2 (Start,Z,R_digital,L_digital)
# Byte 3: stick_x (0-255, center ~128)
# Byte 4: stick_y (0-255, center ~128)
# Byte 5: cstick_x (0-255, center ~128)
# Byte 6: cstick_y (0-255, center ~128)
# Byte 7: trigger_l (0-255)
# Byte 8: trigger_r (0-255)

STICK_RANGE = 80  # Approximate half-range for normalization
TRIGGER_MAX = 200  # Practical max trigger value


class Controller:
    """State for a single GC controller port."""

    def __init__(self):
        self.connected = False
        self.buttons = {}
        self.stick_x = 0.0
        self.stick_y = 0.0
        self.cstick_x = 0.0
        self.cstick_y = 0.0
        self.trigger_l = 0.0
        self.trigger_r = 0.0

        # Calibration centers (set on first read or manual calibrate)
        self._cal_stick_x = None
        self._cal_stick_y = None
        self._cal_cstick_x = None
        self._cal_cstick_y = None
        self._reset_buttons()

    def _reset_buttons(self):
        self.buttons = {
            'a': False, 'b': False, 'x': False, 'y': False,
            'z': False, 'start': False,
            'l': False, 'r': False,
            'dpad_up': False, 'dpad_down': False,
            'dpad_left': False, 'dpad_right': False,
        }

    def update(self, data):
        """Update state from a 9-byte port block."""
        status = data[0]
        was_connected = self.connected
        self.connected = bool(status & 0x10) or bool(status & 0x20)

        if not self.connected:
            self._reset_buttons()
            self.stick_x = self.stick_y = 0.0
            self.cstick_x = self.cstick_y = 0.0
            self.trigger_l = self.trigger_r = 0.0
            return

        b1, b2 = data[1], data[2]
        raw_sx, raw_sy = data[3], data[4]
        raw_cx, raw_cy = data[5], data[6]
        raw_tl, raw_tr = data[7], data[8]

        # Auto-calibrate on first read after connect
        if not was_connected or self._cal_stick_x is None:
            self._cal_stick_x = raw_sx
            self._cal_stick_y = raw_sy
            self._cal_cstick_x = raw_cx
            self._cal_cstick_y = raw_cy

        # Buttons
        self.buttons['a'] = bool(b1 & 0x01)
        self.buttons['b'] = bool(b1 & 0x02)
        self.buttons['x'] = bool(b1 & 0x04)
        self.buttons['y'] = bool(b1 & 0x08)
        self.buttons['dpad_left'] = bool(b1 & 0x10)
        self.buttons['dpad_right'] = bool(b1 & 0x20)
        self.buttons['dpad_down'] = bool(b1 & 0x40)
        self.buttons['dpad_up'] = bool(b1 & 0x80)

        self.buttons['start'] = bool(b2 & 0x01)
        self.buttons['z'] = bool(b2 & 0x02)
        self.buttons['r'] = bool(b2 & 0x04)
        self.buttons['l'] = bool(b2 & 0x08)

        # Normalize sticks: centered at 0, range [-1, 1]
        self.stick_x = _normalize(raw_sx, self._cal_stick_x, STICK_RANGE)
        self.stick_y = _normalize(raw_sy, self._cal_stick_y, STICK_RANGE)
        self.cstick_x = _normalize(raw_cx, self._cal_cstick_x, STICK_RANGE)
        self.cstick_y = _normalize(raw_cy, self._cal_cstick_y, STICK_RANGE)

        # Normalize triggers: 0 to 1
        self.trigger_l = min(1.0, raw_tl / TRIGGER_MAX)
        self.trigger_r = min(1.0, raw_tr / TRIGGER_MAX)

    def calibrate(self):
        """Reset calibration to current stick positions."""
        self._cal_stick_x = None  # Will auto-calibrate on next read

    def to_dict(self):
        return {
            'connected': self.connected,
            'buttons': dict(self.buttons),
            'stick': {'x': round(self.stick_x, 4), 'y': round(self.stick_y, 4)},
            'cstick': {'x': round(self.cstick_x, 4), 'y': round(self.cstick_y, 4)},
            'trigger_l': round(self.trigger_l, 4),
            'trigger_r': round(self.trigger_r, 4),
        }


def _normalize(raw, center, half_range):
    """Normalize a raw byte value relative to a center point."""
    val = (raw - center) / half_range
    return max(-1.0, min(1.0, val))


class GCAdapter:
    """Interface to the Nintendo GameCube Controller Adapter (WUP-028).

    Spawns a background thread that continuously reads controller state.
    """

    def __init__(self):
        self.controllers = [Controller() for _ in range(4)]
        self.connected = False
        self._device = None
        self._endpoint_in = None
        self._endpoint_out = None
        self._lock = threading.Lock()
        self._running = False
        self._thread = None

    def start(self):
        """Start the USB reader thread."""
        self._running = True
        self._thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the reader thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)

    def get_state(self, port=0):
        """Get the current state of a controller port (thread-safe)."""
        with self._lock:
            return self.controllers[port].to_dict()

    def get_all_states(self):
        """Get states for all 4 ports."""
        with self._lock:
            return [c.to_dict() for c in self.controllers]

    def calibrate(self, port=0):
        """Recalibrate a controller port."""
        with self._lock:
            self.controllers[port].calibrate()

    def _reader_loop(self):
        """Background thread: find adapter, read inputs continuously."""
        while self._running:
            if not self.connected:
                self._try_connect()
                if not self.connected:
                    time.sleep(0.5)
                    continue

            try:
                data = self._endpoint_in.read(37, timeout=32)
                if len(data) >= 37 and data[0] == REPORT_POLLING:
                    with self._lock:
                        for i in range(4):
                            offset = 1 + (i * 9)
                            self.controllers[i].update(data[offset:offset + 9])
            except usb.core.USBTimeoutError:
                continue
            except usb.core.USBError as e:
                print(f"USB read error: {e}")
                self._disconnect()

    def _try_connect(self):
        """Attempt to find and initialize the GC adapter."""
        try:
            try:
                dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
            except usb.core.NoBackendError:
                if not hasattr(self, '_backend_warned'):
                    self._backend_warned = True
                    print(
                        "\nNo USB backend found. Install libusb:\n"
                        "  macOS:  brew install libusb\n"
                        "  Linux:  sudo apt install libusb-1.0-0\n"
                    )
                return
            if dev is None:
                return

            # On Linux, detach kernel driver. On macOS, GCAdapterDriver handles this.
            if sys.platform == 'linux':
                try:
                    if dev.is_kernel_driver_active(0):
                        dev.detach_kernel_driver(0)
                except usb.core.USBError:
                    pass

            dev.set_configuration()
            cfg = dev.get_active_configuration()
            intf = cfg[(0, 0)]

            ep_in = usb.util.find_descriptor(
                intf,
                custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN,
            )
            ep_out = usb.util.find_descriptor(
                intf,
                custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT,
            )

            if ep_in is None or ep_out is None:
                print("Could not find USB endpoints")
                return

            # Nyko adapter compatibility (same as Dolphin)
            try:
                dev.ctrl_transfer(0x21, 11, 0x0001, 0, None, timeout=1000)
            except usb.core.USBError:
                pass  # Expected to fail on non-Nyko adapters

            # Send start polling command
            ep_out.write(bytes([CMD_START_POLLING]), timeout=1000)

            self._device = dev
            self._endpoint_in = ep_in
            self._endpoint_out = ep_out
            self.connected = True
            print("GC Adapter connected!")

        except usb.core.USBError as e:
            print(f"Failed to connect to GC adapter: {e}")
            if "Access denied" in str(e) or "LIBUSB_ERROR_ACCESS" in str(e):
                print(
                    "\nOn macOS, install GCAdapterDriver: "
                    "https://github.com/secretkeysio/GCAdapterDriver\n"
                    "On Linux, add a udev rule for the adapter."
                )

    def _disconnect(self):
        """Clean up USB resources."""
        self.connected = False
        try:
            if self._device:
                usb.util.dispose_resources(self._device)
        except usb.core.USBError:
            pass
        self._device = None
        self._endpoint_in = None
        self._endpoint_out = None
        print("GC Adapter disconnected")
