"""Resource path resolution that works both in source checkouts and in
PyInstaller frozen builds.

In a frozen build PyInstaller extracts bundled data (static/, game_profiles/)
to ``sys._MEIPASS``. In a normal checkout the data sits next to this file.
"""

import os
import sys


def resource_dir() -> str:
    """Base directory that bundled data files live under."""
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def resource_path(*parts: str) -> str:
    """Absolute path to a bundled resource (e.g. resource_path('static'))."""
    return os.path.join(resource_dir(), *parts)
