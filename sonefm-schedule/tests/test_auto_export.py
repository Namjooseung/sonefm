"""드라이브 폴더 자동 내보내기: 임시 폴더에서만 돈다(실제 드라이브·Documents 안 씀)."""
import sqlite3

from app.api import Api
from app.config import load_config
from app.services import auto_export as AE
from app.startup import prepare


def _api(folder=""):
    cfg = prepare().config
    cfg.auto_export_dir = str(folder)
    return Api(cfg)


def _enable(api, folder):
    """폴더를 정하고 자동 내보내기를 켠다(바로 한 번 내보냄)."""
    assert api.drive_folder_set(str(folder))["ok"]
    return api.auto_export_enable(True)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _exporter(cfg, clock):
    return AE.AutoExporter(cfg, clock=clock, debounce=60, use_timer=False)


def _count(path):
    c = sqlite3.connect(path)
    try:
        return c.execute("select count(*) from programs").fetchone()[0]
    finally:
        c.close()


def test_폴더가_비면_아무것도_안_함(tmp_path):
    api = _api("")
    api.exporter.mark_changed()
    assert api.exporter.dirty is False
    assert api.exporter.flush_on_close() is False
    assert api.exporter.export_now() == {"ok": True, "skipped": True}
    assert not (tmp_path / AE.EXPORT_NAME).exists()


def test_임시파일_후_교체하고_결과가_열리고_내용이_같다(tmp_path):
    dest = tmp_path / "드라이브"
    dest.mkdir()
    api = _api(dest)
    r = _enable(api, dest)
    assert r["ok"], r
    out = dest / AE.EXPORT_NAME
    assert out.is_file()
    assert not (dest / AE.TMP_NAME).exists()
    assert _count(out) == _count(api._cfg.db_path)
    assert [p.name for p in dest.iterdir()] == [AE.EXPORT_NAME]   # 허브는 *.db만 고름
    assert api.auto_export_status()["ok"] is True


def test_기존_파일을_덮어씀(tmp_path):
    dest = tmp_path / "d"
    dest.mkdir()
    (dest / AE.EXPORT_NAME).write_bytes(b"old")
    api = _api(dest)
    r = _enable(api, dest)
    assert r["ok"], r
    assert _count(dest / AE.EXPORT_NAME) == _count(api._cfg.db_path)


def test_폴더가_없으면_실패_상태만_남김(tmp_path):
    api = _api("")
    api._cfg.auto_export_dir = str(tmp_path / "없는폴더")   # 드라이브가 꺼진 상황
    result = api.exporter.export_now()
    assert result["ok"] is False
    st = api.exporter.status()
    assert st["last_ok"] is False and "드라이브" in st["last_message"]
    assert not (tmp_path / "없는폴더").exists()   # 폴더를 만들지 않는다
    # 화면에서 없는 폴더를 고르면 설정은 바뀌지 않는다
    r = api.drive_folder_set(str(tmp_path / "또없음"))
    assert r["ok"] is False


def test_새_PC_상태는_ok_True(tmp_path):
    api = _api("")
    r = api.auto_export_status()   # 상태 파일 없음: 마지막 결과 키가 ok를 덮으면 화면 카드가 사라진다
    assert r["ok"] is True and r["last_ok"] is None and r["last_at"] is None


def test_지난번_내보내기_실패_기록이_있어도_ok_True(tmp_path):
    api = _api("")
    api._cfg.auto_export_dir = str(tmp_path / "없는폴더")
    api.exporter.export_now()
    r = api.auto_export_status()
    assert r["ok"] is True and r["last_ok"] is False and r["last_message"]


def test_폴더를_비우면_꺼짐(tmp_path):
    dest = tmp_path / "d"
    dest.mkdir()
    api = _api(dest)
    _enable(api, dest)
    assert api.drive_folder_set("")["ok"]
    assert load_config().auto_export_dir == ""
    assert api.auto_export_status()["enabled"] is False


def test_디바운스_순수함수():
    assert AE.is_due(None, 100) is False
    assert AE.is_due(100, 159.9, 60) is False
    assert AE.is_due(100, 160, 60) is True


def test_디바운스_시간_주입(tmp_path):
    dest = tmp_path / "d"
    dest.mkdir()
    cfg = _api(dest)._cfg
    clock = Clock()
    ex = _exporter(cfg, clock)
    ex.mark_changed()
    clock.t += 30
    assert ex.flush_if_due() is False
    ex.mark_changed()              # 다시 바뀌면 60초가 새로 시작
    clock.t += 59
    assert ex.flush_if_due() is False
    assert not (dest / AE.EXPORT_NAME).exists()
    clock.t += 1
    assert ex.flush_if_due() is True
    assert (dest / AE.EXPORT_NAME).is_file()
    assert ex.dirty is False
    assert ex.flush_if_due() is False   # 변경이 없으면 다시 안 함


def test_닫을_때는_변경이_있을_때만(tmp_path):
    dest = tmp_path / "d"
    dest.mkdir()
    cfg = _api(dest)._cfg
    ex = _exporter(cfg, Clock())
    assert ex.flush_on_close() is False
    assert not (dest / AE.EXPORT_NAME).exists()
    ex.mark_changed()
    assert ex.flush_on_close() is True
    assert (dest / AE.EXPORT_NAME).is_file()
    assert ex.flush_on_close() is False
    assert AE.should_export_on_close(True, False) is False
    assert AE.should_export_on_close(False, True) is False


def test_실패하면_변경이_남아_닫을_때_다시_시도(tmp_path):
    cfg = _api(tmp_path / "없음")._cfg
    ex = _exporter(cfg, Clock())
    ex.mark_changed()
    assert ex.flush_on_close() is False
    assert ex.dirty is True


def test_바꾸는_API_성공_뒤에만_변경_표시(tmp_path):
    dest = tmp_path / "d"
    dest.mkdir()
    api = _api(dest)
    api.exporter._use_timer = False
    r = api.save_program({"name": "테스트", "category": "교양", "cycle_type": "weekly",
                          "slots": [{"weekday": 1, "hour": 11, "role": "본방", "valid_from": "2026-01-05"}]})
    assert r["ok"], r
    assert api.exporter.dirty is True
    api.exporter._exported_seq = api.exporter._seq
    api.list_programs("", True)
    api.get_month(2026, 11)
    assert api.exporter.dirty is False
    api.save_program({"name": ""})   # 실패
    assert api.exporter.dirty is False


def _slow_export(monkeypatch):
    """export_copy를 이벤트로 붙잡아 '내보내기 진행 중' 상황을 만든다."""
    import threading
    started, release = threading.Event(), threading.Event()
    real = AE.export_copy

    def slow(db, folder):
        started.set()
        release.wait(5)
        return real(db, folder)

    monkeypatch.setattr(AE, "export_copy", slow)
    return started, release


def test_내보내기_도중_닫으면_끝난_뒤_마지막_변경까지_반영(tmp_path, monkeypatch):
    import threading
    dest = tmp_path / "d"
    dest.mkdir()
    cfg = _api(dest)._cfg
    ex = AE.AutoExporter(cfg, clock=Clock(), debounce=60, use_timer=False, close_wait=5)
    ex.mark_changed()
    started, release = _slow_export(monkeypatch)
    t = threading.Thread(target=ex.flush_if_due)
    ex._last_change = -1000.0    # 디바운스 지남
    t.start()
    assert started.wait(5)
    ex.mark_changed()            # 내보내기 도중 들어온 변경
    closer = {}
    c = threading.Thread(target=lambda: closer.update(r=ex.flush_on_close()))
    c.start()
    release.set()
    t.join(5)
    c.join(5)
    assert closer["r"] is True
    assert ex.dirty is False


def test_닫기_대기_상한을_넘기면_기다리지_않고_False(tmp_path, monkeypatch):
    import threading
    dest = tmp_path / "d"
    dest.mkdir()
    cfg = _api(dest)._cfg
    ex = AE.AutoExporter(cfg, clock=Clock(), debounce=60, use_timer=False, close_wait=0.05)
    ex.mark_changed()
    started, release = _slow_export(monkeypatch)
    t = threading.Thread(target=ex.export_now)
    t.start()
    assert started.wait(5)
    ex.mark_changed()
    assert ex.flush_on_close() is False
    assert ex.dirty is True
    release.set()
    t.join(5)
