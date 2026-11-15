"""편성 데이터 받기: 편성 직원이 드라이브 폴더에 '데이터 내보내기'한 .db → 플랫폼의 읽기 전용 사본.

플랫폼은 편성표 DB를 직접 열지 않는다. 검사·형식 맞춤은 편성표 앱의 check-db 명령에 맡긴다.
"""
from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from hub import db
from hub.paths import platform_dir
from hub.registry import App
from hub.runner import run_command

SCHEDULE_APP = "sonefm-schedule"


def snapshot_path() -> Path:
    return platform_dir() / "schedule" / "schedule.db"


def _current(info: dict[str, Any] | None, warning: str | None = None) -> dict[str, Any]:
    snap = snapshot_path()
    if info and snap.exists():
        return {"path": str(snap), "label": f"{info['name']} ({info['received_at'][5:16].replace('T', ' ')} 받음)",
                "received_at": info["received_at"], "warning": warning}
    return {"path": "", "label": "받은 편성 없음", "received_at": None, "warning": warning}


def receive(cfg: dict[str, Any], apps: dict[str, App], run: Callable[..., dict[str, Any]] = run_command) -> dict[str, Any]:
    """→ {path, label, received_at, warning}. 받은 사본이 없으면 path는 빈 값이고 warning에 이유가 있다."""
    inbox = cfg.get("schedule_inbox") or ""
    if not inbox:
        # 편성은 항상 편성 직원 PC에서 짠다(2026-11-15 결정). 이 PC의 편성표 앱은 비어 있으니 경고한다
        return {"path": "", "label": "편성 폴더 미설정", "received_at": None,
                "warning": "편성 폴더가 설정되지 않았습니다. [설정]에서 편성 담당이 '데이터 내보내기'로 저장하는 드라이브 폴더를 지정하세요."}
    info = db.get_meta("schedule_snapshot")
    folder = Path(inbox)
    if not folder.is_dir():
        return _current(info, f"편성 폴더를 찾지 못했습니다: {folder} (드라이브 데스크톱 앱이 켜져 있는지 확인하세요)")
    files = sorted(folder.glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return _current(info, "편성 폴더에 편성 데이터 파일(.db)이 없습니다. 편성 담당자에게 '데이터 내보내기'를 부탁하세요.")
    newest = files[0]
    st = newest.stat()
    source = {"name": newest.name, "mtime": st.st_mtime, "size": st.st_size}
    if info and info.get("source") == source and snapshot_path().exists():
        return _current(info)

    app = apps.get(SCHEDULE_APP)
    if app is None:
        return _current(info, "편성표 앱을 찾지 못해 새 편성 파일을 검사하지 못했습니다. 편성표 앱을 설치하세요.")
    work = platform_dir() / "schedule"
    work.mkdir(exist_ok=True)
    incoming = work / "incoming.db"
    try:
        shutil.copy2(newest, incoming)
    except OSError as e:
        return _current(info, f"편성 파일을 복사하지 못했습니다(동기화 중일 수 있습니다): {e}")
    result = run(app, "check-db", {"db": str(incoming)}, platform_dir() / "runs" / "check-db")
    if result["status"] != "ok":
        incoming.unlink(missing_ok=True)
        return _current(info, f"새 편성 파일({newest.name})을 쓸 수 없어 이전 편성을 씁니다: {result['message']}")
    os.replace(incoming, snapshot_path())
    info = {"name": newest.name, "source": source, "received_at": datetime.now().isoformat(timespec="seconds")}
    db.set_meta("schedule_snapshot", info)
    return _current(info)
