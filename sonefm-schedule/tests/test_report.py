"""방송실시결과 보고서: 샘플 편성을 가져와 hwpx를 만들면 정해진 칸만 바뀌고 목록 표 행 수가 맞음."""
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from app.config import migrations_dir
from app.db.connection import connect
from app.db.migrate import apply_migrations
from app.repository import settings_repo
from app.services import excel as excel_svc
from app.services import report as svc

SAMPLE = Path(__file__).resolve().parent.parent / "samples" / "samples.xlsx"
pytestmark = pytest.mark.skipif(not SAMPLE.exists(), reason="samples/samples.xlsx 없음")
HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "s.db")
    apply_migrations(c, migrations_dir())
    a = excel_svc.analyze(c, str(SAMPLE))
    mapping = {n["name"]: {"action": "new", "category": "교양"} for n in a["names"] if n["status"] != "matched"}
    assert excel_svc.run_import(c, str(SAMPLE), ["2609", "2610"], mapping)["ok"]
    ids = {r[1]: r[0] for r in c.execute("SELECT id, name FROM programs")}
    settings_repo.set_value(c, "music_program_ids", settings_repo.get(c, "fixed_program_ids") + [ids["그 사이 음악"], ids["963 음악쌀롱"]])
    c.execute("UPDATE programs SET producer_type = '외부' WHERE name LIKE '[OBS]%'")
    yield c
    c.close()


def _tables(path):
    root = ET.fromstring(zipfile.ZipFile(path).read("Contents/section0.xml"))
    out = []
    for tbl in root.iter(HP + "tbl"):
        rows = [[" / ".join("".join(t.text or "" for t in p.iter(HP + "t")) for p in tc.find(HP + "subList").findall(HP + "p")).strip()
                 for tc in tr.findall(HP + "tc")] for tr in tbl.findall(HP + "tr")]
        out.append((tbl, rows))
    return out


def test_10월_보고서(conn, tmp_path):
    out = tmp_path / "보고서.hwpx"
    r = svc.export_report(conn, 2026, 10, str(out), with_hwp=False)
    assert r["ok"], r
    s = r["summary"]
    assert s["total"] == "31,620"
    music, listener = int(s["music"].replace(",", "")), int(s["listener"].replace(",", ""))
    assert music + listener == 31620 and music > 3720          # 굿모닝·굿바이만 3,720
    t = _tables(out)
    assert t[0][1][0][0].startswith("( 10 )월")
    assert t[2][1][1][1:3] == ["31,620", "31,620"]
    assert t[7][1][1][1] == s["music"] and t[9][1][2][5] == s["listener"]
    assert t[8][1][1][4] == s["external"] and t[8][1][2][4] == s["external_pct"] != "0.0%"
    # 목록 표: 행 수 속성 = 실제 행, 합계 행이 맨 아래
    for idx in (16, 21):            # 합계 = 표 행의 합 (빈칸이 있으면 청취자참여 표 합계 ≠ 전체 − 대중음악, 경고로 알림)
        tbl, rows = t[idx]
        assert int(tbl.get("rowCnt")) == len(rows)
        assert rows[-1][4] == f"{sum(int(x[4].replace(',', '')) for x in rows[1:-1] if x[4]):,}"
        assert [int(tc.find(HP + "cellAddr").get("rowAddr")) for tr in tbl.findall(HP + "tr") for tc in tr.findall(HP + "tc")][-1] == len(rows) - 1
    assert t[16][1][-1][4] == s["music"] and t[16][1][1][1] == "월~일 (10.1~10.31)"
    if t[21][1][-1][4] != s["listener"]:
        assert any("청취자참여 표 합계" in w for w in r["warnings"])
    # 원본 파일 목록·mimetype 순서 유지
    with zipfile.ZipFile(out) as z, zipfile.ZipFile(svc.template_path()) as tz:
        assert z.namelist() == tz.namelist() and z.infolist()[0].compress_type == zipfile.ZIP_STORED


def test_파일이_열려_있으면_안내(conn, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise PermissionError
    monkeypatch.setattr(svc, "write_report", boom)
    r = svc.export_report(conn, 2026, 10, str(tmp_path / "x.hwpx"), with_hwp=False)
    assert not r["ok"] and "열려 있으면" in r["errors"][0]
