"""드라이브 폴더에서 가져오기: 임시 폴더에서만 돈다(실제 드라이브·Documents 안 씀)."""
import os
import sqlite3
from pathlib import Path

from app.api import Api
from app.config import data_root, load_config
from app.services import auto_export as AE
from app.services import drive_import as DI
from app.services.data import _copy_db
from app.startup import prepare


def _api(folder=""):
    cfg = prepare().config
    cfg.auto_export_dir = str(folder)
    return Api(cfg)


def _count(path):
    c = sqlite3.connect(path)
    try:
        return c.execute("select count(*) from programs").fetchone()[0]
    finally:
        c.close()


def _drive_file(api, folder, name=AE.EXPORT_NAME, extra=1):
    """이 PC DB를 복사하고 프로그램을 extra개 더 넣은 '편성 PC의 파일'을 드라이브 폴더에 둔다."""
    dest = Path(folder) / name
    _copy_db(Path(api._cfg.db_path), dest)
    c = sqlite3.connect(dest)
    try:
        for i in range(extra):
            row = c.execute("select * from programs limit 1").fetchone()
            cols = [d[0] for d in c.execute("select * from programs limit 1").description]
            data = dict(zip(cols, row))
            data["id"] = f"drive-import-{name}-{i}"
            data["name"] = f"가져오기시험{i}"
            c.execute(f"insert into programs ({','.join(cols)}) values ({','.join('?' * len(cols))})",
                      [data[k] for k in cols])
        c.commit()
    finally:
        c.close()
    return dest


def _set_mtime(p, t):
    os.utime(p, (t, t))


def test_최신_파일_고르기_순수함수():
    assert DI.pick_latest([("a.db", 10.0), ("b.db", 30.0), ("c.db", 20.0)]) == "b.db"
    assert DI.pick_latest([("a.db", 10.0), (".x.db.tmp", 99.0), ("메모.txt", 98.0)]) == "a.db"
    assert DI.pick_latest([(".SoneFM편성표_데이터.db.tmp", 5.0)]) is None
    assert DI.pick_latest([]) is None
    assert DI.is_older(10, 20) is True
    assert DI.is_older(20, 10) is False
    assert DI.is_older(10, None) is False


def test_폴더에서_가장_최근_db를_고르고_임시파일은_제외(tmp_path):
    d = tmp_path / "드라이브"
    d.mkdir()
    old = d / "SoneFM편성표_데이터_20261101.db"
    new = d / AE.EXPORT_NAME
    tmp = d / AE.TMP_NAME
    for p, t in ((old, 1000), (new, 2000), (tmp, 3000)):
        p.write_bytes(b"x")
        _set_mtime(p, t)
    got, err = DI.find_latest(str(d))
    assert err is None and got.name == AE.EXPORT_NAME


def test_폴더_미지정_없음_빈폴더는_안내_오류(tmp_path):
    assert "폴더를 먼저 골라" in DI.find_latest("")[1] or "폴더 고르기" in DI.find_latest("")[1]
    assert "찾지 못했습니다" in DI.find_latest(str(tmp_path / "없음"))[1]
    (tmp_path / "빈").mkdir()
    assert ".db" in DI.find_latest(str(tmp_path / "빈"))[1]
    api = _api(tmp_path / "빈")
    assert api.drive_import_preview()["ok"] is False
    assert api.drive_import_run()["ok"] is False


def test_미리보기와_오래된_파일_경고(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    api = _api(d)
    f = _drive_file(api, d)
    local = Path(api._cfg.db_path).stat().st_mtime
    _set_mtime(f, local - 3600)
    p = api.drive_import_preview()
    assert p["ok"] and p["name"] == AE.EXPORT_NAME and p["size_kb"] >= 1 and p["older_than_local"] is True
    _set_mtime(f, local + 3600)
    assert api.drive_import_preview()["older_than_local"] is False


def test_가져오기_후_내용_교체와_백업_생성(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    api = _api(d)
    before = _count(api._cfg.db_path)
    _drive_file(api, d, extra=2)
    backups_before = len(list(Path(api._cfg.backup_dir).glob("*")))
    r = api.drive_import_run()
    assert r["ok"], r
    assert _count(api._cfg.db_path) == before + 2
    assert len(list(Path(api._cfg.backup_dir).glob("*"))) == backups_before + 1
    assert r["safety_backup"]
    assert not (data_root() / "import_tmp").exists()   # 임시 복사본 정리
    assert api._undo == []


def test_깨진_파일이면_교체_안_함(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    api = _api(d)
    before = _count(api._cfg.db_path)
    (d / AE.EXPORT_NAME).write_bytes(b"not a database at all")
    r = api.drive_import_run()
    assert r["ok"] is False
    assert _count(api._cfg.db_path) == before


def test_가져오기는_자동내보내기_변경표시를_안_함(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    api = _api(d)
    _drive_file(api, d)
    api.exporter = AE.AutoExporter(api._cfg, use_timer=False)
    assert api.exporter.dirty is False
    r = api.drive_import_run()
    assert r["ok"] and r["export_on"] is True    # 켜져 있으면 덮어쓰기 안내용 플래그
    assert api.exporter.dirty is False
    # 비교: 복원은 예전처럼 변경 표시를 한다
    api.backup_restore(r["safety_backup"])
    assert api.exporter.dirty is True


def test_가져오기만_쓰는_PC는_켜지_않음(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    api = _api("")
    assert api.drive_folder_set(str(d))["ok"]
    assert api.auto_export_status()["enabled"] is False
    assert not (d / AE.EXPORT_NAME).exists()          # 폴더만 정했을 뿐 올리지 않는다
    assert load_config().auto_export_dir == str(d)
    _drive_file(api, d)
    assert api.drive_import_run()["export_on"] is False
    # 켜면 올림
    assert api.auto_export_enable(True)["ok"]
    assert api.auto_export_status()["enabled"] is True
    assert (d / AE.EXPORT_NAME).is_file()
    assert api.auto_export_enable(False)["ok"]
    assert load_config().auto_export_enabled is False


def test_예전_설정은_폴더가_있으면_켜짐으로_해석(tmp_path):
    assert AE.is_enabled(str(tmp_path), None) is True
    assert AE.is_enabled(str(tmp_path), False) is False
    assert AE.is_enabled("", True) is False
    assert AE.is_enabled(str(tmp_path), True) is True


def test_미리보기_뒤_더_새_파일이_생기면_가져오지_않음(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    api = _api(d)
    before = _count(api._cfg.db_path)
    f = _drive_file(api, d, extra=1)
    _set_mtime(f, 1000)
    p = api.drive_import_preview()
    exp = {"name": p["name"], "mtime": p["mtime"], "size": p["size"]}
    newer = _drive_file(api, d, name="더새파일.db", extra=3)
    _set_mtime(newer, 2000)
    r = api.drive_import_run(exp)
    assert r["ok"] is False and r["changed"] is True
    assert r["name"] == "더새파일.db" and r["mtime"] == 2000 and r["size_kb"] >= 1
    assert _count(api._cfg.db_path) == before
    # 새 정보로 다시 확인하면 가져옴
    r2 = api.drive_import_run({"name": r["name"], "mtime": r["mtime"], "size": r["size"]})
    assert r2["ok"], r2
    assert _count(api._cfg.db_path) == before + 3


def test_미리보기와_같은_파일이면_가져옴(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    api = _api(d)
    before = _count(api._cfg.db_path)
    _drive_file(api, d, extra=2)
    p = api.drive_import_preview()
    r = api.drive_import_run({"name": p["name"], "mtime": p["mtime"], "size": p["size"]})
    assert r["ok"], r
    assert _count(api._cfg.db_path) == before + 2


def test_자동_내보내기_진행_중이면_상한_뒤_오류_가져오지_않음(tmp_path, monkeypatch):
    import threading
    d = tmp_path / "d"
    d.mkdir()
    api = _api(d)
    before = _count(api._cfg.db_path)
    _drive_file(api, d, extra=1)
    api.exporter = AE.AutoExporter(api._cfg, use_timer=False, import_wait=0.05)
    started, release = threading.Event(), threading.Event()
    real = AE.export_copy

    def slow(db, folder):
        started.set()
        release.wait(5)
        return real(db, folder)

    monkeypatch.setattr(AE, "export_copy", slow)
    t = threading.Thread(target=api.exporter.export_now)
    t.start()
    assert started.wait(5)
    r = api.drive_import_run()
    assert r["ok"] is False and "자동 내보내기가 진행 중입니다. 잠시 뒤 다시 눌러 주세요." in r["errors"]
    assert _count(api._cfg.db_path) == before
    release.set()
    t.join(5)
    assert api.drive_import_run()["ok"]   # 끝난 뒤에는 가져옴
