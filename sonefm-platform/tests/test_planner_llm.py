"""⑥ LLM 분류와 평가 실행기의 LLM 모드·--estimate 테스트. 가짜 공급자만 쓴다(실제 호출·키 없음). 예문은 모두 지어낸 것."""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

from hub import recipes as R
from hub.ai import gateway as GW
from hub.ai.gateway import Gateway
from hub.ai.providers import FakeProvider, ProviderNetworkError, ProviderReply
from hub.paths import resource_dir
from hub.planner import llm as L
from hub.planner.plan import plan_cells
from hub.planner.types import MISC, load_task_types

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 6, 22, 9, 0).astimezone()


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


def sent_units(call: dict) -> list[dict]:
    """공급자가 받은 첫 사용자 메시지의 업무 목록."""
    return json.loads(call["messages"][0]["content"].split("\n", 1)[1])["units"]


def answer(per_unit):
    """per_unit(unit) -> tasks. 받은 업무마다 답을 만들어 주는 가짜 응답."""
    def reply(call):
        return json.dumps({"units": [{"unit_id": u["unit_id"], "tasks": per_unit(u)} for u in sent_units(call)]},
                          ensure_ascii=False)
    return reply


def clf_for(tt, provider, key="k-test", **kw) -> L.LLMClassifier:
    g = Gateway(key_getter=lambda: key, provider_factory=lambda k: provider)
    return L.LLMClassifier(tt, complete=g.complete, **kw)


MEET = lambda u: [{"type": "meeting", "sentence_kind": "지시", "values": {}, "confidence": 0.8}]   # noqa: E731


def test_정상_분류와_기록(tt, recipes):
    p = FakeProvider(answer(MEET))
    clf = clf_for(tt, p)
    cells = [cell("이것저것 알아보기. 5월 일입니다"), cell("스튜디오 청소와 분리수거 부탁드립니다")]
    plan = plan_cells(cells, tt, recipes, [], llm=clf, now=NOW)
    assert len(p.calls) == 1 and len(sent_units(p.calls[0])) == 1       # 확정된 청소 업무는 안 보낸다
    u = sent_units(p.calls[0])[0]
    assert u["date"] == "2026-06-22" and u["dept"] == "홍보부" and set(u) == {"unit_id", "date", "dept", "text", "keyword_top"}
    assert p.calls[0]["model"] == "claude-haiku-5-5"                   # light
    assert "meeting" in p.calls[0]["system"] and "기준일" in p.calls[0]["system"]
    it = next(i for i in plan["items"] if i["classified_by"] == "llm")
    assert it["type"] == "meeting" and it["llm_type"] == "meeting" and it["confidence"] == 0.8
    eng = plan["engine"]["llm"]
    assert eng["used"] and eng["calls"] == 1 and eng["tier"] == "light" and eng["model"] == "claude-haiku-5-5"
    assert clf.calls_log[0]["ok"] and clf.calls_log[0]["units"] == 1


def test_system은_매번_같고_스키마는_유형_enum(tt):
    a, b = L.build_system(tt), L.build_system(load_task_types())
    assert a == b and len(a) > 3000
    enum = L.build_schema(tt)["properties"]["units"]["items"]["properties"]["tasks"]["items"]["properties"]["type"]["enum"]
    assert len(enum) == 60 and MISC in enum


def test_스키마_오류_뒤_재시도(tt, recipes):
    good = answer(MEET)
    p = FakeProvider(["이건 JSON이 아닙니다", good])
    plan = plan_cells([cell("이것저것 알아보기")], tt, recipes, [], llm=clf_for(tt, p), now=NOW)
    assert len(p.calls) == 2                                           # 게이트웨이가 한 번 더 물었다
    assert plan["items"][0]["type"] == "meeting" and "llm_failed" not in plan["items"][0]["flags"]
    assert plan["engine"]["llm"]["calls"] == 1                         # 우리 쪽 묶음 호출은 하나


def test_모르는_type은_스키마에서_걸러져_키워드_결과(tt, recipes):
    bad = answer(lambda u: [{"type": "not_a_type", "sentence_kind": "지시"}])
    p = FakeProvider([bad, bad])
    plan = plan_cells([cell("이것저것 알아보기")], tt, recipes, [], llm=clf_for(tt, p), now=NOW)
    it = plan["items"][0]
    assert it["type"] == MISC and it["classified_by"] == "keyword" and "llm_failed" in it["flags"]


def test_잘못된_값은_버리고_좋은_값만_남김(tt, recipes):
    p = FakeProvider(answer(lambda u: [{"type": "meeting", "sentence_kind": "지시", "confidence": 0.9, "values": {
        "date": "2026-13-45", "time": "9시", "bogus": "x", "month": "2026-05", "programs": [{"name": "없는방송", "episodes": [1]}]}}]))
    plan = plan_cells([cell("이것저것 알아보기")], tt, recipes, [], llm=clf_for(tt, p), now=NOW)
    it = plan["items"][0]
    assert it["type"] == "meeting"
    assert "date" not in it["values"] or it["value_sources"].get("date") != "llm"
    assert "bogus" not in it["values"] and it["values"].get("time") != "9시"
    assert "programs" not in it["values"] or it["value_sources"].get("programs") != "llm"
    if "month" in tt["types"]["meeting"]["value_keys"]:
        assert it["values"]["month"] == "2026-05" and it["value_sources"]["month"] == "llm"


def test_코드_값과_다르면_코드_값을_유지(tt, recipes):
    p = FakeProvider(answer(lambda u: [{"type": "meeting", "sentence_kind": "지시", "values": {"month": "2026-04"}}]))
    plan = plan_cells([cell("이것저것 5월 회의")], tt, recipes, [], llm=clf_for(tt, p), now=NOW)
    it = plan["items"][0]
    assert it["values"]["month"] == "2026-05" and "value_mismatch" in it["flags"]


def test_여러_tasks는_쪼개고_키워드와_다르면_표시(tt, recipes):
    p = FakeProvider(answer(lambda u: [
        {"type": "meeting", "sentence_kind": "지시", "values": {}},
        {"type": "cleaning", "sentence_kind": "지시", "values": {}}]))
    plan = plan_cells([cell("이것저것 알아보기")], tt, recipes, [], llm=clf_for(tt, p), now=NOW)
    its = [i for i in plan["items"] if i["classified_by"] == "llm"]
    assert [i["type"] for i in its] == ["meeting", "cleaning"]
    assert "split_by_llm" in its[1]["flags"] and "split_by_llm" not in its[0]["flags"]
    assert its[1]["id"].endswith(".2")


def test_키워드_top과_다르면_keyword_llm_mismatch(tt, recipes):
    # '청소' 한 낱말: 점수가 모자라 애매 → LLM으로 간다
    p = FakeProvider(answer(MEET))
    plan = plan_cells([cell("청소")], tt, recipes, [], llm=clf_for(tt, p), now=NOW)
    it = plan["items"][0]
    assert it["keyword"]["top"] == "cleaning" and it["type"] == "meeting" and "keyword_llm_mismatch" in it["flags"]


@pytest.mark.parametrize("err", [ProviderNetworkError("끊김")])
def test_AIError면_키워드_결과를_두고_계획은_계속(tt, recipes, err):
    p = FakeProvider([err])
    clf = clf_for(tt, p)
    plan = plan_cells([cell("청소"), cell("이것저것 알아보기")], tt, recipes, [], llm=clf, now=NOW)
    assert [i["type"] for i in plan["items"]] == ["cleaning", MISC]
    assert all("llm_failed" in i["flags"] and i["classified_by"] == "keyword" for i in plan["items"])
    assert clf.calls_log[0]["ok"] is False and clf.calls_log[0]["error"] == "network"
    assert plan["engine"]["llm"]["calls"] == 1 and plan["engine"]["llm"]["cost_usd"] == 0


def test_키_없음이면_공급자를_안_부르고_남은_묶음도_멈춤(tt, recipes):
    p = FakeProvider([])
    clf = clf_for(tt, p, key=None, batch_size=1)
    plan = plan_cells([cell("이것저것 알아보기 가"), cell("이것저것 알아보기 나")], tt, recipes, [], llm=clf, now=NOW)
    assert p.calls == [] and len(clf.calls_log) == 1                  # off는 한 번만 시도하고 멈춘다
    assert [i["type"] for i in plan["items"]] == [MISC, MISC]
    assert all("llm_failed" in i["flags"] for i in plan["items"])


def test_묶음은_12개씩_나눠_호출(tt, recipes):
    p = FakeProvider(answer(MEET))
    cells = [cell(f"이것저것 알아보기 {n}번째") for n in range(30)]
    plan = plan_cells(cells, tt, recipes, [], llm=clf_for(tt, p), now=NOW)
    assert [len(sent_units(c)) for c in p.calls] == [12, 12, 6]
    assert plan["engine"]["llm"]["calls"] == 3 and all(i["classified_by"] == "llm" for i in plan["items"])
    assert plan["engine"]["llm"]["cost_usd"] >= 0


def test_all_units는_확정_업무도_보내고_계정_칸은_끝까지_제외(tt, recipes):
    secret = "Zx9y8w7v6u"
    cells = [cell("카카오톡 채널 확인\n- 아이디: abc123 / 비번: " + secret), cell("스튜디오 청소와 분리수거 부탁드립니다"),
             cell("이것저것 알아보기")]
    for all_units, expect in ((False, 1), (True, 2)):
        p = FakeProvider(answer(MEET))
        plan_cells(cells, tt, recipes, [], llm=clf_for(tt, p), now=NOW, all_units=all_units)
        assert len(sent_units(p.calls[0])) == expect
        wire = json.dumps([c["messages"] for c in p.calls], ensure_ascii=False)
        assert secret not in wire and "abc123" not in wire and "카카오톡" not in wire   # G1 칸은 보낸 글에 없다


def test_가로챈_호출에서_G1_칸이_없음(tt, recipes, monkeypatch):
    seen = []

    def spy(feature, tier, messages, system=None, tools=None, schema=None, max_tokens=4096):
        seen.append((feature, tier, json.dumps(messages, ensure_ascii=False)))
        raise GW.AIError("network")

    cells = [cell("카카오톡 채널 확인\n- 비번: Qq1w2e3r4t5y"), cell("이것저것 알아보기")]
    clf = L.LLMClassifier(tt, complete=spy, feature=L.FEATURE_EVAL, tier="heavy")
    plan_cells(cells, tt, recipes, [], llm=clf, now=NOW, all_units=True)
    assert seen and seen[0][0] == "planner.eval" and seen[0][1] == "heavy"
    assert "Qq1w2e3r4t5y" not in seen[0][2] and "카카오톡" not in seen[0][2]


# ---- 평가 실행기 ---------------------------------------------------------------------------

def write_set(tmp_path: Path) -> Path:
    src = tmp_path / "set.csv"
    with open(src, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["번호", "날짜", "부서", "원문", "문장 종류", "정답 업무 목록", "처리 방식"])
        w.writerow(["1", "2026-06-22", "총괄", "스튜디오 청소와 분리수거 부탁드립니다.", "지시", json.dumps([{"type": "cleaning", "values": {}}]), "checklist"])
        w.writerow(["2", "2026-06-22", "총괄", "이것저것 알아보기", "지시", json.dumps([{"type": "meeting", "values": {}}]), "checklist"])
        w.writerow(["3", "2026-06-22", "총괄", "카카오톡 채널 확인 / 비번: Zz9y8x7w6v", "지시", json.dumps([{"type": "kakao_channel_check", "values": {}}]), "checklist"])
    return src


def test_estimate는_게이트웨이를_부르지_않는다(tmp_path, monkeypatch, capsys):
    sys.path.insert(0, str(ROOT / "tools"))
    import planner_eval as E

    def boom(*a, **k):
        raise AssertionError("게이트웨이를 불렀습니다")

    monkeypatch.setattr(GW, "complete", boom)
    monkeypatch.setattr(GW.Gateway, "complete", boom)
    monkeypatch.setattr(GW.Gateway, "_complete", boom)
    src = write_set(tmp_path)
    outs = {}
    for mode in ("keyword", "llm-light", "llm-heavy", "llm-only-heavy"):
        assert E.main(["--set", str(src), "--mode", mode, "--estimate"], ai_ready=boom) == 0
        outs[mode] = json.loads(capsys.readouterr().out)
    assert outs["keyword"]["calls"] == 0 and outs["keyword"]["system_chars"] == 0
    assert outs["llm-light"]["units_sent"] == 1 and outs["llm-light"]["calls"] == 1
    assert outs["llm-only-heavy"]["units_sent"] == 2 and outs["llm-only-heavy"]["tier"] == "heavy"   # 계정 칸 제외
    assert outs["llm-light"]["system_chars"] > 3000 and outs["llm-light"]["messages_chars_total"] > 0
    assert outs["llm-only-heavy"]["est_output_tokens"] == 2 * L.OUT_TOKENS_PER_UNIT


def test_LLM_모드는_키_없음이면_호출_없이_안내하고_끝(tmp_path, monkeypatch, capsys):
    sys.path.insert(0, str(ROOT / "tools"))
    import planner_eval as E

    def boom(*a, **k):
        raise AssertionError("호출 시도")

    monkeypatch.setattr(GW, "complete", boom)
    rc = E.main(["--set", str(write_set(tmp_path)), "--mode", "llm-light"], ai_ready=lambda: (False, "API 키가 없습니다."))
    assert rc == 1 and "호출 없음" in capsys.readouterr().err


def test_LLM_모드_지표(tmp_path, monkeypatch, capsys):
    sys.path.insert(0, str(ROOT / "tools"))
    import planner_eval as E
    p = FakeProvider(lambda call: ProviderReply(answer(MEET)(call), input_tokens=1000, output_tokens=100, cache_read_tokens=800))
    real = L.LLMClassifier

    def make(ref_types, **kw):
        return real(ref_types, complete=Gateway(key_getter=lambda: "k", provider_factory=lambda k: p).complete, **kw)

    monkeypatch.setattr(E, "LLMClassifier", make)
    out = tmp_path / "r.json"
    rc = E.main(["--set", str(write_set(tmp_path)), "--mode", "llm-only-heavy", "--runs", "2", "--out", str(out)],
                ai_ready=lambda: (True, ""))
    assert rc == 0
    res = json.loads(out.read_text(encoding="utf-8"))
    m = res["M5_M6"]
    assert m["calls"] == 1 and m["units_sent"] == 2 and m["failed_calls"] == 0 and m["retried_calls"] == 0
    assert m["cost_usd"] > 0 and m["cost_per_llm_unit"] == pytest.approx(m["cost_usd"] / 2, rel=1e-3)
    assert m["cache_read_tokens"] == 800 and m["latency_ms"]["median"] is not None
    assert res["runs"] == 2 and res["runs_summary"]["M5_M6.calls"] == {"mean": 1, "min": 1, "max": 1}
    assert len(p.calls) == 2
    assert "Zz9y8x7w6v" not in json.dumps([c["messages"] for c in p.calls], ensure_ascii=False)
