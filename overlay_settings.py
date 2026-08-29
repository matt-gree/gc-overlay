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
    # Dark fill inside unpressed buttons, 0..1. This is an OPACITY rather than
    # a switch because 0 already means off — a separate `show_idle_fill` would
    # add a second way to say the same thing, and the state where it is on at
    # zero opacity has no meaning. The old boolean spellings still parse (see
    # ALIASES and _coerce_opacity), so a URL saying `idlefill=1` keeps working
    # and means what it always meant.
    'idle_fill_opacity': 0.0,
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
    'idlefill': 'idle_fill_opacity',
    'idle_fill': 'idle_fill_opacity',
    # The 1.3.0 spelling. Kept because it is in URLs and OBS sources already,
    # and an unknown key is SKIPPED rather than raised on a query string — so
    # dropping it would have looked like the setting silently stopped working.
    'show_idle_fill': 'idle_fill_opacity',
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


def _coerce_opacity(key, value):
    """A 0..1 opacity that also accepts the boolean it replaced.

    `idlefill=1` and `idlefill=0` mean the same thing under both readings, which
    is what makes the rename safe; `true`/`false`/`on`/`off` are mapped so the
    other boolean spellings survive too.
    """
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE:
            return 1.0
        if text in _FALSE:
            return 0.0
    try:
        opacity = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{key}: expected a number 0-1, got {value!r}")
    if not 0.0 <= opacity <= 1.0:
        raise ValueError(f"{key}: expected a number 0-1, got {value!r}")
    return opacity


COERCERS = {
    'port': _coerce_port,
    'background': _coerce_background,
    'show_gear': _coerce_bool,
    'show_port_label': _coerce_bool,
    'show_status': _coerce_bool,
    'show_labels': _coerce_bool,
    'show_keyline': _coerce_bool,
    'idle_fill_opacity': _coerce_opacity,
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
