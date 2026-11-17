"""드라이브 폴더 자동 내보내기: 임시 폴더에서만 돈다(실제 드라이브·Documents 안 씀)."""
import sqlite3

from app.api import Api
from app.config import load_config
from app.services import auto_export as AE
from app.startup import prepare


def add_cells(db_path, n, month="2026-11"):
    """시험용: 첫 프로그램으로 편성 칸 n개를 넣는다(견본 DB는 칸이 0)."""
    c = sqlite3.connect(db_path)
    try:
        pid = c.execute("select id from programs limit 1").fetchone()[0]
        c.execute("delete from schedule_cells")
        for i in range(n):
            c.execute("insert into schedule_cells (id, date, hour, program_id, kind) values (?,?,?,?, '본방')",
                      (f"cell-{month}-{i}", f"{month}-{1 + i // 17:02d}", 6 + i % 17, pid))
        c.commit()
    finally:
        c.close()


def _api(folder="", cells=3):
    cfg = prepare().config
    cfg.auto_export_dir = str(folder)
    add_cells(cfg.db_path, cells)   # cells=0이면 칸을 모두 지운다
    return Api(cfg)


def _enable(api, folder):
    """폴더를 정하고 자동 내보내기를 켠다(켜도 바로 보내지 않는다)."""
    assert api.drive_folder_set(str(folder))["ok"]
    return api.auto_export_enable(True)


def _cells(path):
    c = sqlite3.connect(path)
    try:
        return c.execute("select count(*) from schedule_cells").fetchone()[0]
    finally:
        c.close()


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
    assert not (dest / AE.EXPORT_NAME).exists()   # 켜는 순간에는 보내지 않는다
    r = api.drive_export_run()
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
    assert (dest / AE.EXPORT_NAME).read_bytes() == b"old"   # 켜기만으로는 안 덮음
    r = api.drive_export_run()
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

    def slow(db, folder, **kw):
        started.set()
        release.wait(5)
        return real(db, folder, **kw)

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


# ── 빈 편성 방지 · 덮기 전 보관 · 수동 보내기 ──

def _drive_with(api, folder, cells):
    """드라이브 폴더에 칸이 cells개인 편성 파일을 둔다."""
    from pathlib import Path
    from app.services.data import _copy_db
    dest = Path(folder) / AE.EXPORT_NAME
    _copy_db(Path(api._cfg.db_path), dest)
    add_cells(dest, cells, month="2027-03")
    return dest


def test_요약_함수(tmp_path):
    from app.services.export_summary import summarize_drive_file, summarize_local
    api = _api(tmp_path, cells=20)
    s = summarize_local(api._cfg.db_path)
    assert s["cells"] == 20 and s["last_month"] == "2026-11" and s["programs"] >= 1
    assert summarize_drive_file(api._cfg.db_path)["cells"] == 20
    empty = _api(tmp_path, cells=0)
    assert summarize_local(empty._cfg.db_path)["last_month"] is None
    assert summarize_local(tmp_path / "없음.db") is None
    (tmp_path / "깨짐.db").write_bytes(b"not a db")
    assert summarize_drive_file(tmp_path / "깨짐.db") is None


def test_막는_판단_순수함수():
    from app.domain import export_guard as G
    assert G.block_reason(0, None, True) == G.EMPTY_LOCAL
    assert G.block_reason(0, 5, False) == G.EMPTY_LOCAL
    assert G.block_reason(3, 5, False) == G.DRIVE_AHEAD
    assert G.block_reason(3, 5, True) is None
    assert G.block_reason(5, 5, False) is None
    assert G.block_reason(3, None, False) is None
    assert G.last_month_of("2026-11-30") == "2026-11" and G.last_month_of(None) is None
    assert G.drive_is_ahead({"cells": 3, "last_month": "2026-11"}, {"cells": 3, "last_month": "2026-12"}) is True
    assert G.drive_is_ahead({"cells": 3, "last_month": "2026-11"}, {"cells": 2, "last_month": "2026-11"}) is False


def test_켤_때_내보내지_않음(tmp_path):
    dest = tmp_path / "d"
    dest.mkdir()
    api = _api(dest)
    r = _enable(api, dest)
    assert r["ok"], r
    assert list(dest.iterdir()) == []
    assert api.auto_export_status()["last_at"] is None


def test_빈_편성은_자동_수동_닫을_때_모두_안_보냄(tmp_path):
    dest = tmp_path / "d"
    dest.mkdir()
    api = _api(dest, cells=0)
    msg = "이 PC 편성이 비어 있어 드라이브로 보내지 않았습니다. 먼저 [드라이브에서 가져오기]를 하세요."
    r = api.drive_export_run()   # 수동
    assert r["ok"] is False and r["errors"] == [msg]
    assert api.exporter.status()["last_message"] == msg
    api._cfg.auto_export_enabled = True   # 자동(디바운스)
    ex = _exporter(api._cfg, Clock())
    ex.mark_changed()
    ex._last_change = -1000.0
    assert ex.flush_if_due() is False
    assert ex.flush_on_close() is False   # 닫을 때
    assert ex.dirty is True
    assert ex.status()["last_ok"] is False and ex.status()["last_message"] == msg
    assert list(dest.iterdir()) == []
    good = _drive_with(api, dest, 5)   # 기존 드라이브 파일도 그대로
    before = good.read_bytes()
    api.drive_export_run()
    ex.flush_on_close()
    assert good.read_bytes() == before


def test_드라이브가_더_많으면_자동은_안_보내고_수동은_보냄(tmp_path):
    dest = tmp_path / "d"
    dest.mkdir()
    api = _api(dest, cells=3)
    drive = _drive_with(api, dest, 10)
    before = drive.read_bytes()
    api._cfg.auto_export_enabled = True
    ex = _exporter(api._cfg, Clock())
    ex.mark_changed()
    assert ex.flush_on_close() is False
    st = ex.status()
    assert st["last_ok"] is False
    assert st["last_message"] == "드라이브 편성이 이 PC보다 많아 자동으로 보내지 않았습니다. 맞다면 [드라이브로 보내기]를 눌러 주세요."
    assert drive.read_bytes() == before
    p = api.drive_export_preview()
    assert p["ok"] and p["ahead"] is True and p["drive"]["cells"] == 10 and p["local"]["cells"] == 3
    assert p["drive"]["last_month"] == "2027-03"
    r = api.drive_export_run({"name": p["name"], "mtime": p["mtime"], "size": p["size"]})
    assert r["ok"], r
    assert _cells(drive) == 3


def test_보내기_expected가_다르면_안_보냄(tmp_path):
    dest = tmp_path / "d"
    dest.mkdir()
    api = _api(dest, cells=3)
    drive = _drive_with(api, dest, 4)
    p = api.drive_export_preview()
    exp = {"name": p["name"], "mtime": p["mtime"], "size": p["size"]}
    _drive_with(api, dest, 9)      # 그 사이 드라이브 파일이 바뀜
    import os
    os.utime(drive, (p["mtime"] + 100, p["mtime"] + 100))
    r = api.drive_export_run(exp)
    assert r["ok"] is False and r["changed"] is True
    assert _cells(drive) == 9
    r2 = api.drive_export_run({"exists": False})   # 없을 줄 알았는데 생긴 경우
    assert r2["ok"] is False and r2["changed"] is True
    p2 = api.drive_export_preview()   # 새 정보로 다시 확인하면 보냄
    assert api.drive_export_run({"name": p2["name"], "mtime": p2["mtime"], "size": p2["size"]})["ok"]
    assert _cells(drive) == 3


def test_자동_내보내기가_꺼져_있어도_수동_보내기는_됨(tmp_path):
    dest = tmp_path / "d"
    dest.mkdir()
    api = _api("", cells=3)
    assert api.drive_folder_set(str(dest))["ok"]
    assert api.auto_export_status()["enabled"] is False
    p = api.drive_export_preview()
    assert p["ok"] and p["exists"] is False and p["drive"] is None
    r = api.drive_export_run({"exists": False})
    assert r["ok"], r
    assert _cells(dest / AE.EXPORT_NAME) == 3


def test_덮기_전_드라이브_파일을_백업_폴더에_보관(tmp_path):
    import re
    from pathlib import Path
    dest = tmp_path / "d"
    dest.mkdir()
    api = _api(dest, cells=3)
    _drive_with(api, dest, 7)
    r = api.drive_export_run()
    assert r["ok"], r
    kept = list(Path(api._cfg.backup_dir).glob("drive-before-export-*.db"))
    assert len(kept) == 1
    assert re.fullmatch(r"drive-before-export-\d{8}-\d{6}\.db", kept[0].name)
    assert _cells(kept[0]) == 7
    from app.backup import prune_backups   # 일반 백업 정리 규칙(schedule_*.db)은 이 파일을 지우지 않는다
    prune_backups(api._cfg.backup_dir, 3)
    assert kept[0].exists()


def test_보관_파일은_최근_개수만_남기고_자동은_간격을_둠(tmp_path):
    import os
    from datetime import datetime, timedelta
    from pathlib import Path
    from app.services.auto_export import backup_drive_file
    src = tmp_path / "src.db"
    src.write_bytes(b"x")
    bdir = tmp_path / "b"
    t0 = datetime(2026, 11, 17, 9, 0, 0)
    for i in range(AE.DRIVE_BACKUP_KEEP + 3):
        assert backup_drive_file(src, bdir, auto=False, now=t0 + timedelta(seconds=i))
    assert len(list(Path(bdir).glob("drive-before-export-*.db"))) == AE.DRIVE_BACKUP_KEEP
    now = datetime.now()
    newest = sorted(Path(bdir).glob("drive-before-export-*.db"))[-1]
    os.utime(newest, (now.timestamp(), now.timestamp()))
    assert backup_drive_file(src, bdir, auto=True, now=now) is None            # 방금 보관함
    assert backup_drive_file(src, bdir, auto=False, now=now + timedelta(minutes=1)) is not None
