"""방송일지: 샘플 편성을 가져와 한 달 일지를 만들면 하루 1시트, 칸 글자는 엑셀 내보내기와 같음."""
from datetime import date
from pathlib import Path

import openpyxl
import pytest

from app.config import migrations_dir
from app.db.connection import connect
from app.db.migrate import apply_migrations
from app.services import excel as excel_svc
from app.services import journal as svc

SAMPLE = Path(__file__).resolve().parent.parent / "samples" / "samples.xlsx"
pytestmark = pytest.mark.skipif(not SAMPLE.exists(), reason="samples/samples.xlsx 없음")


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "s.db")
    apply_migrations(c, migrations_dir())
    a = excel_svc.analyze(c, str(SAMPLE))
    mapping = {n["name"]: {"action": "new", "category": "교양"} for n in a["names"] if n["status"] != "matched"}
    assert excel_svc.run_import(c, str(SAMPLE), ["2610", "2611"], mapping)["ok"]
    yield c
    c.close()


def test_한달_일지(conn, tmp_path):
    out = tmp_path / "일지.xlsx"
    r = svc.export_journal(conn, 2026, 11, str(out))
    assert r["ok"], r
    wb = openpyxl.load_workbook(out)
    assert wb.sheetnames[0] == "261130" and wb.sheetnames[-1] == "261101" and len(wb.sheetnames) == 30
    texts = excel_svc._cell_texts(conn, date(2026, 11, 1), date(2026, 11, 1))
    ws = wb["261101"]                       # 10월 소속 주의 날짜도 들어감
    assert ws["D3"].value.startswith("2026년 11 월 1 일 일요일")
    for k, h in enumerate(range(6, 23)):
        assert " ".join(ws.cell(6 + k, 4).value.split()) == texts[(date(2026, 11, 1), h)][0]
        assert ws.cell(6 + k, 5).value in ("교양", "오락")
    assert ws["A5"].value == "시작시간"     # 양식 머리말 유지


def test_파일이_열려_있으면_안내(conn, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise PermissionError
    monkeypatch.setattr(svc, "write_journal", boom)
    r = svc.export_journal(conn, 2026, 11, str(tmp_path / "x.xlsx"))
    assert not r["ok"] and "열려 있으면" in r["errors"][0]
