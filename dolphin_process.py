"""Find the running Dolphin-family process, so the dme transport can hook it.

dolphin-memory-engine locates its target BY PROCESS NAME, and the list it
ships is stock Dolphin's: ``Dolphin.exe`` / ``DolphinQt2.exe`` /
``DolphinWx.exe`` on Windows, ``dolphin-emu`` and friends on Linux. Project
Rio is a Dolphin fork that renames its executable, so on Windows DME scans
every process, matches none, and reports ``notRunning`` forever — the overlay
sits on "Waiting for controller data..." with Rio running in front of it.
macOS never saw this because the memorywatcher transport connects to a socket
and never asks what the process is called.

The escape hatch DME provides is ``DME_DOLPHIN_PROCESS_NAME``, and it comes
with a trap worth stating: findPID() reads it into a **static**, so the value
is captured on the FIRST hook attempt of the process and every later attempt
reuses it. Setting it per-retry does nothing. So the rule this module exists
to enforce is: do not call ``dme.hook()`` until a process has actually been
found, then commit to that name for the run.

The env var also REPLACES the default list rather than adding to it (it is a
ternary in DME's source), which is why discovery here has to cover stock
Dolphin's own names too — handing DME "Project Rio.exe" on a machine running
plain Dolphin would break the case that works today.
"""

import ctypes
import os
import platform
import re


# Exact names, most specific first. The stock Dolphin entries are not
# redundant: once we set the env var, DME stops checking them itself.
WINDOWS_NAMES = (
    "Project Rio.exe",
    "Project Rio",      # belt and braces: deterministic rather than via _FUZZY
    "ProjectRio.exe",
    "Slippi Dolphin.exe",
    "Dolphin.exe",
    "DolphinQt2.exe",
    "DolphinWx.exe",
)

# Linux compares against /proc/<pid>/comm, which the kernel truncates to 15
# characters — so "project-rio-emu" is as long as a name can usefully be here.
LINUX_NAMES = (
    "project-rio",
    "dolphin-emu",
    "dolphin-emu-qt2",
    "dolphin-emu-wx",
    ".dolphin-emu-wr",
)

# Fallback for a fork nobody has listed yet. Deliberately narrow: "dolphin"
# anywhere, or "rio" as its own word, so "Trio.exe" or "Rioja.exe" miss.
_FUZZY = re.compile(r"dolphin|(?:^|[^a-z])rio(?:$|[^a-z])|projectrio", re.IGNORECASE)


def _known_names():
    return WINDOWS_NAMES if platform.system() == "Windows" else LINUX_NAMES


def _running_process_names():
    """Every running process's name, as DME would see it. [] if we can't tell."""
    system = platform.system()
    if system == "Windows":
        return _windows_process_names()
    if system == "Linux":
        return _linux_process_names()
    return []


def _windows_process_names():
    """Snapshot the process list via Toolhelp32 — the same API DME uses.

    ctypes rather than psutil: gc-overlay's dependency set is three packages
    and this is one documented Win32 call. The restypes are not decoration —
    ctypes defaults a return value to ``c_int``, which TRUNCATES the 64-bit
    HANDLE these functions hand back, and a truncated handle fails in a way
    that looks exactly like "no Dolphin is running".
    """
    TH32CS_SNAPPROCESS = 0x00000002
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    MAX_PATH = 260

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_ulong),
            ("cntUsage", ctypes.c_ulong),
            ("th32ProcessID", ctypes.c_ulong),
            ("th32DefaultHeapID", ctypes.c_size_t),   # ULONG_PTR
            ("th32ModuleID", ctypes.c_ulong),
            ("cntThreads", ctypes.c_ulong),
            ("th32ParentProcessID", ctypes.c_ulong),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", ctypes.c_ulong),
            ("szExeFile", ctypes.c_wchar * MAX_PATH),
        ]

    entry_ptr = ctypes.POINTER(PROCESSENTRY32W)

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_ulong, ctypes.c_ulong]
    kernel32.Process32FirstW.restype = ctypes.c_int
    kernel32.Process32FirstW.argtypes = [ctypes.c_void_p, entry_ptr]
    kernel32.Process32NextW.restype = ctypes.c_int
    kernel32.Process32NextW.argtypes = [ctypes.c_void_p, entry_ptr]
    kernel32.CloseHandle.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == INVALID_HANDLE_VALUE:
        return []

    names = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            while True:
                names.append(entry.szExeFile)
                if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
    finally:
        kernel32.CloseHandle(snapshot)
    return names


def _linux_process_names():
    """Read /proc/<pid>/comm, which is exactly what DME's Linux findPID reads."""
    names = []
    try:
        pids = [d for d in os.listdir("/proc") if d.isdigit()]
    except OSError:
        return []
    for pid in pids:
        try:
            with open(f"/proc/{pid}/comm", "r") as fh:
                names.append(fh.readline().strip())
        except OSError:
            continue
    return names


def find_dolphin_process_name():
    """Name of a running Dolphin-family process, or None if none is up.

    None is also the answer when this platform's process list is unreadable
    — the caller treats that as "don't know" and lets DME try its own names.
    """
    running = _running_process_names()
    if not running:
        return None

    present = set(running)
    for name in _known_names():
        if name in present:
            return name

    for name in running:
        if _FUZZY.search(name):
            return name
    return None


def process_list_is_readable():
    """True when discovery is meaningful on this platform at all."""
    return platform.system() in ("Windows", "Linux")
