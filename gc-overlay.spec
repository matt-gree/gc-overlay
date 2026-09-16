# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for gc-overlay (GameCube controller input overlay).

Produces a one-folder build under dist/gc-overlay/ containing a standalone
``gc-overlay`` executable plus its bundled web assets. The folder is:

  * runnable on its own (double-click / `./gc-overlay --demo`), and
  * vendored wholesale into PRSH's bundle, where PRSH launches the binary
    as a managed subprocess (see PRSH server/controller_overlay.py).

Build:
    pyinstaller gc-overlay.spec

Notes:
    * USB mode (--usb) needs a native libusb at runtime. It is intentionally
      not bundled here; Dolphin and demo modes (what PRSH uses) need nothing
      extra. Standalone users who want --usb install libusb themselves.
    * The dme transport (default on Windows) is the opposite case: its wheel
      is a self-contained compiled extension linking only system libraries,
      so it bundles into the executable and the producer installs nothing.
"""

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('static', 'static'),
        ('game_profiles', 'game_profiles'),
        ('_version.py', '.'),
    ],
    hiddenimports=[
        'aiohttp',
        # Dolphin transports are imported lazily by main.resolve_transport,
        # so PyInstaller's static analysis cannot see this one.
        'dme_adapter',
        # dme_adapter's own import, named explicitly rather than left to the
        # recursive analysis of a hidden import: everything this module fixes
        # is a Windows-only failure that presents as "the overlay just waits",
        # and a missing module here would present the same way.
        'dolphin_process',
        'dolphin_memory_engine',
        'dolphin_memory_engine._dolphin_memory_engine',
        # Optional USB backend — imported lazily by --usb mode.
        'usb',
        'usb.core',
        'usb.util',
        'usb.backend.libusb1',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'pandas',
        'numpy',
        'matplotlib',
        'scipy',
        'pytest',
        'setuptools',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='gc-overlay',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,  # CLI tool: prints the overlay URL; PRSH captures stdout
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='gc-overlay',
    contents_directory='.',  # keep data next to the executable
)
