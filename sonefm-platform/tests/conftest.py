import json
import sys
from pathlib import Path

import pytest

FAKE = Path(__file__).with_name("fake_app.py")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """실제 Documents\\SoneFM과 설치된 앱을 건드리지 않는다."""
    monkeypatch.setenv("SONEFM_HOME", str(tmp_path / "SoneFM"))
    for var in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)"):
        monkeypatch.setenv(var, str(tmp_path / "none"))
    return tmp_path


def make_app(folder: Path, app_id: str, commands=("run", "check-db", "lookup", "make-video"), contract=1,
             timeout=30, exe=False) -> Path:
    """가짜 앱 폴더: 매니페스트 + (exe=True면 가짜 설치 exe 자리)."""
    folder.mkdir(parents=True, exist_ok=True)
    manifest = {
        "contract": contract, "id": app_id, "name": f"가짜 {app_id}", "version": "0.1",
        "cli": {"exe": ["app.exe", "--cli"], "dev": [sys.executable, str(FAKE)]},
        "gui": {"dev": [sys.executable, "-c", "pass"]},
        "commands": {c: {"summary": c, "timeout": timeout, "input": {}, "output": {}} for c in commands},
    }
    if exe:
        (folder / "app.exe").write_bytes(b"")
    (folder / "sonefm-app.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return folder


@pytest.fixture
def fake_app(tmp_path):
    return lambda app_id="fake", **kw: make_app(tmp_path / "apps" / app_id, app_id, **kw)
