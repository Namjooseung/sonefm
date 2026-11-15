import json
import sys
from pathlib import Path

import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError

FAKE = Path(__file__).with_name("fake_app.py")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """실제 Documents\\SoneFM과 설치된 앱을 건드리지 않는다."""
    monkeypatch.setenv("SONEFM_HOME", str(tmp_path / "SoneFM"))
    for var in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)"):
        monkeypatch.setenv(var, str(tmp_path / "none"))
    return tmp_path


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        self.data = {}

    def get_password(self, service, username):
        return self.data.get((service, username))

    def set_password(self, service, username, password):
        self.data[(service, username)] = password

    def delete_password(self, service, username):
        if (service, username) not in self.data:
            raise PasswordDeleteError("없음")
        del self.data[(service, username)]


def pytest_addoption(parser):
    parser.addoption("--real-ai", action="store_true", default=False, help="실제 Anthropic API 테스트(키는 자격 증명 관리자에서)")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--real-ai"):
        return
    skip = pytest.mark.skip(reason="--real-ai 를 줄 때만 실행")
    for item in items:
        if "real_ai" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def memory_keyring(request):
    """실제 Windows 자격 증명 관리자를 건드리지 않는다. real_ai 테스트만 예외(읽기만 한다)."""
    if request.node.get_closest_marker("real_ai"):
        yield None
        return
    original = keyring.get_keyring()
    mem = MemoryKeyring()
    keyring.set_keyring(mem)
    yield mem
    keyring.set_keyring(original)


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
