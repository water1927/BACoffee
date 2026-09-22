# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path


REPOSITORY_ROOT = Path(SPECPATH).resolve().parent
SOURCE = REPOSITORY_ROOT / 'src' / 'bacoffee' / 'BACoffee.py'

# The public source repository intentionally does not carry the large release
# assets.  The build uses an approved complete release baseline or candidate
# supplied through BACOFFEE_RELEASE_ROOT, while keeping students external to
# the one-file EXE so the release directory is the single source of truth.
RELEASE_ROOT = Path(os.environ.get('BACOFFEE_RELEASE_ROOT', REPOSITORY_ROOT))
ASSETS = RELEASE_ROOT / 'assets'
if not ASSETS.is_dir():
    raise SystemExit(f'Missing BACoffee release assets: {ASSETS}')

BUILD_METADATA = Path(os.environ.get('BACOFFEE_BUILD_METADATA', ''))
if not BUILD_METADATA.is_file():
    raise SystemExit(
        'Missing build metadata; set BACOFFEE_BUILD_METADATA to the generated '
        'metadata file before building.')

ASSET_DIRECTORIES = ('background', 'branding', 'fonts', 'ui')
DATA_FILES = [
    (str(ASSETS / name), f'assets/{name}')
    for name in ASSET_DIRECTORIES
    if (ASSETS / name).is_dir()
]
DATA_FILES.append((str(BUILD_METADATA), 'build-metadata.json'))

a = Analysis(
    [str(SOURCE)],
    pathex=[str(SOURCE.parent)],
    binaries=[],
    datas=DATA_FILES,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='BACoffee',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[str(ASSETS / 'branding' / 'Coffee.ico')],
)
