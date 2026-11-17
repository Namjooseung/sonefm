"""엑셀 전체 내보내기: 편성이 있는 달 목록을 최신 달이 앞(왼쪽)으로 정한다. 순수 함수."""
from __future__ import annotations

from datetime import date

from app.domain.weeks import month_of_week


def months_newest_first(dates: list[date]) -> list[tuple[int, int]]:
    """칸 날짜들 → 소속 달 (연, 월) 목록, 최신 달 먼저. 주의 소속은 그 주 월요일이 속한 달이다."""
    return sorted({month_of_week(d) for d in dates}, reverse=True)


def span_label(months: list[tuple[int, int]]) -> str:
    """파일 이름용 기간 글자. 예: [(2026, 11), (2025, 12)] → '2512-2611'."""
    if not months:
        return ""
    first, last = min(months), max(months)
    return f"{first[0] % 100:02d}{first[1]:02d}-{last[0] % 100:02d}{last[1]:02d}"
