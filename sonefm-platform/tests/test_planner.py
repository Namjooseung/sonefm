"""플래너(LLM 없는 부분) 단계별 테스트. 예문은 모두 지어낸 것이다(실제 업무일지 문장·실명 금지)."""
from __future__ import annotations

import csv
import json
import sys
from datetime import date, datetime
from pathlib import Path

import openpyxl
import pytest

from hub import recipes as R
from hub.paths import resource_dir
from hub.planner import classify as C
from hub.planner import masking as M
from hub.planner import values as V
from hub.planner.kind import DONE, INSTRUCT, PROGRESS, sentence_kind
from hub.planner.plan import analyze_cells, apply_llm_result, build_llm_bundle, pending, plan_cells
from hub.planner.split import split_by_pieces, split_structure
from hub.planner.types import MISC, load_task_types
from hub.planner.worklog_tabs import (cells_from_rows, fix_years, looks_like_tab_worklog, parse_tab_name,
                                      read_tab_worklog)

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def tt():
    return load_task_types()


@pytest.fixture(scope="module")
def recipes():
    rs, problems = R.load_recipes([resource_dir() / "recipes"])
    assert not problems
    return rs


def cell(text: str, d: str = "2026-06-22", dept: str = "홍보부") -> dict:
    return {"date": d, "date_end": d, "dept": dept, "text": text, "note": ""}


# ---- 유형 사전 -----------------------------------------------------------------------

def test_유형_사전이_저장소에_있고_레시피와_맞음(tt, recipes):
    assert len(tt["types"]) == 59 and MISC in tt["types"]
    for r in recipes.values():                       # 레시피가 선언한 유형은 사전에 있어야 한다
        for t in r.get("task_types", []):
            assert t in tt["types"], f"{r['id']}: 모르는 유형 {t}"
    by_type = R.recipes_by_type(recipes)
    for tid, t in tt["types"].items():               # recipe_exists인 유형은 그 유형을 선언한 레시피가 있어야 한다
        if t["automation"]["status"] == "recipe_exists":
            assert by_type[tid]["id"] == t["automation"]["recipe"]
    assert "영상" not in recipes["youtube-video"]["keywords"]
    for t in tt["types"].values():
        assert t["value_keys"]


# ---- ② 가리기 --------------------------------------------------------------------------

def test_G1_계정_칸_판정():
    for s in ("아이디: abc / 비번: x", "비밀번호는 메모장", "PW 확인", "password reset", "계정 확인", "ID: abc"):
        assert M.is_credential_cell(s), s
    for s in ("스튜디오 청소", "카카오톡 채널 확인", "paid 확인", "idea 회의"):
        assert not M.is_credential_cell(s), s


def test_G2_G3_G4_가리기():
    out = M.mask_text("접속 https://example.com/a?b=1 과 www.example.org 그리고 example.net/x 로 가세요")
    assert out.count("[링크]") == 3 and "example" not in out
    assert M.mask_text("아이디: kim01 / 비번: Ab12cd34!") == "아이디: [가림] / 비번: [가림]"
    assert M.mask_text("(비번) Zz9y8x7w6") == "(비번) [가림]"
    assert "Q1w2E3r4T5" not in M.mask_text("임시값 Q1w2E3r4T5 입니다")
    assert M.mask_text("파일은 report_final.xlsx 입니다").count("[가림]") == 0   # 숫자가 없으면 그대로
    assert M.mask_text("[링크] [가림] 청소") == "[링크] [가림] 청소"                # 이미 가린 것은 그대로
    assert "tok3n9xyz" not in M.mask_text("a@example.com tok3n9xyz 로 보내기")


def test_G6_이름_가리기():
    assert M.mask_names("김가나 씨와 이 씨", ["김가나", "이"]) == "[이름] 씨와 이 씨"   # 1글자 이름은 무시


def test_위반_검사():
    assert M.find_violations("청소하기") == []
    assert "G2" in M.find_violations("https://example.com")
    assert "G1" in M.find_violations("아이디: [가림]")
    assert "G3" in M.find_violations("비번: abcdef")
    assert "G4" in M.find_violations("tok3n9xyz")


# ---- ① 읽기 ----------------------------------------------------------------------------

def test_탭_이름_보정():
    p = parse_tab_name("250918.")
    assert p["start"] == date(2025, 9, 18) and not p["copy"]
    p = parse_tab_name("250826~27")
    assert (p["start"], p["end"]) == (date(2025, 8, 26), date(2025, 8, 27))
    assert parse_tab_name("260105의 사본")["copy"]
    assert parse_tab_name("요약") is None and parse_tab_name("991399") is None
    assert looks_like_tab_worklog(["250101", "250102", "메모"]) and not looks_like_tab_worklog(["Sheet1"])


def test_연도_오타는_앞뒤_탭에_맞춘다():
    parsed = [parse_tab_name(n) for n in ("250102", "240103", "250104")]
    fix_years(parsed)
    assert parsed[1]["start"] == date(2025, 1, 3)


def _workbook(path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "260512"
    ws.append(["안내문 한 줄"])
    ws.append(["가나", "다라", "부서", "내용", "비고"])
    ws.append([True, False, "편성제작", "스튜디오를 청소합니다.\n- 바닥 쓸기", "개인 일정"])
    ws.append([None, None, None, "방송일지를 적어 주세요.", None])
    ws.append([None, None, "홍보부", "TRUE", None])
    ws.append([None, None, "홍보부", "뉴스레터 만들기", None])
    ws.append([None, None, None, None, "표 밖 안내문은 내용 열이 아니다"])
    ws2 = wb.create_sheet("260512의 사본")
    for row in ws.iter_rows(values_only=True):
        ws2.append(list(row))
    ws3 = wb.create_sheet("260513")
    ws3.append([None, "부서", "내용"])           # 비고 열이 없는 모양
    ws3.append([None, "총괄", "우편물 확인"])
    wb.create_sheet("요약").append(["아무 글"])
    wb.save(path)


def test_날짜별_탭_업무일지_읽기(tmp_path):
    f = tmp_path / "w.xlsx"
    _workbook(f)
    r = read_tab_worklog(f)
    assert r["workers"] == ["가나", "다라"] and r["skipped"] == ["요약"]
    cells = r["cells"]
    assert [c["date"] for c in cells] == ["2026-05-12"] * 3 + ["2026-05-13"]    # 원본과 같은 사본은 버린다
    assert cells[0]["dept"] == "편성제작부" and cells[0]["note"] == "개인 일정"
    assert cells[1]["dept"] == "편성제작부"                                     # 병합 칸은 위 값으로 채움
    assert [c["text"] for c in cells][2] == "뉴스레터 만들기"                    # 체크박스 값은 업무가 아님
    assert cells[3]["dept"] == "총괄" and cells[3]["note"] == ""
    only = read_tab_worklog(f, only_dates={"2026-05-13"})
    assert len(only["cells"]) == 1 and only["sheets"] == 1
    assert cells_from_rows([("가", "나")], date(2026, 1, 1), date(2026, 1, 1), "x") == ([], [])


def test_다른_내용이면_사본도_읽는다(tmp_path):
    wb = openpyxl.Workbook()
    a = wb.active
    a.title = "260601"
    a.append(["부서", "내용"])
    a.append(["총괄", "청소"])
    b = wb.create_sheet("260601의 사본")
    b.append(["부서", "내용"])
    b.append(["총괄", "청소와 정리"])
    f = tmp_path / "w.xlsx"
    wb.save(f)
    assert len(read_tab_worklog(f)["cells"]) == 2


# ---- ③ 쪼개기 --------------------------------------------------------------------------

def test_번호줄_제목줄_단계줄():
    us = split_structure("1. 청소하기\n2. 메일 확인하기")
    assert [u.text for u in us] == ["청소하기", "메일 확인하기"]
    assert len(split_structure("1. 청소하기")) == 1                                # 번호줄 하나는 쪼개지 않는다
    us = split_structure("<뉴스레터>\n- 초안\n- 검토\n<청소>\n- 바닥")
    assert [u.text.split("\n")[0] for u in us] == ["<뉴스레터>", "<청소>"]
    assert us[0].steps == ["초안", "검토"] and us[1].steps == ["바닥"]
    us = split_structure("- 기자증 작성\n- 신청서 작성\n- 메일 발송")
    assert [u.text for u in us] == ["기자증 작성", "신청서 작성", "메일 발송"] and us[0].steps == []
    us = split_structure("*매일 확인\n*예약 사이트\n[링크]")
    assert len(us) == 1 and us[0].steps == ["예약 사이트"]


def test_조각은_서로_다른_유형으로_확정될_때만_쪼갠다(tt):
    idx = C.KeywordIndex(tt)
    one = split_structure("분리수거하기 / 방송일지 작성")[0]
    two = split_by_pieces(one, idx.confirmed_type)
    assert len(two) == 2 and two[0].split_by == "pieces"
    same = split_structure("분리수거하기, 쓰레기 버리기")[0]
    assert len(split_by_pieces(same, idx.confirmed_type)) == 1                   # 같은 유형 → 한 업무
    vague = split_structure("분리수거하기 / 이것저것 정리")[0]
    assert len(split_by_pieces(vague, idx.confirmed_type)) == 1                  # 확정 못 한 조각 → 쪼개지 않음
    date_text = split_structure("6/21 방송일지 작성")[0]
    assert len(split_by_pieces(date_text, idx.confirmed_type)) == 1


# ---- ④ 문장 종류 -----------------------------------------------------------------------

@pytest.mark.parametrize("text,kind", [
    ("스튜디오 청소해 주세요.", INSTRUCT),
    ("방송일지 작성", INSTRUCT),                       # 명사로 끝나면 지시
    ("메일 발송", INSTRUCT),
    ("방송일지 작성 중입니다", PROGRESS),
    ("회계 정리 진행 중", PROGRESS),
    ("썸네일 제작 했습니다", DONE),
    ("정산 서류 제출을 완료했습니다.", DONE),
])
def test_문장_종류(text, kind):
    assert sentence_kind(text) == (kind, False)


def test_보고와_지시가_섞이면_애매():
    k, unsure = sentence_kind("파일에 표기했습니다.\n확인해 주세요.")
    assert k == INSTRUCT and unsure


# ---- ⑤ 키워드 ----------------------------------------------------------------------------

def test_점수와_확정_애매_미분류(tt):
    idx = C.KeywordIndex(tt)
    r = idx.classify("스튜디오 청소와 분리수거")
    assert r["top"] == "cleaning" and r["state"] == C.CONFIRMED and r["score"] >= C.CONFIRM_MIN
    assert idx.classify("이것저것 알아보기")["state"] == C.UNCLASSIFIED
    assert idx.classify("청소")["state"] == C.AMBIGUOUS                          # 2글자 < 기준
    assert C.decide(idx.classify("이것저것 알아보기")) == MISC


def test_더_긴_키워드가_짧은_키워드를_가린다(tt):
    idx = C.KeywordIndex(tt)
    sc = idx.scores("업로드 안된 방송 확인")
    assert sc == {"youtube_backlog_check": 5}
    assert "sns_homepage_post" not in idx.scores("유튜브 업로드 하기")                 # 긴 키워드가 짧은 것을 가린다
    assert idx.classify("유튜브 방송 듣고 댓글 남기기")["top"] == "youtube_comment"


def test_드라이브는_라이브가_아님(tt):
    idx = C.KeywordIndex(tt)
    assert "live_show" not in idx.scores("파일을 드라이브에 올려 주세요")
    assert "live_show" in idx.scores("13시 라이브")


def test_제목만으로_확정되면_제목을_쓴다(tt):
    idx = C.KeywordIndex(tt)
    r = idx.classify("<굿모닝 굿바이 제작>\n- 5개 만들어서 드라이브에 업로드해주세요.")
    assert r["top"] == "jingle_bridge_make" and r["state"] == C.CONFIRMED


# ---- ⑦ 값 ------------------------------------------------------------------------------

REF = date(2026, 6, 22)   # 월요일


def vals(text, keys, ref=REF, programs=None):
    return V.extract_values(text, keys, ref, programs)[0]


def test_연도_규칙():
    assert V.fit_year(5, REF) == 2026
    assert vals("5월 보고", ["month"]) == {"month": "2026-05"}
    assert vals("12월 보고", ["month"], date(2026, 1, 5)) == {"month": "2025-12"}      # 2개월 넘게 뒤 → 전년
    assert vals("1월 보고", ["month"], date(2025, 12, 20)) == {"month": "2026-01"}     # 10개월 넘게 앞 → 다음 해
    assert vals("8월 19일(수) 점심", ["date"]) == {"date": "2026-08-19"}
    assert vals("2025년 11월 3일 행사", ["date"]) == {"date": "2025-11-03"}
    assert vals("6/21일 기사", ["date"]) == {"date": "2026-06-21"}


def test_월_여러_개와_상대_월():
    assert vals("9월 못 올린 것, 10월 올리기", ["month", "months"], date(2026, 10, 12)) == {"months": ["2026-09", "2026-10"]}
    assert vals("9월 못 올린 것, 10월 올리기", ["month"], date(2026, 10, 12)) == {"month": "2026-09"}
    assert vals("지난달 정리", ["month"]) == {"month": "2026-05"}
    assert vals("다음 달 계획", ["month"]) == {"month": "2026-07"}
    assert vals("1월 완료, 2월 3월 진행", ["done_month", "months"], date(2026, 5, 6)) == {
        "done_month": "2026-01", "months": ["2026-02", "2026-03"]}
    assert vals("11월 1주 편성표", ["month", "week"], date(2025, 10, 27)) == {"month": "2025-11", "week": "2025-11 1주"}


def test_주_기간_기한():
    assert vals("이번 주 송출", ["period"], date(2026, 8, 4)) == {"period": "2026-08-03~2026-08-09"}
    assert vals("다음 주 송출", ["period"]) == {"period": "2026-06-29~2026-07-05"}
    assert "period" not in vals("메일 보내주세요", ["period"])                       # '보내주세요'의 '내주'는 다음 주가 아님
    assert vals("(1~5월) 올리기", ["period"], date(2026, 7, 14)) == {"period": "2026-01~2026-05"}
    assert vals("2021~2026년 파일", ["period"]) == {"period": "2021~2026"}
    assert vals("10일까지 제출", ["deadline"], date(2025, 6, 9)) == {"deadline": "2025-06-10"}
    assert vals("5일까지 제출", ["deadline"], date(2025, 6, 9)) == {"deadline": "2025-07-05"}     # 지났으면 다음 달
    assert vals("월요일 퇴근까지", ["deadline"], date(2025, 7, 28)) == {"deadline": "2025-07-28"}   # 당일 포함
    assert vals("월요일까지", ["deadline"], date(2025, 7, 29)) == {"deadline": "2025-08-04"}
    assert vals("내일까지 마무리", ["deadline"]) == {"deadline": "2026-06-23"}
    assert vals("6월 30일까지", ["deadline", "date"]) == {"deadline": "2026-06-30"}


def test_시각_수량_길이():
    assert vals("오후 1시 라이브", ["time"]) == {"time": "13:00"}
    assert vals("13시 라이브", ["time", "date"]) == {"time": "13:00", "date": "2026-06-22"}
    assert "time" not in vals("1시에 출근해서 알려줄게요", ["time"])
    assert "time" not in vals("2시간 걸려요", ["time"])
    assert vals("(예: 월요일 10시 방송)", ["time"]) == {}
    assert vals("하루 3~5개 작성", ["quantity"]) == {"quantity": "3~5개"}
    assert vals("대본 2부 출력", ["quantity"]) == {"quantity": "2부"}
    assert vals("57분으로 편집", ["duration"]) == {"duration": "57분"}
    assert vals("월, 금 확인", ["weekdays"]) == {"weekdays": ["월", "금"]}
    assert vals("10기 대신 11기 모집", ["cohort"]) == {"cohort": "11기"}


def test_키는_유형의_value_keys_안에서만():
    assert vals("5월 10일까지 57분", ["duration"]) == {"duration": "57분"}


def test_프로그램_이름과_회차():
    progs = ["마을이야기", "홍길동의 아침산책", "[교육] 저녁 라디오"]
    v = vals("마을이야기_35화 / 아침산책 6화 / 저녁라디오 48, 49, 50", ["programs"], programs=progs)
    assert v["programs"] == [{"name": "마을이야기", "episodes": [35]}, {"name": "홍길동의 아침산책", "episodes": [6]},
                           {"name": "[교육] 저녁 라디오", "episodes": [48, 49, 50]}]
    assert vals("마을 이야기 48~51", ["programs"], programs=progs)["programs"][0]["episodes"] == [48, 49, 50, 51]
    assert vals("마을이야기", ["programs"], programs=progs)["programs"] == [{"name": "마을이야기", "episodes": []}]
    assert vals("옛 방송 117화, 127화", ["programs"], programs=progs)["programs"] == [{"episodes": [117, 127]}]
    v = vals("선정 마을이야기_3화\n미선정 저녁 라디오_4화", ["programs", "not_selected"], programs=progs)
    assert v["programs"] == [{"name": "마을이야기", "episodes": [3]}] and v["not_selected"][0]["name"] == "[교육] 저녁 라디오"
    assert "programs" not in vals("짧은 이름", ["programs"], programs=["짧음"])            # 4글자 미만은 맞추지 않는다


def test_기본값():
    values, src = {}, {}
    V.apply_defaults(values, src, {"month": "prev_month"}, ["month"], date(2025, 12, 30))
    assert values == {"month": "2025-11"} and src == {"month": "default"}
    values = {"month": "2025-10"}
    V.apply_defaults(values, {"month": "text"}, {"month": "prev_month"}, ["month"], REF)
    assert values == {"month": "2025-10"}                                              # 문장에 값이 있으면 그대로
    assert V.resolve_default("this_week", REF) == "2026-06-22~2026-06-28"
    assert V.resolve_default("next_month", date(2026, 12, 3)) == "2027-01"


# ---- ⑥ 자리, ⑧ 계획 ---------------------------------------------------------------------

NOW = datetime(2026, 6, 22, 9, 0, 0)


def test_계획_기본_흐름(tt, recipes):
    cells = [
        cell("<유튜브 업로드> 5월 방송을 올려 주세요.\n- 파일명 통일\n- 중복 확인", dept="홍보부"),
        cell("스튜디오 청소와 분리수거 부탁드립니다.", dept="총괄"),
        cell("방송일지 작성 중입니다", dept="총괄"),
        cell("이것저것 알아보기", dept="기타"),
        cell("방통위 메일 발송 / 분리수거하기", dept="기타"),
    ]
    plan = plan_cells(cells, tt, recipes, [], now=NOW)
    assert plan["plan_version"] == 1 and plan["engine"]["llm"]["used"] is False
    by_type = {i["type"]: i for i in plan["items"]}
    yt = by_type["youtube_upload"]
    assert yt["mode"] == "recipe" and yt["recipe"]["id"] == "youtube-video" and yt["classified_by"] == "keyword"
    assert yt["values"] == {"month": "2026-05"} and yt["value_sources"] == {"month": "text"}
    run = yt["recipe"]["runs"][0]
    assert run["inputs"]["date"] == "2026-06-22" and set(run["missing"]) == {"hour", "audio", "thumb"}
    assert by_type["cleaning"]["mode"] == "checklist" and by_type["cleaning"]["checklist"]["title"] == "청소·주변 정리"
    assert by_type["cleaning"]["checklist"]["steps"] == []
    assert plan["reports"] == [{**plan["reports"][0], "type": "broadcast_journal", "sentence_kind": PROGRESS}]
    assert by_type[MISC]["keyword"]["state"] == C.UNCLASSIFIED and by_type[MISC]["classified_by"] == "keyword"
    assert plan["engine"]["referred"] >= 1
    assert all(i["id"].count("#") == 1 for i in plan["items"])


def test_체크리스트_단계와_자동화_힌트(tt, recipes):
    plan = plan_cells([cell("<뉴스레터 제작>\n- 초안 쓰기\n- 검토 받기"), cell("방송일지 정리")], tt, recipes, [], now=NOW)
    nl = next(i for i in plan["items"] if i["type"] == "newsletter")
    assert nl["checklist"]["steps"] == [{"text": "초안 쓰기", "done": False}, {"text": "검토 받기", "done": False}]
    bj = next(i for i in plan["items"] if i["type"] == "broadcast_journal")
    assert bj["automation_hint"].startswith("자동화 예정") and bj["checklist"]["steps"] == []


def test_업무_단위_쪼개짐과_기본값(tt, recipes):
    plan = plan_cells([cell("1. 방송일지 작성\n2. 청소하기", d="2025-12-30"),
                       cell("방송통신사업소 월별보고서 제출", d="2025-12-30")], tt, recipes, [], now=NOW)
    ids = [i["id"] for i in plan["items"]]
    assert len(ids) == 3 and ids[0].endswith("#1") and ids[1].endswith("#2")
    rep = next(i for i in plan["items"] if i["type"] == "monthly_broadcast_report")
    assert rep["values"] == {"month": "2025-11"} and rep["value_sources"] == {"month": "default"}


def test_회차마다_실행이_하나씩(tt, recipes):
    foreach = {**recipes["youtube-video"], "foreach": "episodes"}
    inputs = foreach["inputs"] + [{"name": "ep", "label": "회차", "type": "int", "from_value": "episode"}]
    plan = plan_cells([cell("유튜브 업로드: 마을이야기 12화, 13화")], tt, {"youtube-video": {**foreach, "inputs": inputs}},
                      ["마을이야기"], now=NOW)
    it = plan["items"][0]
    runs = it["recipe"]["runs"]
    assert it["type"] == "youtube_upload" and [r["inputs"]["ep"] for r in runs] == [12, 13]
    assert all("audio" in r["missing"] for r in runs)


def test_계정_칸은_LLM으로_보내지_않는다(tt, recipes):
    cells = [cell("카카오톡 채널 확인\n- 아이디: abc123 / 비번: Zx9y8w7v6u\n- 링크: https://example.com/x"),
             cell("이것저것 알아보기 https://example.com/p tok3n9xyz")]
    recs = analyze_cells(cells, tt)
    assert recs[0]["credential"] and "credential_cell" in recs[0]["flags"]
    bundle = build_llm_bundle(recs, all_units=True)
    assert [i["id"] for i in bundle["items"]] == [recs[1]["id"]]
    text = bundle["items"][0]["text"]
    assert M.find_violations(text) == [] and "[링크]" in text and "tok3n9xyz" not in text
    plan = plan_cells(cells, tt, recipes, [], now=NOW)
    first = plan["items"][0]
    assert first["type"] == "kakao_channel_check" and "credential_cell" in first["flags"]
    assert "abc123" not in first["text"] and "Zx9y8w7v6u" not in first["text"] and "example.com" not in first["text"]


def test_계정_칸이_분류_안_되면_사람이_분류(tt, recipes):
    plan = plan_cells([cell("이것저것 확인\n- 비번 메모")], tt, recipes, [], now=NOW)
    it = plan["items"][0]
    assert "credential_cell" in it["flags"] and it["type"] == MISC and "사람이" in it["note"]


def test_묶음에는_비고와_이름이_없다(tt):
    c = {**cell("김가나 씨에게 이것저것 전해 주세요"), "note": "개인 일정입니다"}
    bundle = build_llm_bundle(analyze_cells([c], tt), names=["김가나"])
    assert "개인 일정" not in json.dumps(bundle, ensure_ascii=False) and "김가나" not in bundle["items"][0]["text"]


def test_LLM_자리_합치기(tt, recipes):
    cells = [cell("이것저것 알아보기. 5월 일입니다"), cell("스튜디오 청소와 분리수거 부탁드립니다")]
    seen = {}

    def fake(bundle):
        seen["ids"] = [i["id"] for i in bundle["items"]]
        bid = bundle["items"][0]["id"]
        return {"items": [{"id": bid, "tasks": [
            {"type": "meeting", "sentence_kind": "지시", "values": {"date": "2026-06-30", "bogus": 1, "time": "25:99"}},
            {"type": "not_a_type"},
            {"type": "cleaning", "values": {"month": "2026-05"}}]}]}

    plan = plan_cells(cells, tt, recipes, [], llm=fake, now=NOW)
    assert len(seen["ids"]) == 1                      # 확정된 청소 업무는 보내지 않는다
    assert plan["engine"]["llm"]["used"] is True
    llm_items = [i for i in plan["items"] if i["classified_by"] == "llm"]
    assert [i["type"] for i in llm_items] == ["meeting", "cleaning"]     # 모르는 유형은 버림, 둘로 쪼개짐
    assert "split_by_llm" in llm_items[1]["flags"] and llm_items[0]["values"].get("date") == "2026-06-30"
    assert "bogus" not in llm_items[0]["values"] and "time" not in llm_items[0]["values"]   # 검사에 걸린 값은 버림
    assert llm_items[0]["value_sources"]["date"] == "llm"
    assert llm_items[0]["llm_type"] == "meeting"


def test_LLM_값이_코드_값과_다르면_코드_값(tt, recipes):
    recs = analyze_cells([cell("이것저것 5월 회의")], tt)
    recs = apply_llm_result(recs, {"items": [{"id": recs[0]["id"], "tasks": [
        {"type": "meeting", "values": {"month": "2026-04"}}]}]}, tt, [])
    plan = plan_cells([cell("이것저것 5월 회의")], tt, recipes, [], llm=lambda b: {"items": [
        {"id": b["items"][0]["id"], "tasks": [{"type": "meeting", "values": {"month": "2026-04"}}]}]}, now=NOW)
    it = plan["items"][0]
    assert it["values"]["month"] == "2026-05" and "value_mismatch" in it["flags"]
    assert recs[0]["llm_values"] == {"month": "2026-04"} and pending(recs[0]) in (True, False)


def test_LLM_없으면_애매는_1위_미분류는_misc(tt, recipes):
    plan = plan_cells([cell("청소"), cell("이것저것 알아보기")], tt, recipes, [], now=NOW)
    assert [i["type"] for i in plan["items"]] == ["cleaning", MISC]
    assert plan["engine"]["referred"] == 2


def test_보고는_계획에서_빠지고_참고로만(tt, recipes):
    plan = plan_cells([cell("썸네일 제작 했습니다")], tt, recipes, [], now=NOW)
    assert plan["items"] == [] and plan["reports"][0]["sentence_kind"] == DONE and plan["reports"][0]["type"] == "thumbnail_make"


# ---- 레시피 필드 --------------------------------------------------------------------------

def test_레시피_task_types_from_value_foreach(tmp_path):
    def write(d: Path, name: str, body: dict):
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")

    step = [{"id": "s", "app": "a", "command": "c"}]
    base, user = tmp_path / "base", tmp_path / "user"
    write(base, "a.json", {"id": "a", "name": "A", "steps": step, "task_types": ["t1"]})
    write(base, "b.json", {"id": "b", "name": "B", "steps": step, "task_types": ["t1", "t2"]})
    write(user, "c.json", {"id": "c", "name": "C", "steps": step, "task_types": ["t1"]})
    write(base, "d.json", {"id": "d", "name": "D", "steps": step, "task_types": "잘못"})          # 모양이 다르면 무시
    rs, problems = R.load_recipes([base, user])
    assert not problems and "task_types" not in rs["d"]
    m = R.recipes_by_type(rs)
    assert m["t1"]["id"] == "c" and m["t2"]["id"] == "b"                    # 사용자 폴더 우선, 그 밖에는 파일 이름 순

    rs2, _ = R.load_recipes([base])
    assert R.recipes_by_type(rs2)["t1"]["id"] == "a"

    r = {"foreach": "episodes", "inputs": [
        {"name": "date", "label": "날짜", "type": "date", "from_value": "date", "default": "{task.date}"},
        {"name": "ep", "label": "회차", "type": "int", "from_value": "episode"},
        {"name": "hour", "label": "시각", "type": "int", "from_text": r"(\d{1,2})\s*시"},
        {"name": "audio", "label": "mp3", "type": "file"},
        {"name": "memo", "label": "메모", "optional": True}]}
    runs = R.plan_runs(r, {"date": "2026-06-01", "programs": [{"name": "P", "episodes": [3, 4]}]},
                       {"date": "2026-06-22", "text": "10시"})
    assert [x["inputs"]["ep"] for x in runs] == [3, 4] and runs[0]["inputs"]["date"] == "2026-06-01"
    assert runs[0]["inputs"]["hour"] == 10 and runs[0]["missing"] == ["audio"] and "mp3" in runs[0]["questions"][0]
    one = R.plan_runs({"inputs": [{"name": "date", "default": "{task.date}"}]}, {}, {"date": "2026-06-22", "text": ""})
    assert one == [{"inputs": {"date": "2026-06-22"}, "missing": [], "questions": []}]
    assert R.foreach_items({"foreach": "episodes"}, {}) == [{}]                # 목록이 비면 실행 하나
    f = R.form({"inputs": [{"name": "d", "from_value": "date", "default": "x"}]}, {"values": {"date": "2026-01-01"}}, {})
    assert f[0]["value"] == "2026-01-01"


# ---- 평가 실행기 ---------------------------------------------------------------------------

def test_평가_실행기(tmp_path):
    sys.path.insert(0, str(ROOT / "tools"))
    import planner_eval as E
    gold = [{"type": "cleaning", "values": {}}]
    gold2 = [{"type": "youtube_upload", "values": {"months": ["2026-09", "2026-10"]}}]
    src = tmp_path / "set.csv"
    with open(src, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["번호", "날짜", "부서", "원문", "문장 종류", "정답 업무 목록", "처리 방식"])
        w.writerow(["1", "2026-06-22", "총괄", "스튜디오 청소 부탁드립니다.", "지시", json.dumps(gold, ensure_ascii=False), "checklist"])
        w.writerow(["2", "2026-10-12", "홍보부", "<유튜브 업로드> 9월 10월 방송", "지시", json.dumps(gold2, ensure_ascii=False), "recipe"])
        w.writerow(["3", "2026-06-22", "총괄", "방송일지 작성 중입니다", "진행 보고", json.dumps([{"type": "broadcast_journal", "values": {}}]), "checklist"])
        w.writerow(["4", "2026-06-22", "총괄", "청소 / 아이디: abc", "지시", json.dumps([{"type": "meeting", "values": {}}]), "checklist"])
    out = tmp_path / "out" / "r.json"
    out.parent.mkdir()
    assert E.main(["--set", str(src), "--mode", "keyword", "--runs", "1", "--out", str(out)]) == 0
    res = json.loads(out.read_text(encoding="utf-8"))
    assert res["rows"] == 4 and res["M2"]["accuracy"] == 1.0 and res["M2"]["report_recall"] == 1.0
    assert res["M1"]["tp"] == 3 and res["M1"]["pred"] == 4 and res["M1"]["gold"] == 4
    assert res["M3"]["date_keys"]["recall"] == 1.0 and res["M4"]["recipe"]["precision"] == 1.0
    assert res["M7"]["violations"] == 0 and res["M7"]["credential_units_sent"] == 0
    assert [e["번호"] for e in res["errors"]["M1"]] == ["4"]
    dump = json.dumps(res, ensure_ascii=False)
    assert "청소 부탁" not in dump and "abc" not in dump                   # 원문은 결과에 없다
    assert [p.name for p in tmp_path.rglob("*") if p.is_file()].count("r.json") == 1


def test_평가_값_비교():
    sys.path.insert(0, str(ROOT / "tools"))
    import planner_eval as E
    assert E._name_match("교육 공감", "교육공감") and E._name_match("[교육] 마을 이야기", "마을이야기")
    assert E._name_match("마을이야기 라디오", "마을이야기") and not E._name_match("가나다라", "마바사아")
    assert E._name_match("홍길동 마을이야기 라디오", "[이름] 마을이야기")                     # 정답의 [이름]은 지우고 비교
    assert E._name_match(None, None) and not E._name_match(None, "마을이야기")
    p = ("programs", (None, frozenset({117, 127})))
    assert E.pair_equal(("programs", (None, frozenset({127, 117}))), p)
    assert not E.pair_equal(("programs", ("마을이야기", frozenset({1}))), ("programs", ("마을이야기", frozenset({2}))))
    assert E.pair_equal(("quantity", "3~5개"), ("quantity", "하루 3~5개"))
    assert E.pair_equal(("month", "2026-05"), ("month", "2026-05")) and not E.pair_equal(("month", "2026-05"), ("month", "2026-06"))
    assert E.count_matches([("date", "2026-01-01")], [("date", "2026-01-01"), ("date", "2026-01-01")]) == 1
