"""업무일지: 세 가지 시트 모양, 날짜 읽기, .gsheet 폴더 따라가기, 이상 경고. 내려받기는 가짜로."""
import io
import json
import os
import time
import urllib.request
from datetime import date, datetime

import openpyxl
import pytest

from hub import worklog
from hub.worklog import WorklogError, parse_date, parse_rows

TODAY = date(2026, 11, 16)


def xlsx(*sheets) -> bytes:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for i, rows in enumerate(sheets):
        ws = wb.create_sheet(f"탭{i}")
        for r in rows:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.mark.parametrize("value, expected", [
    (datetime(2026, 11, 16, 9, 0), date(2026, 11, 16)),
    ("2026-11-16", date(2026, 11, 16)), ("2026.11.16", date(2026, 11, 16)), ("2026-11-16 (월)", date(2026, 11, 16)),
    ("11/16", date(2026, 11, 16)), ("11월 16일", date(2026, 11, 16)), ("2026년 11월 16일", date(2026, 11, 16)),
    ("14시", None), ("유튜브", None), (11.16, None), ("13/45", None),
])
def test_날짜_읽기(value, expected):
    assert parse_date(value, TODAY) == expected


def test_연도_없는_날짜는_반년_넘게_앞이면_작년():
    assert parse_date("12/30", date(2027, 1, 5)) == date(2026, 12, 30)
    assert parse_date("12/30", TODAY) == date(2026, 12, 30)


def test_가_날짜열_업무열():
    tasks = parse_rows([["날짜", "업무", "담당"], [datetime(2026, 11, 16), "유튜브 화 14시", "실무자"],
                        ["11/15", "방송일지 정리", None]], TODAY)
    assert [(t.date, t.text) for t in tasks] == [("2026-11-16", "유튜브 화 14시 · 실무자"),
                                                 ("2026-11-15", "방송일지 정리")]


def test_나_날짜줄_아래_업무줄():
    tasks = parse_rows([["11월 15일"], ["협찬사 전화"], [], ["11월 16일 (월)"], ["유튜브 화 14시"], ["  "], ["방송일지"]], TODAY)
    assert [(t.date, t.text) for t in tasks] == [("2026-11-15", "협찬사 전화"), ("2026-11-16", "유튜브 화 14시"),
                                                 ("2026-11-16", "방송일지")]


def test_다_날짜_없는_업무():
    tasks = parse_rows([["업무"], ["업무1"], ["업무2"]], TODAY)
    assert [(t.date, t.text) for t in tasks] == [(None, "업무1"), (None, "업무2")]
    assert tasks[0].key != tasks[1].key and tasks[0].key == parse_rows([["업무1"]], TODAY)[0].key


def test_폴더의_최신_gsheet를_따라가고_모든_탭을_읽음(tmp_path):
    folder = tmp_path / "업무일지"
    folder.mkdir()
    old = folder / "2025년 업무일지.gsheet"
    old.write_text(json.dumps({"doc_id": "OLD" * 10}), encoding="utf-8")
    os.utime(old, (time.time() - 100, time.time() - 100))
    (folder / "2026년 업무일지.gsheet").write_text(
        json.dumps({"url": "https://docs.google.com/spreadsheets/d/NEW_" + "x" * 30 + "/edit"}), encoding="utf-8")
    seen = []

    def download(sid):
        seen.append(sid)
        return xlsx([["11/15", "지난 업무"]], [["11/16", "유튜브 화 14시"]])
    r = worklog.load({"worklog_folder": str(folder), "stale_days": 3}, TODAY, download)
    assert seen == ["NEW_" + "x" * 30]
    assert r["label"] == "2026년 업무일지" and r["last_date"] == "2026-11-16" and r["warning"] is None
    assert [t["text"] for t in r["tasks"]] == ["지난 업무", "유튜브 화 14시"]


def test_링크가_폴더보다_먼저_그리고_내려받은_파일도_읽음(tmp_path):
    r = worklog.load({"worklog_url": "https://docs.google.com/spreadsheets/d/" + "L" * 30 + "/edit#gid=0"},
                     TODAY, lambda sid: xlsx([["11/16", "링크 업무"]]))
    assert r["tasks"][0]["text"] == "링크 업무"
    folder = tmp_path / "f"
    folder.mkdir()
    (folder / "업무일지.csv").write_bytes("날짜,업무\n11/16,CSV 업무\n".encode("cp949"))
    r = worklog.load({"worklog_folder": str(folder)}, TODAY, None)
    assert r["tasks"][0] == {"key": r["tasks"][0]["key"], "date": "2026-11-16", "text": "CSV 업무", "source": "worklog"}


def test_오래된_업무일지_경고(tmp_path):
    r = worklog.load({"worklog_url": "https://docs.google.com/spreadsheets/d/" + "L" * 30, "stale_days": 3},
                     TODAY, lambda sid: xlsx([["11/10", "옛 업무"]]))
    assert "새 시트" in r["warning"]


@pytest.mark.parametrize("cfg, words", [
    ({}, "설정되지"), ({"worklog_folder": "Z:/없는폴더"}, "찾지 못했습니다"),
    ({"worklog_url": "https://example.com/x"}, "구글 시트 주소가 아닙니다"),
])
def test_못_읽으면_경고만_하고_죽지_않음(cfg, words):
    r = worklog.load(cfg, TODAY, None)
    assert r["tasks"] == [] and words in r["warning"]


def test_공유가_꺼져_로그인_화면이_오면_안내(monkeypatch):
    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: Resp("<!doctype html><html>로그인".encode()))
    with pytest.raises(WorklogError, match="공유"):
        worklog.download_xlsx("x" * 30)
