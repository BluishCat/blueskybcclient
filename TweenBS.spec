# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all
import os

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
)
