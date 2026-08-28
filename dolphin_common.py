"""Transport-agnostic Dolphin controller decoding.

Everything here is about *interpreting* GameCube controller memory: the game
profile that says where it lives, and the parsing that turns raw u32 reads
into overlay state. Nothing here knows how the bytes were obtained.

Two adapters consume this module as peers, differing only in transport:

  * ``memorywatcher_adapter`` — Dolphin pushes changes to us over an AF_UNIX
    socket (its built-in MemoryWatcher; Unix-only, since Dolphin guards it
    with ``if(UNIX)``).
  * ``dme_adapter`` — we poll the Dolphin process's emulated memory directly
    (Windows and Linux, where reading another process is permitted).

Both feed the same u32 values into the same DolphinPort, so a game profile is
valid for either and the overlay cannot tell them apart.
"""

import json


# Field name constants for address mapping
FIELD_BUTTONS = 'buttons'
FIELD_PLUGGED = 'plugged'
FIELD_STICKS = 'sticks'
FIELD_TRIGGERS = 'triggers'

# Button bitmask values (big-endian u16, standard GC PADStatus)
BUTTON_MAP = {
    0x0100: 'a',
    0x0200: 'b',
    0x0400: 'x',
    0x0800: 'y',
    0x0010: 'z',
    0x1000: 'start',
    0x0040: 'l',
    0x0020: 'r',
    0x0008: 'dpad_up',
    0x0004: 'dpad_down',
    0x0001: 'dpad_left',
    0x0002: 'dpad_right',
}


def _signed_byte(b):
    """Convert an unsigned byte (0-255) to signed (-128..127)."""
    return b - 256 if b >= 128 else b


class DolphinPort:
    """State for a single controller port read from Dolphin memory.

    Normalization constants are per-game (from the game profile) since
    different games may store stick/trigger data with different ranges.
    """

    def __init__(self, stick_range=80, cstick_range=80, trigger_max=200):
        self.stick_range = stick_range
        self.cstick_range = cstick_range
        self.trigger_max = trigger_max

        # Default to True for Dolphin adapter. MemoryWatcher only reports
        # changes, so if a controller was already plugged when we started
        # watching, we'd never receive the initial update.
        self.connected = True
        self.buttons = {
            'a': False, 'b': False, 'x': False, 'y': False,
            'z': False, 'start': False,
            'l': False, 'r': False,
            'dpad_up': False, 'dpad_down': False,
            'dpad_left': False, 'dpad_right': False,
        }
        self.stick_x = 0.0
        self.stick_y = 0.0
        self.cstick_x = 0.0
        self.cstick_y = 0.0
        self.trigger_l = 0.0
        self.trigger_r = 0.0

    def update_buttons(self, value):
        """Parse buttons from u32 read at the buttons offset.

        Bytes (big-endian): [btn_hi, btn_lo, ...]
        We only use the upper 16 bits (buttons.pressed).
        """
        self.connected = True
        buttons_raw = (value >> 16) & 0xFFFF
        for mask, name in BUTTON_MAP.items():
            self.buttons[name] = bool(buttons_raw & mask)

    def update_sticks(self, value):
        """Parse all 4 stick axes from u32 read at the sticks offset.

        Bytes (big-endian): [stick_x, stick_y, cstick_x, cstick_y]
        All signed bytes (-128..127), center=0.
        """
        self.connected = True
        sx = _signed_byte((value >> 24) & 0xFF)
        sy = _signed_byte((value >> 16) & 0xFF)
        cx = _signed_byte((value >> 8) & 0xFF)
        cy = _signed_byte(value & 0xFF)

        self.stick_x = max(-1.0, min(1.0, sx / self.stick_range))
        self.stick_y = max(-1.0, min(1.0, sy / self.stick_range))
        self.cstick_x = max(-1.0, min(1.0, cx / self.cstick_range))
        self.cstick_y = max(-1.0, min(1.0, cy / self.cstick_range))

    def update_triggers(self, value):
        """Parse triggers from u32 read at the triggers offset.

        Bytes (big-endian): [trigger_l, trigger_r, ...]
        Unsigned bytes 0-255.
        """
        self.connected = True
        raw_tl = (value >> 24) & 0xFF
        raw_tr = (value >> 16) & 0xFF
        self.trigger_l = min(1.0, raw_tl / self.trigger_max)
        self.trigger_r = min(1.0, raw_tr / self.trigger_max)

    def update_plugged(self, value):
        """Parse plugged status from u32 read at the plugged offset.

        Bytes (big-endian): [plugged, ...]
        Nonzero = connected.
        """
        plugged = (value >> 24) & 0xFF
        self.connected = plugged != 0

    def to_dict(self):
        return {
            'connected': self.connected,
            'buttons': dict(self.buttons),
            'stick': {'x': round(self.stick_x, 4), 'y': round(self.stick_y, 4)},
            'cstick': {'x': round(self.cstick_x, 4), 'y': round(self.cstick_y, 4)},
            'trigger_l': round(self.trigger_l, 4),
            'trigger_r': round(self.trigger_r, 4),
        }


def load_game_profile(profile_path):
    """Load a game profile JSON file."""
    with open(profile_path, 'r') as f:
        return json.load(f)


def save_game_profile(profile_path, profile):
    """Save a game profile JSON file."""
    with open(profile_path, 'w') as f:
        json.dump(profile, f, indent=2)


# Dispatch table: field name → DolphinPort method. Shared by both adapters.
FIELD_UPDATERS = {
    FIELD_BUTTONS: DolphinPort.update_buttons,
    FIELD_STICKS: DolphinPort.update_sticks,
    FIELD_TRIGGERS: DolphinPort.update_triggers,
    FIELD_PLUGGED: DolphinPort.update_plugged,
}
