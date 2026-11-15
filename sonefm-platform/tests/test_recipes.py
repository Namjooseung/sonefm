"""레시피: 값 끼워 넣기, 분류(키워드·수동), 여러 단계 실행·실패 시 멈춤, 결과 버튼."""
import json

import pytest

from hub import registry
from hub.recipes import RecipeError, coerce_inputs, form, keyword_classifier, load_recipes, render, run_recipe

RECIPE = {
    "id": "two", "name": "두 단계", "keywords": ["유튜브"],
    "inputs": [{"name": "hour", "label": "시각", "type": "int", "from_text": r"(\d{1,2})\s*시"},
               {"name": "thumb", "label": "썸네일", "type": "file", "remember": True},
               {"name": "date", "label": "날짜", "type": "date", "default": "{task.date}"},
               {"name": "memo", "label": "메모", "optional": True}],
    "steps": [
        {"id": "first", "app": "a", "command": "lookup", "args": {"mode": "{input.mode}", "echo": "{input.hour}"}},
        {"id": "second", "app": "b", "command": "make-video",
         "args": {"mode": "ok", "echo": "{first.data.echo}회 · {task.text}", "n": "{first.data.echo}"}},
    ],
    "result": {"copy": [{"label": "제목 복사", "text": "{second.data.echo}"}],
               "open": [{"label": "열기", "path": "{second.outputs.0}"}, {"label": "없음", "path": "{second.data.none}"}],
               "links": [{"label": "스튜디오", "url": "https://studio.youtube.com"}]},
}


def test_값_끼워_넣기():
    ctx = {"input": {"hour": 14, "d": "2026-11-17"}, "info": {"data": {"label": "마을살이 43회"}, "outputs": ["a.mp4"]}}
    assert render("{input.hour}", ctx) == 14                       # 하나뿐이면 형식 그대로
    assert render("{info.data.label} ({input.d})", ctx) == "마을살이 43회 (2026-11-17)"
    assert render({"x": ["{info.outputs.0}"]}, ctx) == {"x": ["a.mp4"]}
    assert render("{input.none}", {"input": {"none": None}}) is None
    with pytest.raises(RecipeError, match="input.missing"):
        render("{input.missing}", ctx)


def test_키워드_분류와_수동():
    recipes = [RECIPE, {"id": "log", "keywords": ["방송일지"]}]
    assert keyword_classifier("유튜브 화 14시 마을살이", recipes) == "two"
    assert keyword_classifier("YouTube 정리", [{"id": "y", "keywords": ["youtube"]}]) == "y"
    assert keyword_classifier("협찬사 전화", recipes) is None


def test_폼_미리_채우기():
    fields = form(RECIPE, {"task": {"text": "유튜브 화 14시", "date": "2026-11-17"}}, {"thumb": "C:/t.png"})
    v = {f["name"]: f["value"] for f in fields}
    assert v == {"hour": "14", "thumb": "C:/t.png", "date": "2026-11-17", "memo": ""}


def test_입력_정리():
    assert coerce_inputs(RECIPE, {"hour": "14", "thumb": "t", "date": "d", "memo": ""})["hour"] == 14
    with pytest.raises(RecipeError, match="시각"):
        coerce_inputs(RECIPE, {"hour": "두시", "thumb": "t", "date": "d"})
    with pytest.raises(RecipeError, match="썸네일"):
        coerce_inputs(RECIPE, {"hour": "14", "thumb": " ", "date": "d"})


@pytest.fixture
def apps(fake_app):
    return {i: registry.load_app(fake_app(i)) for i in ("a", "b")}


def test_두_단계_앞_결과를_다음_입력으로(apps, tmp_path):
    events = []
    ctx = {"input": {"mode": "ok", "hour": 14}, "task": {"text": "유튜브 14시"}}
    r = run_recipe(RECIPE, ctx, apps, tmp_path / "run", on_event=events.append)
    assert r["status"] == "ok" and r["failed_step"] is None
    second = r["steps"][1]
    assert second["args"] == {"mode": "ok", "echo": "14회 · 유튜브 14시", "n": 14}
    assert second["result"]["data"]["input"]["n"] == 14             # 숫자 형식 그대로 앱까지
    assert r["actions"]["copy"] == [{"label": "제목 복사", "text": "14회 · 유튜브 14시"}]
    assert [a["label"] for a in r["actions"]["open"]] == ["열기"]   # 값 없는 버튼은 빠진다
    assert events[-1]["pct"] == 100 and events[0]["step"] == 1
    assert max(e["pct"] for e in events if e["step"] == 1) <= 50


def test_실패하면_그_단계에서_멈춤(apps, tmp_path):
    r = run_recipe(RECIPE, {"input": {"mode": "error", "hour": 1}, "task": {"text": ""}}, apps, tmp_path / "run")
    assert r["status"] == "error" and r["failed_step"] == 1 and len(r["steps"]) == 1
    assert r["message"].startswith("1단계(가짜 a)에서 실패")


def test_부분성공은_계속_가고_전체는_partial(apps, tmp_path):
    r = run_recipe(RECIPE, {"input": {"mode": "partial", "hour": 1}, "task": {"text": ""}}, apps, tmp_path / "run")
    assert r["status"] == "partial" and len(r["steps"]) == 2


def test_앱이_없거나_레시피_값이_없으면_실패(apps, tmp_path):
    r = run_recipe(RECIPE, {"input": {"mode": "ok", "hour": 1}, "task": {"text": ""}}, {"a": apps["a"]}, tmp_path / "r")
    assert r["failed_step"] == 2 and "찾지 못했습니다" in r["message"]
    r = run_recipe(RECIPE, {"input": {"hour": 1}, "task": {"text": ""}}, apps, tmp_path / "r2")
    assert r["failed_step"] == 1 and "input.mode" in r["message"]


def test_레시피_불러오기_사용자_폴더가_덮어씀(tmp_path):
    base, user = tmp_path / "base", tmp_path / "user"
    base.mkdir(), user.mkdir()
    (base / "a.json").write_text(json.dumps({**RECIPE, "name": "기본"}), encoding="utf-8")
    (user / "a.json").write_text(json.dumps({**RECIPE, "name": "사용자"}), encoding="utf-8")
    (user / "broken.json").write_text("{", encoding="utf-8")
    (user / "nosteps.json").write_text(json.dumps({"id": "x", "name": "x", "steps": []}), encoding="utf-8")
    recipes, problems = load_recipes([base, user])
    assert recipes["two"]["name"] == "사용자" and len(problems) == 2


def test_기본_레시피_파일이_올바름():
    from hub.paths import resource_dir
    recipes, problems = load_recipes([resource_dir() / "recipes"])
    assert not problems and "youtube-video" in recipes
    r = recipes["youtube-video"]
    assert [s["app"] for s in r["steps"]] == ["sonefm-schedule", "radio2yt"]
    assert keyword_classifier("유튜브 화 14시 마을살이", list(recipes.values())) == "youtube-video"
