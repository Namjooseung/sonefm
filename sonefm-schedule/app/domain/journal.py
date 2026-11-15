"""방송일지 (순수 함수).

- 달력 날짜 기준: 그 달 1일~말일 하루마다 06~22시 17칸 (프로그램명, 방송분야).
  주의 소속과 상관없이 11월 일지에는 10월 소속 주에 있는 11/1도 들어간다.
- 프로그램명 글자는 엑셀 편성표 내보내기와 같은 형식(`이름_23화(재)`)을 받아 쓴다.
- 칸 넘침: 기존 `방송일지_생성.py`의 줄바꿈 규칙을 그대로 옮겼다.
  D열 폭 ≈ 371px, 맑은고딕 12pt 한글 1자 ≈ 16px. 330px를 넘으면 두 줄로 나눈다.
"""
from __future__ import annotations

import calendar
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

HOURS = range(6, 23)
WEEKDAYS = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
D_LIMIT = 330


@dataclass
class DayLog:
    day: date
    slots: list[tuple[str, str | None]]          # 17칸 (프로그램명, 방송분야)


@dataclass
class Journal:
    days: list[DayLog]
    warnings: list[str] = field(default_factory=list)


def journal_days(year: int, month: int,
                 cells: dict[tuple[date, int], tuple[str, str | None]]) -> Journal:
    """cells: (날짜, 시) → (프로그램명 글자, 방송분야). 빈칸·분야 없음은 경고로 모은다."""
    days, warnings = [], []
    for d in range(1, calendar.monthrange(year, month)[1] + 1):
        day = date(year, month, d)
        slots = []
        for h in HOURS:
            text, cat = cells.get((day, h), ("", None))
            if not text:
                warnings.append(f"{month}/{d} {h}시 빈칸")
            elif not cat:
                warnings.append(f"{month}/{d} {h}시 {text}: 방송분야 없음")
            slots.append((text, cat))
        days.append(DayLog(day, slots))
    return Journal(days, warnings)


def header_text(day: date) -> str:
    """D3 칸 (기존 양식 글자 그대로, `_x000D_` 포함)."""
    return (f"{day.year}년 {day.month} 월 {day.day} 일 {WEEKDAYS[day.weekday()]}_x000D_\n"
            f"전체방송시간 1,020 분")


def sheet_title(day: date) -> str:
    return f"{day.year % 100:02d}{day.month:02d}{day.day:02d}"


def text_px(s: str) -> float:
    w = 0.0
    for ch in s:
        if unicodedata.east_asian_width(ch) in "WF":
            w += 16
        elif ch == " ":
            w += 4.5
        elif ch in "[]()<>_.,~!;:|":
            w += 6
        elif ch.isupper():
            w += 10.5
        else:
            w += 9.2
    return w


def _split2(s: str) -> str:
    """두 줄 중 긴 쪽이 가장 짧아지는 곳에서 끊는다. 공백 자리 또는 `_N화` 앞. `]`·`)` 뒤를 우선."""
    best: tuple[float, str] | None = None
    for i, ch in enumerate(s):
        if ch == " ":
            a, b = s[:i], s[i + 1:]
        elif ch == "_" and re.match(r"_[0-9]+화", s[i:]):
            a, b = s[:i], s[i:]
        else:
            continue
        score = max(text_px(a), text_px(b))
        if i and s[i - 1] in "])" and score <= D_LIMIT:
            score -= 1000
        if best is None or score < best[0]:
            best = (score, a + "\n" + b)
    return best[1] if best else s


def fit_text(s: str) -> str:
    s = " ".join(s.split())
    return _split2(s) if text_px(s) > D_LIMIT else s
