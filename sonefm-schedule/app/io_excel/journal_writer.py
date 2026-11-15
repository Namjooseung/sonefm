"""방송일지 엑셀 쓰기: 양식 시트 1장을 날짜 수만큼 복제해 D3·D6:E22를 채운다 (최신 날짜 시트가 앞)."""
from __future__ import annotations

from copy import copy
from pathlib import Path

import openpyxl

from app.domain.journal import Journal, fit_text, header_text, sheet_title

FIRST_ROW, NAME_COL, FIELD_COL = 6, 4, 5


def _wrap(cell) -> None:  # type: ignore[no-untyped-def]
    al = copy(cell.alignment)
    al.wrap_text = True
    cell.alignment = al


def write_journal(journal: Journal, template: str | Path, path: str | Path) -> None:
    wb = openpyxl.load_workbook(template)
    base = wb.worksheets[0]
    for i, day in enumerate(journal.days):
        ws = base if i == 0 else wb.copy_worksheet(base)
        ws.title = sheet_title(day.day)
        ws["D3"] = header_text(day.day)
        for ref in ("B3", "C5", "D3"):           # 원래 줄바꿈이 들어 있는 머리말 칸
            _wrap(ws[ref])
        for k, (text, cat) in enumerate(day.slots):
            c = ws.cell(row=FIRST_ROW + k, column=NAME_COL, value=fit_text(text) if text else None)
            _wrap(c)
            ws.cell(row=FIRST_ROW + k, column=FIELD_COL, value=cat)
    wb._sheets.sort(key=lambda s: s.title, reverse=True)
    wb.active = 0
    wb.save(path)
