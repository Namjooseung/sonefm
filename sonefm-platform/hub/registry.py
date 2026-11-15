"""앱 레지스트리: sonefm-app.json(매니페스트)을 읽어 실행할 수 있는 앱 목록을 만든다.

찾는 순서: 설정 app_dirs(개발 폴더) → %LOCALAPPDATA%\\Programs\\*\\, %ProgramFiles%\\*\\ (설치판).
같은 id는 먼저 찾은 것을 쓴다. 앱별 코드는 여기에 넣지 않는다.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hub import SUPPORTED_CONTRACTS

MANIFEST = "sonefm-app.json"


@dataclass
class App:
    id: str
    name: str
    version: str
    dir: Path
    manifest: dict[str, Any] = field(default_factory=dict)
    cli: list[str] | None = None
    gui: list[str] | None = None
    error: str | None = None      # 있으면 실행하지 않고 화면에 이유를 보여준다
    installed: bool = False       # exe(설치판)로 실행하는지

    def command(self, name: str) -> dict[str, Any] | None:
        return (self.manifest.get("commands") or {}).get(name)

    def summary(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "version": self.version, "dir": str(self.dir),
                "error": self.error, "installed": self.installed, "has_gui": bool(self.gui),
                "commands": sorted((self.manifest.get("commands") or {}).keys())}


def _resolve(folder: Path, spec: dict[str, Any] | None) -> tuple[list[str] | None, bool]:
    """{"exe": [...], "dev": [...]} → (실행 명령, 설치판인지). exe[0]이 있으면 설치판."""
    if not spec:
        return None, False
    for kind in ("exe", "dev"):
        cmd = spec.get(kind)
        if cmd and (folder / cmd[0]).exists():
            return [str((folder / cmd[0]).resolve()), *cmd[1:]], kind == "exe"
    return None, False


def load_app(folder: str | Path) -> App | None:
    folder = Path(folder)
    path = folder / MANIFEST
    if not path.is_file():
        return None
    try:
        m = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(m, dict) or not m.get("id"):
            raise ValueError("id 없음")
    except (OSError, ValueError) as e:
        return App(folder.name, folder.name, "?", folder, error=f"앱 정보 파일(sonefm-app.json)을 읽지 못했습니다: {e}")
    app = App(str(m["id"]), str(m.get("name") or m["id"]), str(m.get("version") or "?"), folder, m)
    if m.get("contract") not in SUPPORTED_CONTRACTS:
        app.error = (f"이 앱은 계약 버전 {m.get('contract')}을 씁니다. 플랫폼이 아는 버전은 "
                     f"{sorted(SUPPORTED_CONTRACTS)}입니다. 플랫폼을 업데이트하세요.")
        return app
    app.cli, app.installed = _resolve(folder, m.get("cli"))
    app.gui, _ = _resolve(folder, m.get("gui"))
    if app.cli is None:
        app.error = f"실행 파일을 찾지 못했습니다 ({folder}). 앱을 다시 설치하세요."
    return app


def default_scan_roots() -> list[Path]:
    roots = []
    if os.environ.get("LOCALAPPDATA"):
        roots.append(Path(os.environ["LOCALAPPDATA"]) / "Programs")
    for var in ("ProgramFiles", "ProgramFiles(x86)"):
        if os.environ.get(var):
            roots.append(Path(os.environ[var]))
    return roots


def discover(app_dirs: list[str], scan_roots: list[Path] | None = None) -> dict[str, App]:
    candidates = [Path(d) for d in app_dirs]
    for root in scan_roots if scan_roots is not None else default_scan_roots():
        if root.is_dir():
            candidates += sorted(p.parent for p in root.glob(f"*/{MANIFEST}"))
    apps: dict[str, App] = {}
    for folder in candidates:
        app = load_app(folder)
        if app is not None and app.id not in apps:
            apps[app.id] = app
    return apps
