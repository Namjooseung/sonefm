"""방송실시결과 보고서 만들기: 편성표 DB → 지난달 보고서 양식(hwpx). 한글이 있으면 hwp도."""
from __future__ import annotations

import calendar
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

from app.config import resource_dir
from app.domain.report import ProgramInfo, ReportCell, build_report, fmt
from app.io_hwpx.hangul import save_as_hwp
from app.io_hwpx.report_writer import TemplateError, write_report
from app.repository import schedule_repo, settings_repo
from app.services.schedule import rerun_mark_map


def template_path() -> Path:
    return resource_dir() / "app" / "templates" / "report_template.hwpx"


def export_report(conn: sqlite3.Connection, year: int, month: int, path: str, with_hwp: bool = True) -> dict[str, Any]:
    first = date(year, month, 1)
    last = date(year, month, calendar.monthrange(year, month)[1])
    # 본방/재방은 편성표에 보이는 표시를 따른다: 본방 자리에 나가는 재방은 (재) 없이 본방 줄로 (엑셀·방송일지와 같음)
    marks = rerun_mark_map(conn, first, last)
    cells = []
    for r in schedule_repo.cell_rows_between(conn, first, last):
        d = date.fromisoformat(r["date"])
        marked = r["kind"] == "재방" and marks.get((d, r["hour"]), True)
        cells.append(ReportCell(d, r["hour"], r["program_id"], "재방" if marked else "본방"))
    programs = {pid: ProgramInfo(p["name"], p["producer_type"] == "외부")
                for pid, p in schedule_repo.program_display(conn).items()}
    music_ids = [pid for pid in settings_repo.get(conn, "music_program_ids", []) if pid in programs]
    report = build_report(year, month, cells, programs, music_ids)
    if not music_ids:
        report.warnings.insert(0, "대중음악 프로그램이 정해지지 않았습니다. 설정 화면에서 골라 주세요.")
    try:
        write_report(report, template_path(), path)
    except PermissionError:
        return {"ok": False, "errors": ["파일을 저장할 수 없습니다. 같은 이름의 파일이 한글에 열려 있으면 닫고 다시 시도해 주세요."]}
    except TemplateError as e:
        return {"ok": False, "errors": [f"보고서 양식을 읽지 못했습니다: {e}"]}
    hwp = None
    if with_hwp:
        hwp_path = str(Path(path).with_suffix(".hwp"))
        hwp = hwp_path if save_as_hwp(path, hwp_path) else None
    return {"ok": True, "path": str(path), "hwp_path": hwp, "warnings": report.warnings,
            "summary": {"total": fmt(report.total), "music": fmt(report.music), "listener": fmt(report.listener),
                        "external": fmt(report.external), "external_pct": report.external_pct},
            "message": f"보고서를 만들었습니다: {Path(path).name}" + (" (hwp도 저장)" if hwp else "")}
