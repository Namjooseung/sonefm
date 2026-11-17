"""엑셀 내보내기 서식: 기존 엑셀 편성표(v2)와 같은 글꼴·열 너비·행 높이, 청록·붉은 글자."""
import openpyxl
import pytest

from app.config import migrations_dir
from app.db.connection import connect
from app.db.migrate import apply_migrations
from app.io_excel.reader import read_workbook
from app.services import excel as svc
from app.services import programs as program_svc
from app.services import schedule as schedule_svc


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "s.db")
    apply_migrations(c, migrations_dir())
    yield c
    c.close()


def _slot(weekday, hour, role="본방"):
    return {"weekday": weekday, "hour": hour, "role": role, "valid_from": "2026-01-05"}


def _prog(conn, name, category, slots, **extra):
    r = program_svc.save_program(conn, {"name": name, "category": category, "cycle_type": "weekly",
                                        "start_episode": 10, "slots": slots, **extra})
    assert r["ok"], r
    return r["program"]["id"]


@pytest.fixture
def sheet(conn, tmp_path):
    _prog(conn, "음악쌀롱", "오락", [_slot(1, 10), _slot(3, 14, "재방")])        # 화 10시 본방, 목 14시 재방
    _prog(conn, "학교이야기", "교양", [_slot(1, 11), _slot(3, 15, "재방")])
    _prog(conn, "특집쇼", "교양", [_slot(2, 12)])
    conn.execute("UPDATE programs SET is_special = 1 WHERE name = '특집쇼'")
    _prog(conn, "회차없음", "교양", [_slot(2, 13)], show_episode=False)
    assert schedule_svc.generate(conn, 2026, 11, "all")["ok"]
    out = tmp_path / "o.xlsx"
    assert svc.export_months(conn, [(2026, 11)], str(out))["ok"]
    return openpyxl.load_workbook(out)["2611"]


def _find(ws, text):
    return [c for row in ws.iter_rows(min_col=2, max_col=8) for c in row if c.value and text in str(c.value)]


def test_오락은_본방_재방_모두_청록(sheet):
    cells = _find(sheet, "음악쌀롱")
    assert len(cells) >= 8
    assert {c.fill.fgColor.rgb for c in cells} == {"FFA2C4C9"}


def test_교양_재방은_연노랑_본방은_채우기_없음(sheet):
    cells = _find(sheet, "학교이야기")
    re_cells = [c for c in cells if "(재)" in c.value]
    new_cells = [c for c in cells if "(재)" not in c.value]
    assert re_cells and new_cells
    assert {c.fill.fgColor.rgb for c in re_cells} == {"FFFFF2CC"}
    assert all(c.fill.fill_type is None for c in new_cells)


def test_붉은_글자는_회차_오르는_본방만(sheet):
    def red(c):
        return c.font.color is not None and c.font.color.rgb == "FFFF0000"
    for c in _find(sheet, "학교이야기"):
        assert red(c) == ("(재)" not in c.value)
        assert c.font.b
    assert _find(sheet, "특집쇼") and _find(sheet, "회차없음")
    assert not any(red(c) for c in _find(sheet, "특집쇼"))
    assert not any(red(c) for c in _find(sheet, "회차없음"))
    # 오락 본방은 청록이면서 붉은 글자
    assert any(red(c) for c in _find(sheet, "음악쌀롱"))


def test_띠는_칠하지_않음(sheet):
    assert "B21:H21" in {str(m) for m in sheet.merged_cells.ranges}      # 둘째 주 06시
    assert sheet["B21"].fill.fill_type is None


def test_열_너비_행_높이_글꼴은_기존_엑셀과_같음(sheet):
    widths = {k: v.width for k, v in sheet.column_dimensions.items()}
    assert {k: widths[k] for k in "ABCDEFGH"} == {"A": 16.38, "B": 35, "C": 35.5, "D": 33.13, "E": 37,
                                                   "F": 36.88, "G": 33.63, "H": 38.88}
    h = lambda r: sheet.row_dimensions[r].height
    assert (h(1), h(2), h(20), h(21), h(22), h(36), h(37)) == (64.5, 35.25, 35.25, 34.5, 41.25, 41.25, 36.0)   # 둘째 주: 머리글 20, 06시 21, 22시 37
    f = lambda c: (c.font.name, c.font.sz, c.font.b)
    assert f(sheet["B1"]) == ("Arial", 32, False)
    assert f(sheet["A2"]) == ("Calibri", 19, True) and sheet["A2"].fill.fgColor.rgb == "FF9FC5E8"
    assert f(sheet["A3"]) == ("Malgun Gothic", 12, True)
    assert f(_find(sheet, "학교이야기")[0]) == ("Gulim", 12, True)


def test_새_서식으로_내보낸_파일을_다시_가져올_수_있음(conn, sheet, tmp_path):
    out = tmp_path / "again.xlsx"
    assert svc.export_months(conn, [(2026, 11)], str(out))["ok"]
    sh = read_workbook(out)[0]
    texts = [v for w in sh.weeks for v, _ in w.cells.values() if v]
    assert any(str(v).startswith("음악쌀롱") for v in texts) and any(str(v).startswith("학교이야기") for v in texts)
    assert svc.analyze(conn, str(out))["ok"]
    assert svc.compare(conn, str(out), ["2611"])["count"] == 0
