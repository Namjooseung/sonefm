"""드라이브 폴더 자동 내보내기.

편성을 바꾸면 구글 드라이브 데스크톱 앱 폴더에 DB 사본을 고정 이름으로 올린다.
업무자동화 PC의 허브가 그 폴더의 .db를 읽는다. 사본만 올리며 쓰고 있는 DB는 옮기지 않는다.

- 언제: 마지막 변경 후 DEBOUNCE_SECONDS 동안 추가 변경이 없을 때 / 창을 닫을 때(미반영 변경이 있으면) / 폴더를 처음 고를 때
- 어떻게: 대상 폴더에 `.tmp`(.db로 끝나지 않음)로 쓴 뒤 os.replace로 교체 → 드라이브가 반쪽 파일을 올리지 않는다.
- 실패해도 앱은 멈추지 않는다. 로그와 상태(설정 화면)로만 알린다.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import AppConfig, data_root
from app.services.data import _copy_db

log = logging.getLogger("sonefm.auto_export")

EXPORT_NAME = "SoneFM편성표_데이터.db"
TMP_NAME = ".SoneFM편성표_데이터.db.tmp"
DEBOUNCE_SECONDS = 60.0
STATUS_FILE = "auto_export_status.json"
CLOSE_WAIT_SECONDS = 15.0   # 창을 닫을 때 진행 중인 내보내기를 기다리는 상한
IMPORT_WAIT_SECONDS = 10.0  # 가져오기 전에 진행 중인 내보내기를 기다리는 상한


def is_enabled(folder: str | None, enabled: bool | None = None) -> bool:
    """폴더가 비어 있으면 꺼진 것. enabled가 None(설정 키 없음)이면 폴더가 있을 때 켜짐, False면 꺼짐."""
    return bool((folder or "").strip()) and enabled is not False


def cfg_enabled(cfg: AppConfig) -> bool:
    return is_enabled(cfg.auto_export_dir, cfg.auto_export_enabled)


def is_due(last_change: float | None, now: float, debounce: float = DEBOUNCE_SECONDS) -> bool:
    """디바운스: 변경이 있었고, 마지막 변경 후 debounce초가 지났으면 내보낼 때."""
    return last_change is not None and now - last_change >= debounce


def should_export_on_close(enabled: bool, dirty: bool) -> bool:
    """창을 닫을 때: 켜져 있고 아직 내보내지 않은 변경이 있을 때만."""
    return enabled and dirty


def export_copy(db_path: str | Path, folder: str | Path) -> dict[str, Any]:
    """DB 사본을 folder에 고정 이름으로 쓴다(임시 파일 → 교체). 예외를 던지지 않는다."""
    target = Path(folder)
    if not target.is_dir():
        return {"ok": False, "errors": [
            "내보내기 폴더를 찾지 못했습니다. 구글 드라이브 데스크톱 앱이 켜져 있고 로그인돼 있는지 확인한 뒤, 설정에서 폴더를 다시 골라 주세요."]}
    tmp = target / TMP_NAME
    try:
        tmp.unlink(missing_ok=True)
        _copy_db(Path(db_path), tmp)
        os.replace(tmp, target / EXPORT_NAME)
    except Exception as e:  # PermissionError, sqlite3.Error 등: 앱을 멈추지 않는다
        log.exception("자동 내보내기 실패: %s", target)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return {"ok": False, "errors": [
            f"내보내기 폴더에 저장하지 못했습니다({type(e).__name__}). 폴더 권한과 드라이브 동기화 상태를 확인해 주세요."]}
    return {"ok": True, "path": str(target / EXPORT_NAME)}


class AutoExporter:
    """변경 표시 → 디바운스 → 백그라운드 내보내기. 동시에 두 번 돌지 않는다."""

    def __init__(self, cfg: AppConfig, clock: Callable[[], float] = time.monotonic,
                 debounce: float = DEBOUNCE_SECONDS, use_timer: bool = True,
                 close_wait: float = CLOSE_WAIT_SECONDS, import_wait: float = IMPORT_WAIT_SECONDS) -> None:
        self._cfg = cfg
        self._clock = clock
        self._debounce = debounce
        self._use_timer = use_timer
        self._close_wait = close_wait
        self._import_wait = import_wait
        self._state = threading.Lock()     # 변경 표시 보호
        self._running = threading.Lock()   # 내보내기 한 번에 하나
        self._last_change: float | None = None
        self._seq = 0                      # 변경 횟수 (내보내는 도중 생긴 변경을 놓치지 않기 위해)
        self._exported_seq = 0
        self._timer: threading.Timer | None = None

    # ── 상태 ──
    @property
    def dirty(self) -> bool:
        return self._seq != self._exported_seq

    def _status_path(self) -> Path:
        return data_root() / STATUS_FILE

    def _save_status(self, result: dict[str, Any]) -> None:
        status = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "ok": bool(result.get("ok")),
                  "message": "" if result.get("ok") else (result.get("errors") or [""])[0]}
        try:
            p = self._status_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
        except OSError:
            log.exception("자동 내보내기 상태 저장 실패")

    def status(self) -> dict[str, Any]:
        """설정 화면용: 켜짐 여부, 폴더, 마지막 시각·결과."""
        out: dict[str, Any] = {"enabled": cfg_enabled(self._cfg),
                               "folder": self._cfg.auto_export_dir, "at": None, "ok": None, "message": ""}
        try:
            raw = json.loads(self._status_path().read_text(encoding="utf-8"))
            out.update(at=raw.get("at"), ok=raw.get("ok"), message=raw.get("message", ""))
        except (OSError, ValueError):
            pass
        return out

    # ── 변경 표시·실행 ──
    def mark_changed(self) -> None:
        """편성 데이터를 바꾸는 호출이 성공한 뒤 부른다. 꺼져 있으면 아무것도 하지 않는다."""
        if not cfg_enabled(self._cfg):
            return
        with self._state:
            self._seq += 1
            self._last_change = self._clock()
            if self._use_timer:
                if self._timer:
                    self._timer.cancel()
                self._timer = threading.Timer(self._debounce, self.flush_if_due)
                self._timer.daemon = True
                self._timer.start()

    def flush_if_due(self) -> bool:
        """디바운스 시간이 지났고 미반영 변경이 있으면 내보낸다. 내보냈으면 True."""
        with self._state:
            due = self.dirty and is_due(self._last_change, self._clock(), self._debounce)
        return self._export() if due else False

    def flush_on_close(self) -> bool:
        """창을 닫을 때: 진행 중인 내보내기가 있으면 상한 시간까지 기다린 뒤, 미반영 변경이 있으면 한 번 내보낸다."""
        with self._state:
            if self._timer:
                self._timer.cancel()
            enabled = cfg_enabled(self._cfg)
        if not enabled:
            return False
        if not self._running.acquire(timeout=self._close_wait):
            log.warning("닫는 중: 진행 중인 내보내기가 %s초 안에 끝나지 않아 기다리지 않고 닫습니다", self._close_wait)
            return False
        try:
            with self._state:
                go = should_export_on_close(enabled, self.dirty)
            return bool(self._do_export().get("ok")) if go else False
        finally:
            self._running.release()

    def export_now(self) -> dict[str, Any]:
        """폴더를 처음 고른 직후 등: 바로 한 번. 결과를 돌려준다."""
        if not cfg_enabled(self._cfg):
            return {"ok": True, "skipped": True}
        with self._running:
            return self._do_export()

    def hold(self) -> bool:
        """가져오기용: 진행 중인 내보내기가 끝나기를 상한까지 기다린 뒤 내보내기를 막는다. 못 막으면 False. 끝나면 release_hold()."""
        return self._running.acquire(timeout=self._import_wait)

    def release_hold(self) -> None:
        self._running.release()

    def _export(self) -> bool:
        if not self._running.acquire(blocking=False):
            return False   # 이미 돌고 있음. 변경이 남아 있으면 닫을 때나 다음 변경 때 다시 한다
        try:
            return bool(self._do_export().get("ok"))
        finally:
            self._running.release()

    def _do_export(self) -> dict[str, Any]:
        folder = self._cfg.auto_export_dir
        if not cfg_enabled(self._cfg):
            return {"ok": True, "skipped": True}
        with self._state:
            seq = self._seq
        result = export_copy(self._cfg.db_path, folder)
        if result.get("ok"):
            with self._state:
                self._exported_seq = max(self._exported_seq, seq)
            log.info("자동 내보내기 완료: %s", result.get("path"))
        else:
            log.warning("자동 내보내기 실패: %s", result.get("errors"))
        self._save_status(result)
        return result
