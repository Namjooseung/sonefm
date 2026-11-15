"""실제 앱(옆 폴더의 개발 저장소)으로 유튜브 레시피 끝까지. 저장소·ffmpeg가 없으면 건너뛴다.

편성표 씨앗 DB를 '편성 직원이 내보낸 파일'로 편성 폴더에 넣고 → 플랫폼이 받기(check-db) →
month로 회차가 있는 본방 칸을 하나 고르고 → 레시피(lookup → make-video) → mp4·제목.
"""
import json
import shutil
import subprocess
import time
from datetime import date
from pathlib import Path

import pytest

from hub import paths, registry
from hub.api import Api
from hub.runner import run_command

SONEFM = Path(__file__).resolve().parents[2]
SCHEDULE, RADIO = SONEFM / "sonefm-schedule", SONEFM / "radio2yt"
pytestmark = pytest.mark.skipif(
    not ((SCHEDULE / ".venv").exists() and (RADIO / ".venv").exists() and shutil.which("ffmpeg")),
    reason="옆 폴더의 앱 개발 환경이나 ffmpeg가 없음")


def test_유튜브_레시피_실제_앱(tmp_path):
    inbox = tmp_path / "편성 폴더"
    inbox.mkdir()
    shutil.copy(SCHEDULE / "seed" / "schedule_seed.db", inbox / "편성_내보내기.db")
    paths.save_config({**paths.load_config(), "app_dirs": [str(SCHEDULE), str(RADIO)], "schedule_inbox": str(inbox)})

    api = Api(today=lambda: date(2026, 11, 16), download=lambda sid: b"")
    s = api.refresh()
    assert s["schedule"]["warning"] is None, s["schedule"]
    snap = s["schedule"]["path"]

    apps = registry.discover([str(SCHEDULE), str(RADIO)], [])
    m = run_command(apps["sonefm-schedule"], "month", {"year": 2026, "month": 11, "db": snap}, tmp_path / "m")
    assert m["status"] == "ok", m["message"]
    key, cell = next((k, c) for k, c in sorted(m["data"]["cells"].items())
                     if c["kind"] == "본방" and c["episode"] and c["show_episode"] and not c["is_fixed"] and k.startswith("2026-11"))
    day, hour = key.split("|")

    audio = tmp_path / "송출.mp3"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=30",
                    str(audio)], check=True)
    api.add_task(f"유튜브 {int(hour)}시 {cell['name']}")
    task = next(t for t in api.state()["tasks"] if t["source"] == "manual")
    assert task["recipe_id"] == "youtube-video"
    fields = {f["name"]: f["value"] for f in api.form(task["key"], "youtube-video")["fields"]}
    assert fields["hour"] == hour
    r = api.start(task["key"], "youtube-video", {**fields, "date": day, "audio": str(audio),
                                                 "thumb": str(RADIO / "assets" / "thumbnails" / "기본.png")})
    assert r["ok"], r
    for _ in range(600):
        st = api.run_status(r["run_id"])
        if st["done"]:
            break
        time.sleep(0.5)
    res = st["result"]
    assert res["status"] == "ok", json.dumps(res, ensure_ascii=False, default=str)[:3000]
    title = f"{cell['name']} {cell['episode']}회"
    assert res["steps"][0]["result"]["data"]["label"] == title
    video = res["steps"][1]["result"]["data"]
    assert video["title"] == title and Path(video["mp4"]).stat().st_size > 0
    assert Path(video["mp4"]).parent == paths.home() / "유튜브"
    assert {a["label"] for a in res["actions"]["copy"]} == {"제목 복사", "설명 복사"}
    assert api.state()["tasks"][-1]["mark"] == "완료"
