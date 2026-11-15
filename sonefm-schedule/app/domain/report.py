"""월간 방송실시결과 보고서 집계 (순수 함수).

- 달력 날짜 기준 1일~말일. 모든 칸은 60분으로 센다.
- 전체방송 = 일수 × 17 × 60.
- 대중음악 = 음악 프로그램(settings `music_program_ids`) 칸 합계. 청취자참여 = 전체 − 대중음악.
- 다른 방송사업자 제작 = 제작 주체가 '외부'인 프로그램 칸 합계.
- 표 행
  - 대중음악(4면): 프로그램·본방/재방·요일·시각마다 한 행, 프로그램명은 묶음 첫 행에만.
    한 시각에 그 달 매일 나가면 `월~일 (10.1~10.31)` 한 줄.
  - 청취자참여(5면): 프로그램·본방/재방마다 한 행, 요일·시각이 여럿이면 칸 안 줄바꿈.
- 이름은 편성표(DB) 이름 + ` (60분)`, 재방은 ` (재) (60분)`.
"""
from __future__ import annotations

import calendar
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

HOURS_PER_DAY = 17
MIN_PER_CELL = 60
WD = "월화수목금토일"


@dataclass(frozen=True)
class ReportCell:
    day: date
    hour: int
    program_id: str
    kind: str               # 본방 | 재방


@dataclass(frozen=True)
class ProgramInfo:
    name: str
    external: bool          # 제작 주체 '외부'


@dataclass
class ReportRow:
    name: str                       # 빈 문자열이면 위 행과 같은 프로그램 (대중음악 표)
    dates: list[str]                # 칸 안 줄마다 하나
    starts: list[str]
    ends: list[str]
    minutes: int


@dataclass
class Report:
    year: int
    month: int
    total: int
    music: int
    listener: int
    external: int
    external_pct: str
    music_rows: list[ReportRow]
    listener_rows: list[ReportRow]
    listener_rows_sum: int
    warnings: list[str] = field(default_factory=list)


def fmt(n: int) -> str:
    return f"{n:,}"


def _label(name: str, kind: str) -> str:
    return f"{name} (재) (60분)" if kind == "재방" else f"{name} (60분)"


def _dates_text(days: list[date]) -> str:
    """`목 (10/1, 8, 15, 22, 29)` — 첫 날짜만 월을 붙인다."""
    days = sorted(days)
    rest = ", ".join(str(d.day) for d in days[1:])
    return f"{WD[days[0].weekday()]} ({days[0].month}/{days[0].day}{', ' + rest if rest else ''})"


def _hhmm(h: int) -> str:
    return f"{h:02d}:00"


def _slots(cells: list[ReportCell]) -> list[tuple[int, int, list[date]]]:
    """(요일, 시각, 날짜들) — 요일 순, 같은 요일이면 시각 순."""
    by: dict[tuple[int, int], list[date]] = defaultdict(list)
    for c in cells:
        by[(c.day.weekday(), c.hour)].append(c.day)
    return [(wd, h, days) for (wd, h), days in sorted(by.items())]


def _daily_hour(cells: list[ReportCell], ndays: int) -> int | None:
    """모든 칸이 한 시각이고 그 달 매일이면 그 시각."""
    hours = {c.hour for c in cells}
    if len(hours) == 1 and len({c.day for c in cells}) == ndays == len(cells):
        return hours.pop()
    return None


def build_report(year: int, month: int, cells: list[ReportCell], programs: dict[str, ProgramInfo],
                 music_ids: list[str]) -> Report:
    ndays = calendar.monthrange(year, month)[1]
    first, last = date(year, month, 1), date(year, month, ndays)
    cells = [c for c in cells if first <= c.day <= last]
    total = ndays * HOURS_PER_DAY * MIN_PER_CELL
    warnings: list[str] = []

    filled = {(c.day, c.hour) for c in cells}
    blanks = [(d, h) for d in (date(year, month, i) for i in range(1, ndays + 1))
              for h in range(6, 23) if (d, h) not in filled]
    if blanks:
        head = ", ".join(f"{d.month}/{d.day} {h}시" for d, h in blanks[:5])
        warnings.append(f"빈칸 {len(blanks)}개: {head}{' 외' if len(blanks) > 5 else ''}")

    groups: dict[tuple[str, str], list[ReportCell]] = defaultdict(list)
    for c in cells:
        groups[(c.program_id, c.kind)].append(c)

    def name_of(pid: str) -> str:
        return programs[pid].name if pid in programs else "(삭제된 프로그램)"

    music_set = set(music_ids)
    order = {pid: i for i, pid in enumerate(music_ids)}
    kind_order = {"본방": 0, "재방": 1}

    music_rows: list[ReportRow] = []
    for (pid, kind) in sorted((k for k in groups if k[0] in music_set),
                              key=lambda k: (order[k[0]], kind_order.get(k[1], 2))):
        g = groups[(pid, kind)]
        label = _label(name_of(pid), kind)
        h = _daily_hour(g, ndays)
        if h is not None:
            music_rows.append(ReportRow(label, [f"월~일 ({month}.1~{month}.{ndays})"],
                                        [_hhmm(h)], [_hhmm(h + 1)], len(g) * MIN_PER_CELL))
            continue
        for i, (_wd, hour, days) in enumerate(_slots(g)):
            music_rows.append(ReportRow(label if i == 0 else "", [_dates_text(days)],
                                        [_hhmm(hour)], [_hhmm(hour + 1)], len(days) * MIN_PER_CELL))

    listener_rows: list[ReportRow] = []
    for (pid, kind) in sorted((k for k in groups if k[0] not in music_set),
                              key=lambda k: (not name_of(k[0]).startswith("["), name_of(k[0]),
                                             kind_order.get(k[1], 2))):
        slots = _slots(groups[(pid, kind)])
        listener_rows.append(ReportRow(
            _label(name_of(pid), kind),
            [_dates_text(days) for _wd, _h, days in slots],
            [_hhmm(h) for _wd, h, _d in slots], [_hhmm(h + 1) for _wd, h, _d in slots],
            len(groups[(pid, kind)]) * MIN_PER_CELL))

    music = sum(r.minutes for r in music_rows)
    listener = total - music
    rows_sum = sum(r.minutes for r in listener_rows)
    if rows_sum != listener:
        warnings.append(f"청취자참여 표 합계 {fmt(rows_sum)}분 ≠ 전체 − 대중음악 {fmt(listener)}분 (빈칸 때문)")
    external = sum(MIN_PER_CELL for c in cells if c.program_id in programs and programs[c.program_id].external)
    return Report(year, month, total, music, listener, external, f"{external / total * 100:.1f}%",
                  music_rows, listener_rows, rows_sum, warnings)
