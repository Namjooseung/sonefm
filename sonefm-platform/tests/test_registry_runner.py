"""앱 레지스트리 + 공통 실행기: 가짜 앱으로 성공·부분성공·실패·시간 초과·깨진 결과."""
import json
from pathlib import Path

from hub import registry
from hub.runner import run_command


def test_매니페스트로_앱_찾기_개발폴더와_설치폴더(tmp_path, fake_app):
    dev = fake_app("a")
    installed_root = tmp_path / "Programs"
    from tests.conftest import make_app
    make_app(installed_root / "B 앱", "b", exe=True)
    make_app(installed_root / "A 설치판", "a", exe=True)      # 같은 id: 개발 폴더가 먼저
    (installed_root / "다른 프로그램").mkdir()
    apps = registry.discover([str(dev)], [installed_root])
    assert set(apps) == {"a", "b"}
    assert apps["a"].dir == dev and not apps["a"].installed
    assert apps["b"].installed and apps["b"].cli[0].endswith("app.exe") and apps["b"].cli[1] == "--cli"


def test_모르는_계약_버전은_실행하지_않음(fake_app):
    app = registry.load_app(fake_app("new", contract=2))
    assert app.error and "업데이트" in app.error
    r = run_command(app, "run", {}, Path(app.dir) / "w")
    assert r["status"] == "error" and "업데이트" in r["message"]


def test_깨진_매니페스트와_실행파일_없음(tmp_path):
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "sonefm-app.json").write_text("{", encoding="utf-8")
    assert "읽지 못했습니다" in registry.load_app(bad).error
    gone = tmp_path / "gone"
    gone.mkdir()
    (gone / "sonefm-app.json").write_text(json.dumps(
        {"contract": 1, "id": "gone", "cli": {"exe": ["x.exe"], "dev": ["없음/python.exe"]}}), encoding="utf-8")
    assert "실행 파일" in registry.load_app(gone).error
    assert registry.load_app(tmp_path / "매니페스트없음") is None


def run(app_dir, mode, tmp_path, **kw):
    app = registry.load_app(app_dir)
    progress = []
    r = run_command(app, "run", {"mode": mode, "echo": "안녕"}, tmp_path / "w" / mode,
                    on_progress=lambda p, m: progress.append((p, m)), **kw)
    return r, progress


def test_성공_결과와_진행률(fake_app, tmp_path):
    r, progress = run(fake_app(), "ok", tmp_path)
    assert r["status"] == "ok" and r["exit_code"] == 0 and r["data"]["echo"] == "안녕"
    assert [p for p, _ in progress] == [10, 50, 90] and progress[0][1] == "단계 10"
    assert r["log"] == ["로그 한 줄"]   # @progress 줄은 로그에서 빠진다


def test_부분성공_실패(fake_app, tmp_path):
    r, _ = run(fake_app(), "partial", tmp_path)
    assert r["status"] == "partial" and r["exit_code"] == 2 and r["skipped"][0]["reason"] == "가짜 건너뜀"
    r, _ = run(fake_app(), "error", tmp_path)
    assert r["status"] == "error" and r["exit_code"] == 1


def test_시간_초과(fake_app, tmp_path):
    r, _ = run(fake_app(timeout=2), "sleep", tmp_path)
    assert r["status"] == "error" and "시간 초과" in r["message"]


def test_결과_없음_깨진_출력(fake_app, tmp_path):
    r, _ = run(fake_app(), "garbage", tmp_path)
    assert r["status"] == "error" and "결과를 남기지 않고" in r["message"] and "로그 한 줄" in r["message"]


def test_결과_파일_없으면_표준출력_마지막_줄(fake_app, tmp_path):
    r, _ = run(fake_app(), "stdout", tmp_path)
    assert r["status"] == "ok" and r["data"]["echo"] == "안녕"


def test_상태와_종료코드가_다르면_나쁜_쪽(fake_app, tmp_path):
    r, _ = run(fake_app(), "liar", tmp_path)
    assert r["status"] == "error"


def test_없는_명령(fake_app, tmp_path):
    app = registry.load_app(fake_app(commands=("run",)))
    r = run_command(app, "nothing", {}, tmp_path / "w")
    assert r["status"] == "error" and "nothing" in r["message"]
