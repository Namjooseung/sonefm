"""① 읽기: 날짜별 탭 업무일지(PLATFORM_DESIGN 2절 '실제 업무일지 형식') → 칸[날짜·부서·내용·비고].

탭 하나 = 하루, 탭 이름 = YYMMDD. 머리글 줄에서 '부서' 바로 오른쪽이 '내용'인 칸을 찾아 열 위치를 정한다.
이 모양이 아닌 시트는 hub/worklog.py가 읽는다(looks_like_tab_worklog로 먼저 판정).
"""
from __future__ import annotations

import io
import re
import statistics
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from hub.worklog import task_key

TAB_NAME = re.compile(r"^\s*(\d{6})\s*[.。]?\s*(?:[~\-]\s*(\d{2,6}))?\s*[.。]?\s*(.*)$")
COPY_WORDS = re.compile(r"(의\s*사본|사본|copy)", re.I)
HEADER_SCAN_ROWS = 12
DEPT_ALIASES = {"편성제작": "편성제작부"}


def _ymd(s: str) -> date | None:
    try:
        return date(2000 + int(s[:2]), int(s[2:4]), int(s[4:6]))
    except ValueError:
        return None


def parse_tab_name(name: str) -> dict[str, Any] | None:
    """탭 이름 → {start, end, copy}. 날짜 탭이 아니면 None. 연도 오타 보정은 fix_years가 한다."""
    m = TAB_NAME.match(name or "")
    if not m:
        return None
    start = _ymd(m.group(1))
    if start is None:
        return None
    end = start
    if m.group(2):
        e = m.group(2)
        try:
            if len(e) == 2:
                end = date(start.year, start.month, int(e))
            elif len(e) == 4:
                end = date(start.year, int(e[:2]), int(e[2:]))
            else:
                end = _ymd(e) or start
        except ValueError:
            end = start
        if end < start:
            end = start
    return {"start": start, "end": end, "copy": bool(COPY_WORDS.search(m.group(3) or ""))}


def looks_like_tab_worklog(sheetnames: Iterable[str]) -> bool:
    names = list(sheetnames)
    ok = sum(1 for n in names if parse_tab_name(n))
    return len(names) >= 1 and ok >= max(1, len(names) // 2)


def _with_year(d: date, year: int) -> date | None:
    try:
        return d.replace(year=year)
    except ValueError:
        return None


def fix_years(parsed: list[dict[str, Any] | None]) -> None:
    """탭 순서와 맞지 않는 연도 오타(예: 24… 인데 앞뒤가 25…)를 앞뒤 탭에 맞춘다. 제자리 수정."""
    n = len(parsed)

    def shift(p: dict[str, Any], year: int) -> None:
        s = _with_year(p["start"], year)
        if s is None:
            return
        span = (p["end"] - p["start"]).days
        p["start"], p["end"] = s, date.fromordinal(s.toordinal() + span)

    for i, p in enumerate(parsed):
        if not p:
            continue
        prev = next((parsed[j] for j in range(i - 1, -1, -1) if parsed[j]), None)
        nxt = next((parsed[j] for j in range(i + 1, n) if parsed[j]), None)
        if prev and nxt and prev["start"].year == nxt["start"].year != p["start"].year:
            shift(p, prev["start"].year)
    ords = [p["start"].toordinal() for p in parsed if p]
    if len(ords) >= 3:
        med = date.fromordinal(int(statistics.median(ords)))
        for p in parsed:
            if p and abs((p["start"] - med).days) > 400:
                shift(p, med.year)


def _text(v: Any) -> str:
    if v is None or isinstance(v, bool):
        return ""
    return str(v).strip()


def _find_header(rows: list[tuple]) -> tuple[int, int, int | None, list[str]] | None:
    """→ (머리글 줄 번호, 부서 열, 비고 열, 근무자 이름들)."""
    for ri, row in enumerate(rows[:HEADER_SCAN_ROWS]):
        for ci in range(len(row) - 1):
            if _text(row[ci]) == "부서" and _text(row[ci + 1]) == "내용":
                note = next((cj for cj in range(ci + 2, len(row)) if _text(row[cj]) == "비고"), None)
                workers = [_text(c) for c in row[:ci] if _text(c)]
                return ri, ci, note, workers
    return None


def cells_from_rows(rows: list[tuple], start: date, end: date, sheet: str) -> tuple[list[dict[str, Any]], list[str]]:
    """한 탭의 줄들 → (칸 목록, 근무자 이름). 머리글을 못 찾으면 빈 목록."""
    head = _find_header(rows)
    if not head:
        return [], []
    hi, dc, nc, workers = head
    cells: list[dict[str, Any]] = []
    dept = ""
    for row in rows[hi + 1:]:
        if dc < len(row) and _text(row[dc]):
            dept = DEPT_ALIASES.get(_text(row[dc]), _text(row[dc]))
        body = _text(row[dc + 1]) if dc + 1 < len(row) else ""
        if not body or body.upper() in ("TRUE", "FALSE"):
            continue
        note = _text(row[nc]) if nc is not None and nc < len(row) else ""
        cells.append({"date": start.isoformat(), "date_end": end.isoformat(), "dept": dept, "text": body,
                      "note": note, "sheet": sheet, "key": task_key(start, body)})
    return cells, workers


def read_tab_worklog(source: Path | str | bytes, only_dates: set[str] | None = None) -> dict[str, Any]:
    """xlsx(경로 또는 바이트) → {"cells": [...], "workers": [...], "sheets": 읽은 탭 수, "skipped": [탭 이름]}.

    only_dates(YYYY-MM-DD)를 주면 그 날짜 탭만 읽는다(약 2년치 367탭·37MB라서 필요한 탭만).
    """
    import openpyxl
    src = io.BytesIO(source) if isinstance(source, bytes) else source
    wb = openpyxl.load_workbook(src, read_only=True, data_only=True)
    try:
        names = list(wb.sheetnames)
        parsed = [parse_tab_name(n) for n in names]
        fix_years(parsed)
        cells: list[dict[str, Any]] = []
        workers: list[str] = []
        seen: dict[str, list[dict[str, Any]]] = {}
        skipped: list[str] = []
        read = 0
        order = sorted(range(len(names)), key=lambda i: (parsed[i]["copy"] if parsed[i] else False, i))
        for i in order:   # 원본을 먼저 읽어 사본과 견준다
            p = parsed[i]
            if not p:
                skipped.append(names[i])
                continue
            if only_dates is not None and p["start"].isoformat() not in only_dates:
                continue
            rows = [tuple(r) for r in wb[names[i]].iter_rows(values_only=True)]
            tab_cells, w = cells_from_rows(rows, p["start"], p["end"], names[i])
            read += 1
            same = seen.get(p["start"].isoformat())
            if p["copy"] and same is not None and [c["text"] for c in same] == [c["text"] for c in tab_cells]:
                continue    # 원본과 같은 사본은 버린다
            seen.setdefault(p["start"].isoformat(), tab_cells)
            cells.extend(tab_cells)
            workers.extend(x for x in w if x not in workers)
        cells.sort(key=lambda c: c["date"])
        return {"cells": cells, "workers": workers, "sheets": read, "skipped": skipped}
    finally:
        wb.close()
