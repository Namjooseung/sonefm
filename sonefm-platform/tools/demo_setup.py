"""체험용 데이터 준비 (실행.bat이 부른다). 실제 문서\\SoneFM 대신 SONEFM_HOME(demo-data) 폴더를 쓴다.

- 설정: 옆 폴더의 개발 앱(sonefm-schedule, radio2yt), 체험용 편성 폴더·업무일지 폴더 (이미 있으면 그대로 둔다)
- 편성 폴더: 편성표 씨앗 DB를 '편성 담당이 내보낸 파일'처럼 넣는다
- 업무일지 폴더: 오늘 날짜의 견본 업무일지(CSV). 유튜브 업무는 견본 편성에서 오늘 이전 마지막 본방으로 만든다
- 견본 송출 mp3(ffmpeg가 있으면), 썸네일은 유튜브 편집기 기본 썸네일을 미리 채워 둔다
"""
from __future__ import annotations

import csv
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hub import db, paths, registry  # noqa: E402
from hub.runner import run_command  # noqa: E402

SONEFM = ROOT.parent
SCHEDULE, RADIO = SONEFM / "sonefm-schedule", SONEFM / "radio2yt"


def main() -> None:
    home = paths.home()
    inbox, worklog, sample = home / "편성 폴더", home / "업무일지 폴더", home / "견본"
    for d in (inbox, worklog, sample):
        d.mkdir(parents=True, exist_ok=True)
    if not paths.config_path().exists():
        paths.save_config({**paths.load_config(), "app_dirs": [str(SCHEDULE), str(RADIO)],
                           "worklog_folder": str(worklog), "schedule_inbox": str(inbox)})
    if not any(inbox.glob("*.db")):
        shutil.copy(SCHEDULE / "seed" / "schedule_seed.db", inbox / "편성_견본.db")

    today = date.today()
    slot = _last_slot(inbox, today)
    youtube = f"유튜브 {slot[0].month}/{slot[0].day} {slot[1]}시 {slot[2]}" if slot else "유튜브 11시 업로드"
    with (worklog / "업무일지_견본.csv").open("w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows([["날짜", "업무"], [today.isoformat(), youtube],
                                 [today.isoformat(), "협찬사 전화"], [today.isoformat(), "방송일지 정리"]])

    mp3 = sample / "송출_견본.mp3"
    if not mp3.exists() and shutil.which("ffmpeg"):
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=40",
                        str(mp3)], check=False)
    thumb = RADIO / "assets" / "thumbnails" / "기본.png"
    if thumb.exists() and not db.get_meta("remember:youtube-video"):
        db.set_meta("remember:youtube-video", {"thumb": str(thumb)})

    print(f"체험용 데이터: {home}")
    print(f"오늘 업무일지: {youtube} / 협찬사 전화 / 방송일지 정리")
    if slot and slot[0] != today:
        print(f"※ 견본 편성은 {slot[0]}까지만 있습니다. [실행] 창에서 방송 날짜를 {slot[0]}(으)로 바꾸세요.")
    print(f"견본 송출 mp3: {mp3 if mp3.exists() else '(ffmpeg가 없어 못 만듦 - 아무 mp3나 고르세요)'}")


def _last_slot(inbox: Path, today: date) -> tuple[date, int, str] | None:
    """견본 편성에서 오늘 또는 그 전 마지막 본방(날짜, 시각, 이름). 편성표 앱 명령줄로 묻는다."""
    from hub import schedule_inbox
    snap = schedule_inbox.receive(paths.load_config(), registry.discover([str(SCHEDULE)], []))["path"]
    app = registry.load_app(SCHEDULE)
    if not snap or app is None or app.error:
        return None
    best = None
    for y, m in ((today.year, today.month), (today.year - (today.month == 1), today.month - 1 or 12)):
        r = run_command(app, "month", {"year": y, "month": m, "db": snap}, paths.platform_dir() / "runs" / "demo-setup")
        for key, c in (r["data"].get("cells", {}) if r["status"] == "ok" else {}).items():
            d, h = date.fromisoformat(key.split("|")[0]), int(key.split("|")[1])
            if d <= today and c["kind"] == "본방" and not c["is_fixed"] and c["episode"] and c["show_episode"]:
                best = max(best or (d, h, c["name"]), (d, h, c["name"]))
        if best:
            return best
    return None


if __name__ == "__main__":
    main()
