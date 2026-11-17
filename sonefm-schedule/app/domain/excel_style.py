"""엑셀 내보내기 칸 색 규칙 (순수 함수).

- 청록 배경: 프로그램 분야가 '오락'인 칸 (본방·재방 모두)
- 연노랑 배경: 오락이 아닌 재방 (본방 자리에 나가는 재방처럼 (재)를 안 붙인 칸은 제외)
- 붉은 글자: 회차가 올라간 새 본방 — 화면(WeekRow.vue isNewEpisode)과 같은 조건
"""
from __future__ import annotations

FILL_NONE, FILL_RERUN, FILL_ENTERTAINMENT = None, "rerun", "entertainment"


def cell_fill(category: str | None, rerun_marked: bool) -> str | None:
    if category == "오락":
        return FILL_ENTERTAINMENT
    return FILL_RERUN if rerun_marked else FILL_NONE


def is_red_text(kind: str, show_episode: bool, is_special: bool, is_fixed: bool) -> bool:
    return kind == "본방" and show_episode is not False and not is_special and not is_fixed
