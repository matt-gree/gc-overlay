"""Display settings for the overlay, shared by the HTTP API and the page.

Settings layer on top of each other:

1. ``DEFAULTS`` below.
2. Server defaults — seeded from CLI flags at startup, mutated by
   ``POST /api/settings``.
3. Per-client settings — a copy of the server defaults taken when a browser
   opens a WebSocket, with that client's query-string overrides applied on
   top (e.g. ``http://localhost:8069/?port=2&gear=0``).

``POST /api/settings`` takes a *partial* patch: only the keys present in the
body change, both on the server defaults and on every connected client. That
lets an external controller (PRSH) drive shared chrome — the settings gear,
the port label — without disturbing per-source settings like ``port``.

Ports are 1-indexed everywhere in this module and in the API, matching what
users see. Only ``adapter.get_state()`` takes a 0-indexed port.
"""

BACKGROUNDS = ('dark', 'transparent')

DEFAULTS = {
    'port': 1,               # controller port to display, 1-4
    'background': 'dark',    # 'dark' or 'transparent' (use transparent in OBS)
    'show_gear': True,       # settings gear button, bottom-left
    'show_port_label': True, # "P1" label, bottom-right
    'show_status': True,     # "Waiting for controller data..." text
    'show_labels': True,     # A/B/X/Y/Z/ST/L/R glyphs on the controller
    'show_keyline': True,    # black outline behind every stroke and glyph
    'show_idle_fill': False, # dark fill inside unpressed buttons
}

# Short query-string aliases -> canonical setting key. The canonical keys work
# as query params too, so ?gear=0 and ?show_gear=0 are equivalent.
ALIASES = {
    'bg': 'background',
    'gear': 'show_gear',
    'portlabel': 'show_port_label',
    'port_label': 'show_port_label',
    'status': 'show_status',
    'labels': 'show_labels',
    'keyline': 'show_keyline',
    'idlefill': 'show_idle_fill',
    'idle_fill': 'show_idle_fill',
}

_TRUE = {'1', 'true', 'yes', 'on'}
_FALSE = {'0', 'false', 'no', 'off'}


def _coerce_bool(key, value):
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ValueError(f"{key}: expected a boolean, got {value!r}")


def _coerce_port(key, value):
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{key}: expected an integer 1-4, got {value!r}")
    if not 1 <= port <= 4:
        raise ValueError(f"{key}: expected an integer 1-4, got {value!r}")
    return port


def _coerce_background(key, value):
    text = str(value).strip().lower()
    if text not in BACKGROUNDS:
        raise ValueError(
            f"{key}: expected one of {', '.join(BACKGROUNDS)}, got {value!r}"
        )
    return text


COERCERS = {
    'port': _coerce_port,
    'background': _coerce_background,
    'show_gear': _coerce_bool,
    'show_port_label': _coerce_bool,
    'show_status': _coerce_bool,
    'show_labels': _coerce_bool,
    'show_keyline': _coerce_bool,
    'show_idle_fill': _coerce_bool,
}


def defaults():
    """A fresh copy of the built-in defaults."""
    return dict(DEFAULTS)


def parse(mapping, strict=True):
    """Validate a partial settings mapping into a canonical patch dict.

    With ``strict`` off, unknown keys and unparseable values are skipped
    instead of raising — used for query strings, which carry unrelated
    params (cache busters, OBS additions) we should not choke on.
    """
    patch = {}
    for raw_key, value in mapping.items():
        key = ALIASES.get(raw_key, raw_key)
        coerce = COERCERS.get(key)
        if coerce is None:
            if strict:
                raise ValueError(f"unknown setting {raw_key!r}")
            continue
        try:
            patch[key] = coerce(key, value)
        except ValueError:
            if strict:
                raise
    return patch
