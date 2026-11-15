"""화면 API 흐름: 업무 목록(분류·수동), 레시피 실행 → 기록·완료 표시, 편성 파일 받기."""
import json
import os
import time
from datetime import date
from pathlib import Path

import pytest

from hub import db, paths, schedule_inbox
from hub.api import Api
from tests.test_recipes import RECIPE
from tests.test_worklog import xlsx

TODAY = date(2026, 11, 16)


@pytest.fixture
def api(tmp_path, fake_app, monkeypatch):
    rdir = tmp_path / "recipes"
    rdir.mkdir()
    (rdir / "two.json").write_text(json.dumps(RECIPE, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr("hub.api.recipe_dirs", lambda: [rdir])
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    paths.save_config({**paths.load_config(), "app_dirs": [str(fake_app("a")), str(fake_app("b")),
                                                           str(fake_app("sonefm-schedule"))],
                       "worklog_url": "https://docs.google.com/spreadsheets/d/" + "W" * 30,
                       "schedule_inbox": str(inbox)})
    sheet = xlsx([["날짜", "업무"], ["11/16", "유튜브 화 14시 마을살이"], ["11/16", "협찬사 전화"], ["11/15", "어제 업무"]])
    a = Api(today=lambda: TODAY, download=lambda sid: sheet)
    a.inbox = inbox
    return a


def wait(api, run_id):
    for _ in range(100):
        s = api.run_status(run_id)
        if s["done"]:
            return s
        time.sleep(0.1)
    raise AssertionError("끝나지 않음")


def test_오늘_업무와_분류(api):
    s = api.refresh()
    assert [(t["text"], t["recipe_name"]) for t in s["tasks"]] == [("유튜브 화 14시 마을살이", "두 단계"),
                                                                   ("협찬사 전화", "수동")]
    assert {a["id"] for a in s["apps"]} == {"a", "b", "sonefm-schedule"}
    assert s["worklog"]["last_date"] == "2026-11-16" and s["worklog"]["warning"] is None


def test_직접_입력_업무와_완료_체크(api):
    api.refresh()
    assert not api.add_task("  ")["ok"]
    api.add_task("유튜브 수 10시 직접")
    t = [t for t in api.state()["tasks"] if t["source"] == "manual"][0]
    assert t["recipe_id"] == "two"
    api.mark(t["key"], True)
    assert [x for x in api.state()["tasks"] if x["key"] == t["key"]][0]["mark"] == "완료"
    api.delete_task(t["id"])
    assert not [x for x in api.state()["tasks"] if x["source"] == "manual"]


def test_레시피_실행_기록과_완료_표시(api):
    s = api.refresh()
    key = s["tasks"][0]["key"]
    f = api.form(key, "two")
    assert {x["name"]: x["value"] for x in f["fields"]}["hour"] == "14"
    assert not api.start(key, "two", {"hour": "14", "date": "2026-11-16"})["ok"]     # 썸네일 빠짐
    r = api.start(key, "two", {"hour": "14", "thumb": "C:/t.png", "date": "2026-11-16", "mode": "ok"})
    assert r["ok"]
    # mode는 레시피 입력이 아니라서 버려진다 → {input.mode} 값 없음으로 1단계에서 실패해야 한다
    done = wait(api, r["run_id"])
    assert done["result"]["status"] == "error" and done["result"]["failed_step"] == 1
    hist = db.recent_runs()
    assert hist[0]["status"] == "error" and hist[0]["task_text"] == "유튜브 화 14시 마을살이"
    assert api.state()["tasks"][0]["mark"] is None and api.state()["tasks"][0]["last_run"]["status"] == "error"


def test_성공하면_업무_완료와_기억값(api, monkeypatch, tmp_path):
    recipe = {**RECIPE, "inputs": RECIPE["inputs"] + [{"name": "mode", "label": "모드"}]}
    (tmp_path / "recipes" / "two.json").write_text(json.dumps(recipe, ensure_ascii=False), encoding="utf-8")
    key = api.refresh()["tasks"][0]["key"]
    r = api.start(key, "two", {"hour": "14", "thumb": "C:/t.png", "date": "2026-11-16", "mode": "ok"})
    done = wait(api, r["run_id"])
    assert done["result"]["status"] == "ok", done["result"]["message"]
    assert done["result"]["actions"]["copy"][0]["text"] == "14회 · 유튜브 화 14시 마을살이"
    assert api.state()["tasks"][0]["mark"] == "완료"
    assert db.get_meta("remember:two") == {"thumb": "C:/t.png"}
    with db.connect() as c:
        steps = json.loads(c.execute("SELECT steps FROM runs").fetchone()[0])
    assert [s["app"] for s in steps] == ["a", "b"]


def test_편성_파일_받기_검사_통과와_실패(api):
    good = api.inbox / "편성_1114.db"
    good.write_bytes(b"x" * 10)
    s = api.refresh()["schedule"]
    assert s["warning"] is None and s["path"].endswith("schedule.db") and "편성_1114.db" in s["label"]
    assert Path(s["path"]).read_bytes() == b"x" * 10
    bad = api.inbox / "편성_bad.db"
    bad.write_bytes(b"bad")
    os.utime(bad, (time.time() + 10, time.time() + 10))
    s = api.refresh()["schedule"]
    assert "이전 편성을 씁니다" in s["warning"] and "편성_1114.db" in s["label"]
    assert Path(s["path"]).read_bytes() == b"x" * 10     # 실패하면 이전 사본 유지


def test_편성_폴더가_없으면_이_PC_데이터():
    assert schedule_inbox.receive({"schedule_inbox": ""}, {})["path"] == ""
