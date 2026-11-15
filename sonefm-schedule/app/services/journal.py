"""방송일지 만들기: 편성표(계획) 그대로, 달력 날짜 기준 한 달 = 하루 1시트."""
from __future__ import annotations

import calendar
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

from app.config import resource_dir
from app.domain.journal import journal_days
from app.io_excel.journal_writer import write_journal
from app.repository import schedule_repo
from app.services.excel import _cell_texts


def template_path() -> Path:
    return resource_dir() / "app" / "templates" / "journal_template.xlsx"


def export_journal(conn: sqlite3.Connection, year: int, month: int, path: str) -> dict[str, Any]:
    first = date(year, month, 1)
    last = date(year, month, calendar.monthrange(year, month)[1])
    display = schedule_repo.program_display(conn)
    cells = {k: (text, display.get(pid, {}).get("category"))
             for k, (text, _marked, _band, pid) in _cell_texts(conn, first, last).items()}
    journal = journal_days(year, month, cells)
    try:
        write_journal(journal, template_path(), path)
    except PermissionError:
        return {"ok": False, "errors": ["파일을 저장할 수 없습니다. 같은 이름의 파일이 엑셀에 열려 있으면 닫고 다시 시도해 주세요."]}
    return {"ok": True, "path": str(path), "warnings": journal.warnings,
            "message": f"방송일지를 만들었습니다: {Path(path).name}"}
