"""엑셀 전체 내보내기: 편성이 있는 모든 달, 달마다 시트, 최신 달이 왼쪽."""
from datetime import date

import openpyxl
import pytest

from app.config import migrations_dir
from app.db.connection import connect
from app.db.migrate import apply_migrations
from app.domain.export_order import months_newest_first, span_label
from app.services import excel as svc
from app.services import programs as program_svc
from app.services import schedule as schedule_svc


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "s.db")
    apply_migrations(c, migrations_dir())
    yield c
    c.close()


def test_달_목록은_최신이_먼저이고_해를_넘겨도_맞다():
    dates = [date(2025, 12, 3), date(2026, 1, 7), date(2026, 1, 8), date(2026, 11, 3)]
    assert months_newest_first(dates) == [(2026, 11), (2026, 1), (2025, 12)]


def test_주의_소속은_월요일이_속한_달():
    # 2026-11-01(일)은 10/26 월요일 주 → 10월 소속
    assert months_newest_first([date(2026, 11, 1)]) == [(2026, 10)]


def test_기간_글자():
    assert span_label([(2026, 11), (2025, 12), (2026, 1)]) == "2512-2611"
    assert span_label([]) == ""


def test_편성이_없으면_할_일을_안내하는_오류(conn, tmp_path):
    out = tmp_path / "all.xlsx"
    r = svc.export_all(conn, str(out))
    assert r["ok"] is False
    assert "편성" in r["errors"][0] and "만들" in r["errors"][0]
    assert not out.exists()


def test_여러_달을_한_파일에_최신_달이_왼쪽(conn, tmp_path):
    r = program_svc.save_program(conn, {
        "name": "음악쌀롱", "category": "오락", "cycle_type": "weekly", "start_episode": 10,
        "slots": [{"weekday": 1, "hour": 10, "role": "본방", "valid_from": "2025-01-06"}]})
    assert r["ok"], r
    for y, m in [(2025, 12), (2026, 1), (2026, 2)]:
        assert schedule_svc.generate(conn, y, m, "all")["ok"]
    out = tmp_path / "all.xlsx"
    res = svc.export_all(conn, str(out))
    assert res["ok"], res
    assert "3개월" in res["message"]

    wb = openpyxl.load_workbook(out)
    assert wb.sheetnames == ["2602", "2601", "2512"]
    assert wb.active.title == "2602"
    assert wb["2512"]["B1"].value.date() == date(2025, 12, 1)
