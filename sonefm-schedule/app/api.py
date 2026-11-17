"""화면(js_api)에 노출하는 함수들. 얇게 유지하고 실제 일은 services/domain/repository에 맡긴다.

모든 함수는 예외를 밖으로 던지지 않는다. 예상 못 한 오류는 로그에 남기고
{"ok": False, "errors": ["...한국어 안내..."]} 를 돌려준다.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from collections.abc import Callable
from typing import Any

from app import APP_VERSION
from app.backup import backup_db
from app.config import AppConfig, save_config
from app.db.connection import open_db
from app.domain import programs as program_rules
from app.repository import meta_repo
from app.repository import change_log_repo
from app.services import cell_edit, month_status
from app.services import data as data_service
from app.services import drive_import
from app.services import auto_export as auto_export_service
from app.services.auto_export import AutoExporter
from app.services.auto_export import cfg_enabled as auto_export_enabled
from app.services import excel as excel_service
from app.services import journal as journal_service
from app.services import report as report_service
from app.services import settings as settings_service
from app.services import programs as program_service
from app.services import schedule as schedule_service

log = logging.getLogger("sonefm.api")

UNDO_LIMIT = 50   # 세션 안에서 되돌릴 수 있는 작업 수

_UNEXPECTED = "처리 중 문제가 생겼습니다. 다시 시도해 주세요. 계속되면 로그 파일을 개발자에게 보내 주세요."


class Api:
    def __init__(self, cfg: AppConfig) -> None:
        # pywebview는 밑줄로 시작하는 속성을 화면에 노출하지 않는다
        self._cfg = cfg
        self._undo: list[dict[str, Any]] = []   # Ctrl+Z용 직전 상태 (이번 실행 동안만)
        self.exporter = AutoExporter(cfg)       # 드라이브 폴더 자동 내보내기 (main.py가 창 닫을 때 부른다)

    def _run(self, name: str, fn: Callable[[sqlite3.Connection], Any]) -> Any:
        """DB 연결 하나 = 트랜잭션 하나. 실패 결과({"ok": False})면 쓰기를 되돌린다."""
        try:
            with open_db(self._cfg.db_path) as conn:
                result = fn(conn)
                if isinstance(result, dict) and result.get("ok") is False:
                    conn.rollback()
                if isinstance(result, dict) and "undo" in result:
                    record = result.pop("undo")
                    if record:
                        self._undo.append(record)
                        del self._undo[:-UNDO_LIMIT]
                if isinstance(result, dict):
                    result["can_undo"] = bool(self._undo)
                return result
        except Exception:
            log.exception("%s 실패", name)
            return {"ok": False, "errors": [_UNEXPECTED]}

    def _write(self, name: str, fn: Callable[[sqlite3.Connection], Any]) -> Any:
        """편성 데이터를 바꾸는 호출. 성공하면 자동 내보내기에 '변경 있음'을 알린다."""
        result = self._run(name, fn)
        if isinstance(result, dict) and result.get("ok"):
            self.exporter.mark_changed()
        return result

    # ── 공통 ──────────────────────────────────────
    def ping(self) -> dict[str, Any]:
        """연결 확인용 왕복 호출."""
        def fn(conn: sqlite3.Connection) -> dict[str, Any]:
            version = meta_repo.schema_version(conn)
            log.info("ping 응답: 스키마 v%d", version)
            return {"ok": True, "message": "정상 연결", "app_version": APP_VERSION,
                    "db_path": self._cfg.db_path, "schema_version": version}
        return self._run("ping", fn)

    # ── 프로그램 ──────────────────────────────────
    def program_choices(self) -> dict[str, Any]:
        return {"ok": True, **program_rules.choices()}

    def list_programs(self, query: str = "", include_ended: bool = False) -> dict[str, Any]:
        return self._run("list_programs", lambda c: {
            "ok": True, "items": program_service.list_programs(c, query or "", bool(include_ended))})

    def get_program(self, program_id: str) -> dict[str, Any]:
        return self._run("get_program", lambda c: program_service.get_program(c, program_id))

    def save_program(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._write("save_program", lambda c: program_service.save_program(c, payload))

    def end_program(self, program_id: str, end_date: str) -> dict[str, Any]:
        return self._write("end_program", lambda c: program_service.end_program(c, program_id, end_date))

    def resume_program(self, program_id: str) -> dict[str, Any]:
        return self._write("resume_program", lambda c: program_service.resume_program(c, program_id))

    def delete_program(self, program_id: str) -> dict[str, Any]:
        return self._write("delete_program", lambda c: program_service.delete_program(c, program_id))

    def create_special(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._write("create_special", lambda c: program_service.create_special(c, payload))

    # ── 편성표 ────────────────────────────────────
    def get_month(self, year: int, month: int) -> dict[str, Any]:
        return self._run("get_month", lambda c: schedule_service.get_month(c, year, month))

    def generate_month(self, year: int, month: int, scope: str,
                       overwrite_manual: bool = False) -> dict[str, Any]:
        """scope: first_week(첫 주 불러오기) | rest(월단위 생성) | all"""
        return self._write("generate_month", lambda c: schedule_service.generate(
            c, year, month, scope, bool(overwrite_manual)))

    # ── 칸 편집 ───────────────────────────────────
    def get_cell(self, date: str, hour: int) -> dict[str, Any]:
        return self._run("get_cell", lambda c: cell_edit.get_cell(c, date, hour))

    def cell_set_kind(self, date: str, hour: int, kind: str, scope: str = "once") -> dict[str, Any]:
        return self._write("cell_set_kind", lambda c: cell_edit.set_kind(c, date, hour, kind, scope))

    def cell_cancel(self, date: str, hour: int, mode: str, reason: str = "") -> dict[str, Any]:
        return self._write("cell_cancel", lambda c: cell_edit.cancel(c, date, hour, mode, reason))

    def cell_replace(self, date: str, hour: int, program_id: str, kind: str, scope: str = "once") -> dict[str, Any]:
        return self._write("cell_replace", lambda c: cell_edit.replace(c, date, hour, program_id, kind, scope))

    def cell_special(self, date: str, hour: int, payload: dict[str, Any]) -> dict[str, Any]:
        return self._write("cell_special", lambda c: cell_edit.put_special(c, date, hour, payload or {}))

    def cell_override(self, date: str, hour: int, episode: Any) -> dict[str, Any]:
        return self._write("cell_override", lambda c: cell_edit.set_override(c, date, hour, episode))

    def cell_actual(self, date: str, hour: int, note: str) -> dict[str, Any]:
        return self._write("cell_actual", lambda c: cell_edit.set_actual(c, date, hour, note))

    def cell_prep(self, date: str, hour: int, status: str) -> dict[str, Any]:
        return self._write("cell_prep", lambda c: cell_edit.set_prep(c, date, hour, status))

    def cell_revert(self, date: str, hour: int) -> dict[str, Any]:
        return self._write("cell_revert", lambda c: cell_edit.revert(c, date, hour))

    def undo(self) -> dict[str, Any]:
        """Ctrl+Z: 가장 최근 작업을 되돌린다 (이번 실행 동안 한 작업만)."""
        if not self._undo:
            return {"ok": False, "errors": ["되돌릴 작업이 없습니다."], "can_undo": False}
        record = self._undo.pop()
        result = self._write("undo", lambda c: cell_edit.undo(c, record))
        if not result.get("ok"):
            self._undo.append(record)
            result["can_undo"] = True
        return result

    def list_change_log(self, limit: int = 200) -> dict[str, Any]:
        return self._run("list_change_log", lambda c: {
            "ok": True, "items": change_log_repo.recent(c, max(1, min(int(limit), 1000)))})

    # ── 월 상태 ───────────────────────────────────
    def month_status(self, year: int, month: int, action: str, reason: str = "") -> dict[str, Any]:
        """action: confirm(확정) | reopen(확정 취소) | close(마감) | unlock(잠금 해제, 사유 필수)"""
        return self._write("month_status", lambda c: month_status.change(c, year, month, action, reason))

    # ── 엑셀 ──────────────────────────────────────
    def _window(self):  # type: ignore[no-untyped-def]
        import webview
        return webview.windows[0] if webview.windows else None

    def excel_pick_file(self) -> dict[str, Any]:
        """가져올 엑셀 파일 고르기 (열기 창)."""
        import webview
        win = self._window()
        if win is None:
            return {"ok": False, "errors": ["파일 선택 창을 열 수 없습니다."]}
        picked = win.create_file_dialog(webview.OPEN_DIALOG, file_types=("엑셀 파일 (*.xlsx;*.xlsm)",))
        return {"ok": True, "path": picked[0] if picked else None}

    def excel_analyze(self, path: str) -> dict[str, Any]:
        return self._run("excel_analyze", lambda c: excel_service.analyze(c, path))

    def excel_import(self, path: str, titles: list[str], mapping: dict[str, Any], overwrite: bool = True) -> dict[str, Any]:
        """가져오기 전에 DB를 백업한다 (되돌리기 대신 백업으로 복원)."""
        try:
            backup = backup_db(self._cfg.db_path, self._cfg.backup_dir, reason="before-import")
        except Exception:
            log.exception("가져오기 전 백업 실패")
            return {"ok": False, "errors": ["가져오기 전 백업을 만들지 못해 중단했습니다. 백업 폴더를 확인해 주세요."]}
        result = self._write("excel_import", lambda c: excel_service.run_import(c, path, titles or [], mapping or {}, bool(overwrite)))
        if result.get("ok") and backup:
            result["backup"] = str(backup)
        return result

    def excel_export(self, year: int, month: int) -> dict[str, Any]:
        """그 달 편성표를 엑셀로 저장 (저장 창). 수식 없이 글자만."""
        import webview
        win = self._window()
        if win is None:
            return {"ok": False, "errors": ["저장 창을 열 수 없습니다."]}
        name = f"SoneFM편성표_{int(year)}년{int(month):02d}월.xlsx"
        docs = Path(self._cfg.db_path).parent.parent
        picked = win.create_file_dialog(webview.SAVE_DIALOG, directory=str(docs), save_filename=name,
                                        file_types=("엑셀 파일 (*.xlsx)",))
        if not picked:
            return {"ok": True, "cancelled": True}
        path = picked if isinstance(picked, str) else picked[0]
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        return self._run("excel_export", lambda c: excel_service.export_months(c, [(int(year), int(month))], path))

    def excel_export_all(self) -> dict[str, Any]:
        """편성이 있는 모든 달을 한 엑셀 파일로 저장 (저장 창). 읽기 전용이라 자동 내보내기 대상 아님."""
        import webview
        win = self._window()
        if win is None:
            return {"ok": False, "errors": ["저장 창을 열 수 없습니다."]}
        listed = self._run("excel_export_all", lambda c: excel_service.export_months_list(c))
        if not listed.get("ok"):
            return listed
        name = f"SoneFM편성표_전체_{listed['span']}.xlsx"
        docs = Path(self._cfg.db_path).parent.parent
        picked = win.create_file_dialog(webview.SAVE_DIALOG, directory=str(docs), save_filename=name,
                                        file_types=("엑셀 파일 (*.xlsx)",))
        if not picked:
            return {"ok": True, "cancelled": True}
        path = picked if isinstance(picked, str) else picked[0]
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        return self._run("excel_export_all", lambda c: excel_service.export_all(c, path))

    def journal_export(self, year: int, month: int) -> dict[str, Any]:
        """그 달 방송일지(하루 1시트)를 엑셀로 저장 (저장 창). 편성표 그대로."""
        import webview
        win = self._window()
        if win is None:
            return {"ok": False, "errors": ["저장 창을 열 수 없습니다."]}
        name = f"방송일지_{int(year)}년_{int(month)}월.xlsx"
        docs = Path(self._cfg.db_path).parent.parent
        picked = win.create_file_dialog(webview.SAVE_DIALOG, directory=str(docs), save_filename=name,
                                        file_types=("엑셀 파일 (*.xlsx)",))
        if not picked:
            return {"ok": True, "cancelled": True}
        path = picked if isinstance(picked, str) else picked[0]
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        return self._run("journal_export", lambda c: journal_service.export_journal(c, int(year), int(month), path))

    def report_export(self, year: int, month: int) -> dict[str, Any]:
        """그 달 방송실시결과 보고서를 hwpx로 저장 (저장 창). 한글이 있으면 같은 이름 hwp도."""
        import webview
        win = self._window()
        if win is None:
            return {"ok": False, "errors": ["저장 창을 열 수 없습니다."]}
        name = f"별지 13 방송실시결과지상파방송사업자방송법 시행에 관한 방송통신위원회규칙_팔달FM_{int(month)}월전체.hwpx"
        docs = Path(self._cfg.db_path).parent.parent
        picked = win.create_file_dialog(webview.SAVE_DIALOG, directory=str(docs), save_filename=name,
                                        file_types=("한글 문서 (*.hwpx)",))
        if not picked:
            return {"ok": True, "cancelled": True}
        path = picked if isinstance(picked, str) else picked[0]
        if not path.lower().endswith(".hwpx"):
            path += ".hwpx"
        return self._run("report_export", lambda c: report_service.export_report(c, int(year), int(month), path))

    # ── 설정: 운영 기준값·임시공휴일 ──────────────
    def settings_get(self) -> dict[str, Any]:
        return self._run("settings_get", settings_service.get_rules)

    def settings_save(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._write("settings_save", lambda c: settings_service.save_rules(c, payload or {}))

    def holiday_add(self, date: str, name: str) -> dict[str, Any]:
        return self._write("holiday_add", lambda c: settings_service.add_holiday(c, date, name))

    def holiday_delete(self, date: str) -> dict[str, Any]:
        return self._write("holiday_delete", lambda c: settings_service.delete_holiday(c, date))

    # ── 데이터 파일: 백업·복원·이동 ───────────────
    def _safe(self, name: str, fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        try:
            return fn()
        except Exception:
            log.exception("%s 실패", name)
            return {"ok": False, "errors": [_UNEXPECTED]}

    def _dialog(self, kind: str, **kw: Any) -> str | None:
        import webview
        win = self._window()
        if win is None:
            return None
        const = {"open": webview.OPEN_DIALOG, "save": webview.SAVE_DIALOG, "folder": webview.FOLDER_DIALOG}[kind]
        picked = win.create_file_dialog(const, **kw)
        if not picked:
            return None
        return picked if isinstance(picked, str) else picked[0]

    def data_info(self) -> dict[str, Any]:
        cfg = self._cfg
        return self._safe("data_info", lambda: {
            "ok": True, "db_path": cfg.db_path, "backup_dir": cfg.backup_dir, "backup_keep": cfg.backup_keep,
            "backups": data_service.list_backups(cfg)})

    def backup_now(self) -> dict[str, Any]:
        return self._safe("backup_now", lambda: data_service.backup_now(self._cfg))

    def backup_restore(self, path: str) -> dict[str, Any]:
        """백업 파일로 되돌리기. 이전 작업의 Ctrl+Z 기록은 지운다."""
        result = self._safe("backup_restore", lambda: data_service.restore(self._cfg, path))
        if result.get("ok"):
            self._undo.clear()
            self.exporter.mark_changed()
        return result

    def data_import_pick(self) -> dict[str, Any]:
        """다른 데이터 파일(내보낸 .db) 고르기."""
        return {"ok": True, "path": self._dialog("open", file_types=("SoneFM 데이터 파일 (*.db)",))}

    def data_export(self) -> dict[str, Any]:
        name = f"SoneFM편성표_데이터_{datetime.now():%Y%m%d}.db"
        picked = self._dialog("save", save_filename=name, file_types=("SoneFM 데이터 파일 (*.db)",))
        if not picked:
            return {"ok": True, "cancelled": True}
        return self._safe("data_export", lambda: data_service.export_data(self._cfg, picked))

    def pick_folder(self) -> dict[str, Any]:
        return {"ok": True, "path": self._dialog("folder")}

    def db_move(self, folder: str, mode: str = "copy") -> dict[str, Any]:
        result = self._safe("db_move", lambda: data_service.change_db_location(self._cfg, folder, mode))
        if result.get("ok"):
            self._undo.clear()
            self.exporter.mark_changed()
        return result

    def backup_options(self, folder: str | None, keep: Any) -> dict[str, Any]:
        return self._safe("backup_options", lambda: data_service.set_backup_options(self._cfg, folder, keep))

    # ── 드라이브 폴더 자동 내보내기 ───────────────
    def auto_export_status(self) -> dict[str, Any]:
        return self._safe("auto_export_status", lambda: {"ok": True, **self.exporter.status()})

    def drive_folder_set(self, folder: str | None) -> dict[str, Any]:
        """드라이브 폴더만 정한다(보내기·가져오기 공용). 자동 내보내기 켜짐 여부는 바꾸지 않되,
        새로 정하는 PC는 꺼진 채로 시작한다(가져오기만 쓰는 PC가 실수로 덮어쓰지 않도록). 비우면 폴더를 지운다."""
        def run() -> dict[str, Any]:
            new = (folder or "").strip()
            if new and not Path(new).is_dir():
                return {"ok": False, "errors": ["폴더를 찾지 못했습니다. 드라이브 폴더를 다시 골라 주세요."]}
            was_on = auto_export_enabled(self._cfg)
            self._cfg.auto_export_dir = new
            self._cfg.auto_export_enabled = True if (new and was_on) else False
            save_config(self._cfg)
            if not new:
                return {"ok": True, "message": "드라이브 폴더를 지웠습니다."}
            return {"ok": True, "message": "드라이브 폴더를 정했습니다."}
        return self._safe("drive_folder_set", run)

    def auto_export_enable(self, on: bool) -> dict[str, Any]:
        """자동 내보내기 켜기·끄기(폴더는 그대로). 켜도 바로 보내지 않는다(이후 변경부터)."""
        def run() -> dict[str, Any]:
            if on and not (self._cfg.auto_export_dir or "").strip():
                return {"ok": False, "errors": ["드라이브 폴더를 먼저 골라 주세요."]}
            self._cfg.auto_export_enabled = bool(on)
            save_config(self._cfg)
            if not on:
                return {"ok": True, "message": "자동 내보내기를 껐습니다."}
            return {"ok": True, "message": "자동 내보내기를 켰습니다. 지금은 보내지 않고, 이후 편성을 바꿀 때부터 보냅니다."}
        return self._safe("auto_export_enable", run)

    def drive_export_preview(self) -> dict[str, Any]:
        """[드라이브로 보내기] 확인 창용: 드라이브·이 PC 편성 요약과 경고 여부."""
        return self._safe("drive_export_preview", lambda: auto_export_service.preview_export(self._cfg))

    def drive_export_run(self, expected: dict[str, Any] | None = None) -> dict[str, Any]:
        """이 PC 편성으로 드라이브 편성을 바꾼다(덮기 전 보관). 이 PC가 비어 있으면 막는다.

        expected: 미리보기가 돌려준 드라이브 파일 정보(name·mtime·size, 파일이 없었으면 exists=False).
        그 사이 드라이브 파일이 바뀌었으면 보내지 않고 changed를 돌려준다.
        """
        return self._safe("drive_export_run", lambda: self.exporter.export_manual(expected))

    def drive_import_preview(self) -> dict[str, Any]:
        """드라이브 폴더에서 가져올 최신 파일 정보(이름·수정 시각·크기, 이 PC보다 오래됐는지)."""
        return self._safe("drive_import_preview", lambda: drive_import.preview(self._cfg))

    def drive_import_run(self, expected: dict[str, Any] | None = None) -> dict[str, Any]:
        """최신 파일로 이 PC의 편성을 바꾼다(검사·백업 후 교체). 자동 내보내기 변경 표시는 하지 않는다.

        expected: 미리보기가 돌려준 name·mtime·size. 그 사이 최신 파일이 바뀌었으면 가져오지 않고 changed를 돌려준다.
        가져오는 동안에는 자동 내보내기가 돌지 않는다.
        """
        def run() -> dict[str, Any]:
            if not self.exporter.hold():
                return {"ok": False, "errors": ["자동 내보내기가 진행 중입니다. 잠시 뒤 다시 눌러 주세요."]}
            try:
                return drive_import.import_latest(self._cfg, expected)
            finally:
                self.exporter.release_hold()
        result = self._safe("drive_import_run", run)
        if result.get("ok"):
            self._undo.clear()
            result["export_on"] = auto_export_enabled(self._cfg)
        return result
