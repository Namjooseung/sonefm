"""플랫폼용 명령줄 (app/cli.py): 계약 v1 결과 형식·종료 코드."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app import cli
from app.config import migrations_dir
from app.db.connection import connect
from app.db.migrate import apply_migrations
from app.services import programs as program_svc
from app.services import schedule as svc

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "s.db"
    c = connect(path)
    apply_migrations(c, migrations_dir())
    r = program_svc.save_program(c, {"name": "마을살이 정류장", "category": "교양", "cycle_type": "weekly",
                                     "start_episode": 41,
                                     "slots": [{"weekday": 1, "hour": 11, "role": "본방", "valid_from": "2026-01-05"}]})
    assert r["ok"]
    assert svc.generate(c, 2026, 11, "first_week")["ok"] and svc.generate(c, 2026, 11, "rest")["ok"]
    c.commit()
    c.close()
    return path


def run(tmp_path, *argv):
    out = tmp_path / "result.json"
    code = cli.main([*argv, "--result", str(out)])
    return code, json.loads(out.read_text(encoding="utf-8"))


def test_lookup_회차는_get_month_계산과_같다(tmp_path, db):
    code, r = run(tmp_path, "lookup", "--db", str(db), "--date", "2026-11-10", "--hour", "11")
    assert code == 0 and r["status"] == "ok" and r["count"] == 1
    assert r["data"]["name"] == "마을살이 정류장" and r["data"]["episode"] == 42
    assert r["data"]["label"] == "마을살이 정류장 42회" and r["data"]["kind"] == "본방"
    assert r["data"]["month_status"] == "작성중" and "작성중" in r["message"]
    assert set(r) == {"status", "count", "data", "outputs", "skipped", "message"}


def test_lookup_입력_파일(tmp_path, db):
    inp = tmp_path / "in.json"
    inp.write_text(json.dumps({"db": str(db), "date": "2026-11-03", "hour": 11}), encoding="utf-8")
    code, r = run(tmp_path, "lookup", "--input", str(inp))
    assert code == 0 and r["data"]["episode"] == 41


@pytest.mark.parametrize("argv, words", [
    (["--date", "2026-11-03", "--hour", "15"], "빈칸"),
    (["--date", "11월3일", "--hour", "11"], "날짜"),
    (["--date", "2026-11-03", "--hour", "23"], "시각"),
])
def test_lookup_실패는_종료코드_1(tmp_path, db, argv, words):
    code, r = run(tmp_path, "lookup", "--db", str(db), *argv)
    assert code == 1 and r["status"] == "error" and words in r["message"]


def test_lookup_db_없음(tmp_path):
    code, r = run(tmp_path, "lookup", "--db", str(tmp_path / "없음.db"), "--date", "2026-11-03", "--hour", "11")
    assert code == 1 and "없습니다" in r["message"]


def test_month(tmp_path, db):
    code, r = run(tmp_path, "month", "--db", str(db), "--year", "2026", "--month", "11")
    assert code == 0 and r["data"]["cells"]["2026-11-17|11"]["episode"] == 43


def test_set_prep_바꾸고_조회에_반영(tmp_path, db):
    code, r = run(tmp_path, "set-prep", "--db", str(db), "--date", "2026-11-10", "--hour", "11", "--status", "송출완료")
    assert code == 0, r
    _, r = run(tmp_path, "lookup", "--db", str(db), "--date", "2026-11-10", "--hour", "11")
    assert r["data"]["prep_status"] == "송출완료"
    code, r = run(tmp_path, "set-prep", "--db", str(db), "--date", "2026-11-10", "--hour", "11", "--status", "아무거나")
    assert code == 1


def test_programs(tmp_path, db, monkeypatch):
    monkeypatch.setenv("SONEFM_HOME", str(tmp_path / "home"))
    c = connect(db)
    pid = c.execute("SELECT id FROM programs WHERE name = '마을살이 정류장'").fetchone()[0]
    c.execute("INSERT INTO program_aliases (id, program_id, alias, created_at, updated_at)"
              " VALUES ('a1', ?, '정류장', 'x', 'x')", (pid,))
    c.execute("UPDATE programs SET start_date = '2026-01-05' WHERE id = ?", (pid,))
    c.commit()
    c.close()
    code, r = run(tmp_path, "programs", "--db", str(db))
    assert code == 0 and r["count"] == len(r["data"]["programs"])
    p = next(x for x in r["data"]["programs"] if x["name"] == "마을살이 정류장")
    assert p["name"] == "마을살이 정류장" and p["aliases"] == ["정류장"]
    assert p["is_fixed"] is False and p["is_special"] is False and p["start_date"] == "2026-01-05"
    assert set(p) == {"id", "name", "aliases", "start_date", "end_date", "is_fixed", "is_special"}
    names = lambda d: [x["name"] for x in run(tmp_path, "programs", "--db", str(db), "--active-on", d)[1]["data"]["programs"]]
    assert "마을살이 정류장" in names("2026-11-16") and "마을살이 정류장" not in names("2025-12-31")
    code, r = run(tmp_path, "programs", "--db", str(db), "--active-on", "내일")
    assert code == 1 and "YYYY-MM-DD" in r["message"]


def test_check_db(tmp_path, db):
    assert run(tmp_path, "check-db", "--db", str(db))[0] == 0
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"not sqlite" * 100)
    code, r = run(tmp_path, "check-db", "--db", str(bad))
    assert code == 1 and "아니" in r["message"]


def test_main_py_cli_진입점(tmp_path, db):
    """화면 없이 `app/main.py --cli`로 실행되고 표준출력 마지막 줄이 결과 JSON."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.run([sys.executable, "app/main.py", "--cli", "lookup", "--db", str(db),
                        "--date", "2026-11-03", "--hour", "11"],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout.strip().splitlines()[-1])["data"]["episode"] == 41


def test_매니페스트(tmp_path):
    m = json.loads((ROOT / "sonefm-app.json").read_text(encoding="utf-8"))
    assert m["contract"] == 1 and m["id"] == "sonefm-schedule"
    assert set(m["commands"]) == set(cli.COMMANDS)
    from app import APP_VERSION
    assert m["version"] == APP_VERSION
