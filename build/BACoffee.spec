# -*- coding: utf-8 -*-
import os
from pathlib import Path
REPOSITORY_ROOT=Path(SPECPATH).resolve().parent
SOURCE=REPOSITORY_ROOT/'src'/'bacoffee'/'BACoffee.py'
RELEASE_ROOT=Path(os.environ['BACOFFEE_RELEASE_ROOT'])
BUILD_METADATA=Path(os.environ['BACOFFEE_BUILD_METADATA'])
SAT_METADATA=Path(os.environ['BACOFFEE_SAT_BUILD_METADATA'])
for resource in (RELEASE_ROOT/'assets',BUILD_METADATA,SAT_METADATA):
    if not resource.exists():raise SystemExit('Missing build input: '+str(resource))
a=Analysis([str(SOURCE)],pathex=[str(SOURCE.parent),str(REPOSITORY_ROOT/'src'/'sat')],
    binaries=[],datas=[(str(BUILD_METADATA),'.'),(str(SAT_METADATA),'.')],
    hiddenimports=['user_avatar_import_tool','avatar_import_core','user_tool_core','secrets'],
    hookspath=[],hooksconfig={},runtime_hooks=[],excludes=['cv2','numpy','PIL'],noarchive=False)
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,a.binaries,a.datas,[],name='BACoffee',debug=False,
    bootloader_ignore_signals=False,strip=False,upx=True,upx_exclude=[],runtime_tmpdir=None,
    console=False,disable_windowed_traceback=False,argv_emulation=False,target_arch=None,
    codesign_identity=None,entitlements_file=None,icon=[str(RELEASE_ROOT/'assets'/'branding'/'Coffee.ico')])

