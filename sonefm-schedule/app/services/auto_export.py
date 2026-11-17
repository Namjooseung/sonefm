"""드라이브 폴더 자동 내보내기.

편성을 바꾸면 구글 드라이브 데스크톱 앱 폴더에 DB 사본을 고정 이름으로 올린다.
업무자동화 PC의 허브가 그 폴더의 .db를 읽는다. 사본만 올리며 쓰고 있는 DB는 옮기지 않는다.

- 언제: 마지막 변경 후 DEBOUNCE_SECONDS 동안 추가 변경이 없을 때 / 창을 닫을 때(미반영 변경이 있으면). 켤 때는 보내지 않는다.
- 막는 조건(export_copy): 이 PC 편성 칸 0 → 항상 막음 / 드라이브가 칸이 더 많으면 자동은 막음(수동 [드라이브로 보내기]는 확인 후 가능)
- 덮어쓰기 전에 드라이브의 기존 파일을 이 PC 백업 폴더에 보관한다.
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

import shutil

from app.config import AppConfig, data_root
from app.domain.export_guard import block_reason, drive_is_ahead
from app.services.data import _copy_db
from app.services.export_summary import summarize_drive_file, summarize_local

log = logging.getLogger("sonefm.auto_export")

EXPORT_NAME = "SoneFM편성표_데이터.db"
TMP_NAME = ".SoneFM편성표_데이터.db.tmp"
DEBOUNCE_SECONDS = 60.0
STATUS_FILE = "auto_export_status.json"
CLOSE_WAIT_SECONDS = 15.0   # 창을 닫을 때 진행 중인 내보내기를 기다리는 상한
IMPORT_WAIT_SECONDS = 10.0  # 가져오기 전에 진행 중인 내보내기를 기다리는 상한
DRIVE_BACKUP_PREFIX = "drive-before-export-"
DRIVE_BACKUP_KEEP = 20                  # 덮어쓰기 전 보관 파일은 최근 20개만 (일반 백업 정리 규칙과 별개)
DRIVE_BACKUP_AUTO_GAP_SECONDS = 1800.0  # 자동 내보내기는 30분에 한 번만 보관


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


def _fmt(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _signature(p: Path) -> dict[str, Any] | None:
    try:
        st = p.stat()
    except OSError:
        return None
    return {"name": p.name, "mtime": st.st_mtime, "size": st.st_size}


def _same_signature(cur: dict[str, Any] | None, expected: dict[str, Any]) -> bool:
    """expected가 {"exists": False}면 파일이 없어야 같다. 아니면 name·size·mtime이 같아야 한다."""
    if expected.get("exists") is False:
        return cur is None
    if cur is None:
        return False
    return (cur["name"] == expected.get("name") and expected.get("size") == cur["size"]
            and abs(float(expected.get("mtime", -1)) - cur["mtime"]) < 0.001)


def backup_drive_file(existing: Path, backup_dir: str | Path, auto: bool,
                      now: datetime | None = None) -> Path | None:
    """덮어쓰기 전에 드라이브의 기존 파일을 이 PC 백업 폴더에 `drive-before-export-YYYYMMDD-HHMMSS.db`로 복사한다.

    이 이름은 일반 백업(schedule_*.db) 정리 규칙에 걸리지 않으므로 여기서 따로 최근 DRIVE_BACKUP_KEEP개만 남긴다.
    자동 내보내기는 변경마다 돌 수 있어 마지막 보관 후 DRIVE_BACKUP_AUTO_GAP_SECONDS 안이면 건너뛴다(수동은 항상 보관).
    """
    folder = Path(backup_dir)
    folder.mkdir(parents=True, exist_ok=True)
    olds = sorted(p for p in folder.iterdir() if p.is_file() and p.name.startswith(DRIVE_BACKUP_PREFIX) and p.name.endswith(".db"))
    stamp_now = now or datetime.now()
    if auto and olds:
        try:
            if stamp_now.timestamp() - olds[-1].stat().st_mtime < DRIVE_BACKUP_AUTO_GAP_SECONDS:
                return None
        except OSError:
            pass
    dest = folder / f"{DRIVE_BACKUP_PREFIX}{stamp_now.strftime('%Y%m%d-%H%M%S')}.db"
    n = 1
    while dest.exists():
        dest = folder / f"{DRIVE_BACKUP_PREFIX}{stamp_now.strftime('%Y%m%d-%H%M%S')}-{n}.db"
        n += 1
    shutil.copyfile(existing, dest)
    olds = sorted(p for p in folder.iterdir() if p.is_file() and p.name.startswith(DRIVE_BACKUP_PREFIX) and p.name.endswith(".db"))
    for p in olds[:-DRIVE_BACKUP_KEEP]:
        try:
            p.unlink()
        except OSError:
            pass
    return dest


def export_copy(db_path: str | Path, folder: str | Path, manual: bool = False,
                backup_dir: str | Path | None = None, expected: dict[str, Any] | None = None) -> dict[str, Any]:
    """DB 사본을 folder에 고정 이름으로 쓴다(임시 파일 → 교체). 예외를 던지지 않는다.

    모든 내보내기(자동·닫을 때·수동)가 여기를 지나므로 막는 조건은 여기서만 건다.
    - 이 PC 편성 칸이 0이면 보내지 않는다.
    - 자동(manual=False)일 때 드라이브 파일의 칸 수가 이 PC보다 많으면 보내지 않는다.
    - expected(미리보기의 드라이브 파일 정보)가 있고 지금 파일과 다르면 보내지 않고 changed를 돌려준다.
    - 덮어쓰기 전, 기존 드라이브 파일을 backup_dir에 보관한다(backup_dir가 있을 때).
    """
    target = Path(folder)
    if not target.is_dir():
        return {"ok": False, "errors": [
            "내보내기 폴더를 찾지 못했습니다. 구글 드라이브 데스크톱 앱이 켜져 있고 로그인돼 있는지 확인한 뒤, 설정에서 폴더를 다시 골라 주세요."]}
    local = summarize_local(db_path)
    if local is None:
        return {"ok": False, "errors": ["이 PC의 편성 데이터를 읽지 못해 드라이브로 보내지 않았습니다."]}
    existing = target / EXPORT_NAME
    if expected is not None and not _same_signature(_signature(existing), expected):
        return {"ok": False, "changed": True, "errors": [
            "확인한 뒤에 드라이브의 편성 파일이 바뀌었습니다. 새 정보를 다시 확인해 주세요."]}
    drive_cells: int | None = None
    if not manual and existing.is_file():
        d = summarize_drive_file(existing)
        drive_cells = d["cells"] if d else None
    reason = block_reason(local["cells"], drive_cells, manual)
    if reason:
        return {"ok": False, "blocked": True, "errors": [reason]}
    tmp = target / TMP_NAME
    try:
        if backup_dir is not None and existing.is_file():
            backup_drive_file(existing, backup_dir, auto=not manual)
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


def preview_export(cfg: AppConfig) -> dict[str, Any]:
    """[드라이브로 보내기] 확인 창용: 드라이브·이 PC 편성 요약, 경고 여부, 드라이브 파일 정보(expected로 되돌려 받음)."""
    folder = (cfg.auto_export_dir or "").strip()
    if not folder:
        return {"ok": False, "errors": ["드라이브 폴더를 먼저 골라 주세요."]}
    d = Path(folder)
    if not d.is_dir():
        return {"ok": False, "errors": [
            "드라이브 폴더를 찾지 못했습니다. 구글 드라이브 데스크톱 앱이 켜져 있고 로그인돼 있는지 확인하고, 폴더를 다시 골라 주세요."]}
    local = summarize_local(cfg.db_path)
    if local is None:
        return {"ok": False, "errors": ["이 PC의 편성 데이터를 읽지 못했습니다. 프로그램을 다시 켜 보고, 계속되면 담당자에게 알려 주세요."]}
    out: dict[str, Any] = {"ok": True, "local": local, "local_empty": local["cells"] <= 0,
                           "drive": None, "exists": False, "ahead": False, "name": EXPORT_NAME}
    sig = _signature(d / EXPORT_NAME)
    if sig is not None:
        summary = summarize_drive_file(d / EXPORT_NAME)
        out.update(exists=True, mtime=sig["mtime"], size=sig["size"], modified=_fmt(sig["mtime"]),
                   drive=summary, ahead=drive_is_ahead(local, summary))
    return out


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
                               "folder": self._cfg.auto_export_dir, "last_at": None, "last_ok": None, "last_message": ""}
        try:
            raw = json.loads(self._status_path().read_text(encoding="utf-8"))
            out.update(last_at=raw.get("at"), last_ok=raw.get("ok"), last_message=raw.get("message", ""))
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

    def export_manual(self, expected: dict[str, Any] | None = None) -> dict[str, Any]:
        """[드라이브로 보내기]: 자동 내보내기 켜짐 여부와 상관없이 폴더만 있으면 보낸다.

        진행 중인 내보내기가 있으면 상한까지 기다린다. 이 PC가 비어 있으면 막고,
        expected(미리보기의 드라이브 파일 정보)와 지금 파일이 다르면 보내지 않고 changed를 돌려준다.
        """
        folder = (self._cfg.auto_export_dir or "").strip()
        if not folder:
            return {"ok": False, "errors": ["드라이브 폴더를 먼저 골라 주세요."]}
        if not self._running.acquire(timeout=self._import_wait):
            return {"ok": False, "errors": ["자동 내보내기가 진행 중입니다. 잠시 뒤 다시 눌러 주세요."]}
        try:
            with self._state:
                seq = self._seq
            result = export_copy(self._cfg.db_path, folder, manual=True,
                                 backup_dir=self._cfg.backup_dir, expected=expected)
            if result.get("changed"):
                return result
            if result.get("ok"):
                with self._state:
                    self._exported_seq = max(self._exported_seq, seq)
                result["message"] = "드라이브로 보냈습니다."
                log.info("수동 내보내기 완료: %s", result.get("path"))
            else:
                log.warning("수동 내보내기 실패: %s", result.get("errors"))
            self._save_status(result)
            return result
        finally:
            self._running.release()

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
        result = export_copy(self._cfg.db_path, folder, manual=False, backup_dir=self._cfg.backup_dir)
        if result.get("ok"):
            with self._state:
                self._exported_seq = max(self._exported_seq, seq)
            log.info("자동 내보내기 완료: %s", result.get("path"))
        else:
            log.warning("자동 내보내기 실패: %s", result.get("errors"))
        self._save_status(result)
        return result
