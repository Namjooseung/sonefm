# -*- mode: python ; coding: utf-8 -*-
# SoneFM 업무자동화 — PyInstaller onedir 빌드 (onefile보다 백신 오탐이 적다)
# 실행 (저장소 루트에서): .venv\Scripts\pyinstaller packaging/platform.spec --noconfirm
# 결과: dist/SoneFMPlatform/SoneFMPlatform.exe  (데이터는 문서\SoneFM\platform 에 저장)
from pathlib import Path

ROOT = Path(SPECPATH).parent

a = Analysis(
    [str(ROOT / "hub" / "main.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "ui"), "ui"), (str(ROOT / "recipes"), "recipes")],
    hiddenimports=["hub.api"],
    excludes=["tkinter", "pytest", "PIL", "PyInstaller"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="SoneFMPlatform", console=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="SoneFMPlatform", upx=False)
