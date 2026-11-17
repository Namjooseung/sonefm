"""편성 DB 파일 하나의 요약(프로그램 수·편성 칸 수·마지막 달). 읽기 전용.

드라이브 파일은 동기화 중일 수 있어 바로 열지 않고 로컬 임시 복사본으로 읽는다.
"""
from __future__ import annotations

import logging
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from app.domain.export_guard import last_month_of
from app.repository import meta_repo

log = logging.getLogger("sonefm.export_summary")


def summarize_local(path: str | Path) -> dict[str, Any] | None:
    """로컬 DB 파일을 읽기 전용으로 열어 요약한다. 못 읽으면 None."""
    p = Path(path)
    if not p.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
        try:
            programs, cells, max_date = meta_repo.schedule_counts(conn)
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    return {"programs": programs, "cells": cells, "last_month": last_month_of(max_date)}


def summarize_drive_file(path: str | Path) -> dict[str, Any] | None:
    """드라이브 파일을 로컬 임시 폴더로 복사한 뒤 요약한다. 복사·읽기 실패면 None."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="sonefm_summary_"))
    try:
        tmp = tmp_dir / "copy.db"
        shutil.copyfile(path, tmp)
        return summarize_local(tmp)
    except OSError:
        log.warning("드라이브 파일 요약용 복사 실패: %s", path)
        return None
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
