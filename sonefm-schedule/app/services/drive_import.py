"""드라이브 폴더에서 최신 편성 데이터 가져오기.

편성 PC가 드라이브 공유 폴더에 올린 .db 중 수정 시각이 가장 최근인 것을 이 PC의 편성으로 받는다.
드라이브 파일은 바로 열지 않고 로컬 임시 위치로 복사한 뒤, 기존 복원(data.restore: 검사·마이그레이션·백업·교체)을 쓴다.
"""
from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import AppConfig, data_root
from app.services import data as data_service
from app.services.export_summary import summarize_drive_file, summarize_local

log = logging.getLogger("sonefm.drive_import")
Result = dict[str, Any]

_NO_FOLDER = "드라이브 폴더가 정해져 있지 않습니다. 아래 '폴더 고르기'로 구글 드라이브 폴더(내 드라이브에 추가한 공유 폴더)를 먼저 골라 주세요."
_BAD_FOLDER = "드라이브 폴더를 찾지 못했습니다. 구글 드라이브 데스크톱 앱이 켜져 있고 로그인돼 있는지 확인하고, 폴더를 다시 골라 주세요."
_NO_FILE = "드라이브 폴더에 편성 데이터 파일(.db)이 없습니다. 편성 담당 PC에서 자동 내보내기가 켜져 있는지, 드라이브 동기화가 끝났는지 확인해 주세요."
EMPTY_DRIVE = "드라이브 파일이 비어 있습니다. 편성 담당 PC에서 다시 보내 달라고 하세요."


def pick_latest(files: list[tuple[str, float]]) -> str | None:
    """(이름, 수정 시각) 목록에서 .db로 끝나는 것 중 가장 최근 이름. 같으면 이름 순으로 뒤의 것. 없으면 None."""
    dbs = [(m, n) for n, m in files if n.lower().endswith(".db")]
    return max(dbs)[1] if dbs else None


def is_older(source_mtime: float, local_mtime: float | None) -> bool:
    """받을 파일이 이 PC의 데이터 파일보다 오래됐는가."""
    return local_mtime is not None and source_mtime < local_mtime


def _fmt(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def find_latest(folder: str | None) -> tuple[Path | None, str | None]:
    """(파일, 오류 문구). 폴더 미지정·없음·파일 없음은 할 일까지 안내하는 오류."""
    if not (folder or "").strip():
        return None, _NO_FOLDER
    d = Path(folder)
    if not d.is_dir():
        return None, _BAD_FOLDER
    entries: list[tuple[str, float]] = []
    for p in d.iterdir():
        if p.is_file() and p.name.lower().endswith(".db"):
            try:
                entries.append((p.name, p.stat().st_mtime))
            except OSError:
                continue
    name = pick_latest(entries)
    if name is None:
        return None, _NO_FILE
    return d / name, None


def preview(cfg: AppConfig) -> Result:
    """가져올 파일의 이름·수정 시각·크기, 이 PC 데이터보다 오래됐는지."""
    src, err = find_latest(cfg.auto_export_dir)
    if err or src is None:
        return {"ok": False, "errors": [err]}
    st = src.stat()
    try:
        local = Path(cfg.db_path).stat().st_mtime
    except OSError:
        local = None
    summary = summarize_drive_file(src)   # 칸 수·마지막 달. 못 읽으면 None(가져올 때 다시 검사한다)
    return {"ok": True, "name": src.name, "mtime": st.st_mtime, "size": st.st_size, "modified": _fmt(st.st_mtime), "size_kb": max(1, round(st.st_size / 1024)),
            "older_than_local": is_older(st.st_mtime, local),
            "local_modified": _fmt(local) if local is not None else None,
            "cells": summary["cells"] if summary else None,
            "programs": summary["programs"] if summary else None,
            "last_month": summary["last_month"] if summary else None,
            "empty": bool(summary and summary["cells"] <= 0)}


def import_latest(cfg: AppConfig, expected: dict[str, Any] | None = None) -> Result:
    """최신 파일을 로컬 임시 위치로 복사한 뒤 복원 경로(검사·마이그레이션·백업·교체)로 바꾼다.

    expected(미리보기가 돌려준 name·mtime·size)가 있으면 지금 고른 최신 파일이 그것과 같을 때만 가져온다.
    다르면 가져오지 않고 {"ok": False, "changed": True, 새 미리보기 정보}를 돌려준다.
    """
    src, err = find_latest(cfg.auto_export_dir)
    if err or src is None:
        return {"ok": False, "errors": [err]}
    if expected is not None:
        st = src.stat()
        same = (src.name == expected.get("name") and expected.get("size") == st.st_size
                and abs(float(expected.get("mtime", -1)) - st.st_mtime) < 0.001)
        if not same:
            return {**preview(cfg), "ok": False, "changed": True,
                    "errors": ["확인한 뒤에 드라이브 폴더의 최신 파일이 바뀌었습니다. 새 파일 정보를 다시 확인해 주세요."]}
    tmp_dir = data_root() / "import_tmp"
    tmp = tmp_dir / "drive_import.db"
    try:
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp.unlink(missing_ok=True)
        shutil.copyfile(src, tmp)   # 동기화 중인 파일을 직접 열지 않는다
    except OSError:
        log.exception("드라이브 파일 복사 실패: %s", src)
        shutil.rmtree(tmp_dir, ignore_errors=True)   # 부분 복사본 정리
        return {"ok": False, "errors": ["드라이브 파일을 가져오지 못했습니다. 동기화가 끝난 뒤 다시 시도해 주세요."]}
    try:
        summary = summarize_local(tmp)
        if summary is not None and summary["cells"] <= 0:
            return {"ok": False, "errors": [EMPTY_DRIVE]}
        result = data_service.restore(cfg, str(tmp))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    if result.get("ok"):
        result["message"] = f"드라이브의 {src.name}(으)로 편성을 바꿨습니다."
        result["source_name"] = src.name
    return result
