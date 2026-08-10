# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all
from PyInstaller.utils.win32.versioninfo import (
    VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct,
    VarFileInfo, VarStruct,
)
import os
import sys

# pyinstaller をどのディレクトリから起動してもアプリ側モジュールを解決できるようにする
sys.path.insert(0, SPECPATH)
from utils.version import __version__

# バージョンリソースは4要素タプルを要求するため "0.1.0" → (0, 1, 0, 0) に展開
_vers = tuple(int(x) for x in __version__.split('.')) + (0,)

version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=_vers, prodvers=_vers),
    kids=[
        # '041104b0' = 日本語(0x0411) + Unicode(0x04b0=1200)。下の Translation と一致させる
        StringFileInfo([StringTable('041104b0', [
            StringStruct('CompanyName', 'BluishCat'),
            StringStruct('FileDescription', 'Bluesky BC Client'),
            StringStruct('FileVersion', __version__),
            StringStruct('InternalName', 'BlueskyBCClient'),
            StringStruct('LegalCopyright', 'Copyright (c) 2026 BluishCat'),
            StringStruct('OriginalFilename', 'BlueskyBCClient.exe'),
            StringStruct('ProductName', 'TweenBS'),
            StringStruct('ProductVersion', __version__),
        ])]),
        VarFileInfo([VarStruct('Translation', [0x0411, 1200])]),
    ],
)

datas = [('sound', 'sound')]  # サウンドファイルを同梱
binaries = []
hiddenimports = [
    'atproto', 'atproto_client', 'pydantic', 'pydantic_core', 'typing_extensions',
    'atproto_client.client', 'atproto_client.client.client',
    'atproto_crypto', 'atproto_identity', 'atproto_lexicon', 'atproto_server', 
    'atproto_codegen', 'atproto_common', 'libipld', 'requests', 'windnd'
]

# Collection loop
for pkg in ['atproto', 'atproto_client', 'atproto_crypto', 'atproto_identity', 'atproto_lexicon', 'atproto_server', 'atproto_codegen', 'atproto_common', 'libipld', 'pydantic', 'pydantic_core']:
    try:
        tmp_ret = collect_all(pkg)
        datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
    except Exception: pass

a = Analysis(
    ['bluesky_client.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='BlueskyBCClient',
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
    version=version_info,
)
