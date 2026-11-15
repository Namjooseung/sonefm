from datetime import date, timedelta

from app.domain.report import ProgramInfo, ReportCell, build_report

PROGS = {
    "gm": ProgramInfo("굿모닝!", False), "mu": ProgramInfo("그 사이 음악", False),
    "obs": ProgramInfo("[OBS] 오늘의 기후", True), "su": ProgramInfo("수원은 지금", False),
    "fill": ProgramInfo("채움", False),
}
MUSIC = ["gm", "mu"]


def _month(year=2026, month=10):
    """매일 06시 굿모닝, 목 13시 그 사이 음악 본방, 화 17시 재방, 월 20시 OBS,
    화 10시 수원은 지금 + 금·일 08시 재방, 나머지 칸은 '채움'."""
    cells = {}
    d = date(year, month, 1)
    while d.month == month:
        for h in range(6, 23):
            cells[(d, h)] = ("fill", "본방")
        cells[(d, 6)] = ("gm", "본방")
        wd = d.weekday()
        if wd == 3: cells[(d, 13)] = ("mu", "본방")
        if wd == 1: cells[(d, 17)] = ("mu", "재방"); cells[(d, 10)] = ("su", "본방")
        if wd == 0: cells[(d, 20)] = ("obs", "본방")
        if wd in (4, 6): cells[(d, 8)] = ("su", "재방")
        d += timedelta(days=1)
    return [ReportCell(day, h, pid, kind) for (day, h), (pid, kind) in cells.items()]


def test_합계와_검산():
    r = build_report(2026, 10, _month(), PROGS, MUSIC)
    assert r.total == 31 * 17 * 60 == 31620
    assert r.music == (31 + 5 + 4) * 60            # 굿모닝 매일 + 목 5회 + 화 4회
    assert r.listener == r.total - r.music == r.listener_rows_sum
    assert r.external == 4 * 60 and r.external_pct == "0.8%"
    assert r.warnings == []


def test_대중음악_행():
    rows = build_report(2026, 10, _month(), PROGS, MUSIC).music_rows
    gm, mu, mu_re = rows
    assert (gm.name, gm.dates, gm.starts, gm.ends, gm.minutes) == (
        "굿모닝! (60분)", ["월~일 (10.1~10.31)"], ["06:00"], ["07:00"], 1860)
    assert (mu.name, mu.dates, mu.minutes) == ("그 사이 음악 (60분)", ["목 (10/1, 8, 15, 22, 29)"], 300)
    assert (mu_re.name, mu_re.dates, mu_re.starts) == ("그 사이 음악 (재) (60분)", ["화 (10/6, 13, 20, 27)"], ["17:00"])


def test_대중음악_여러_요일은_첫_행에만_이름():
    cells = [ReportCell(date(2026, 10, 6), 13, "mu", "본방"), ReportCell(date(2026, 10, 7), 18, "mu", "본방"),
             ReportCell(date(2026, 10, 14), 18, "mu", "본방")]
    rows = build_report(2026, 10, cells, PROGS, MUSIC).music_rows
    assert [(r.name, r.dates[0], r.starts[0], r.minutes) for r in rows] == [
        ("그 사이 음악 (60분)", "화 (10/6)", "13:00", 60), ("", "수 (10/7, 14)", "18:00", 120)]


def test_청취자참여_행은_대괄호_먼저_재방은_줄바꿈():
    rows = build_report(2026, 10, _month(), PROGS, MUSIC).listener_rows
    assert [r.name for r in rows] == ["[OBS] 오늘의 기후 (60분)", "수원은 지금 (60분)", "수원은 지금 (재) (60분)", "채움 (60분)"]
    re = rows[2]
    assert re.dates == ["금 (10/2, 9, 16, 23, 30)", "일 (10/4, 11, 18, 25)"]
    assert re.starts == ["08:00", "08:00"] and re.ends == ["09:00", "09:00"] and re.minutes == 540


def test_달력_날짜_기준과_빈칸_경고():
    cells = [c for c in _month(2026, 11) if not (c.day == date(2026, 11, 3) and c.hour == 15)]
    cells.append(ReportCell(date(2026, 10, 31), 6, "gm", "본방"))   # 다른 달 칸은 빠짐
    r = build_report(2026, 11, cells, PROGS, MUSIC)
    assert r.total == 30 * 17 * 60
    assert r.warnings[0] == "빈칸 1개: 11/3 15시"
    assert "청취자참여 표 합계" in r.warnings[1]
