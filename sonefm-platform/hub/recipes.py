"""레시피: 업무 유형 하나 = JSON 파일 하나. 단계마다 어떤 앱의 어떤 명령을 부를지 적는다.

새 업무 유형은 코드 수정 없이 레시피 파일만 추가한다 (PLATFORM_DESIGN.md 4절).
값 끼워 넣기: "{input.date}", "{info.data.label}", "{video.outputs.0}", "{today}", "{home}" ...
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from hub.registry import App
from hub.runner import run_command, worse

TOKEN = re.compile(r"\{([A-Za-z_]\w*(?:\.\w+)*)\}")

# 분류기 자리: (업무 글자, 레시피 목록) → 레시피 id 또는 None. 나중에 LLM 분류기로 바꿔 끼울 수 있다.
Classifier = Callable[[str, list[dict[str, Any]]], "str | None"]
EventFn = Callable[[dict[str, Any]], None]


class RecipeError(Exception):
    pass


def load_recipes(dirs: list[Path]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """→ (id → 레시피, 문제 목록). 뒤 폴더가 같은 id를 덮는다. 같은 폴더 안은 파일 이름 순."""
    recipes: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    for di, d in enumerate(dirs):
        for f in sorted(Path(d).glob("*.json")) if Path(d).is_dir() else []:
            try:
                r = json.loads(f.read_text(encoding="utf-8"))
                if not r.get("id") or not r.get("name") or not isinstance(r.get("steps"), list) or not r["steps"]:
                    raise ValueError("id, name, steps가 필요합니다")
                for s in r["steps"]:
                    if not all(s.get(k) for k in ("id", "app", "command")):
                        raise ValueError("단계마다 id, app, command가 필요합니다")
            except (OSError, ValueError) as e:
                problems.append(f"레시피 {f.name}을(를) 읽지 못했습니다: {e}")
                continue
            r["_file"] = str(f)
            r["_dir_index"] = di
            if not isinstance(r.get("task_types"), list):    # 플래너 연결 필드(선택): 모양이 다르면 무시
                r.pop("task_types", None)
            recipes[r["id"]] = r
    return recipes, problems


def keyword_classifier(text: str, recipes: list[dict[str, Any]]) -> str | None:
    low = text.lower()
    for r in recipes:
        if any(k and k.lower() in low for k in r.get("keywords", [])):
            return r["id"]
    return None


def _lookup(path: str, ctx: dict[str, Any]) -> Any:
    cur: Any = ctx
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            raise RecipeError(f"레시피 값 '{{{path}}}'을(를) 찾지 못했습니다.")
    return cur


def render(value: Any, ctx: dict[str, Any]) -> Any:
    """문자열 전체가 {...} 하나면 원래 형식(숫자 등) 그대로, 아니면 글자로 끼워 넣는다."""
    if isinstance(value, dict):
        return {k: render(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, ctx) for v in value]
    if not isinstance(value, str):
        return value
    whole = TOKEN.fullmatch(value)
    if whole:
        return _lookup(whole.group(1), ctx)
    return TOKEN.sub(lambda m: "" if (v := _lookup(m.group(1), ctx)) is None else str(v), value)


def form(recipe: dict[str, Any], ctx: dict[str, Any], remembered: dict[str, Any]) -> list[dict[str, Any]]:
    """실행 전 입력 폼: default / from_text(업무 글자에서 정규식) / remember(지난번 값)로 미리 채운다."""
    fields = []
    planned = ctx.get("values") or {}      # 플래너가 뽑은 값(선택). from_value가 가장 먼저
    for spec in recipe.get("inputs", []):
        value: Any = ""
        if spec.get("from_value") and planned.get(spec["from_value"]) not in (None, "", []):
            value = planned[spec["from_value"]]
        elif spec.get("remember") and remembered.get(spec["name"]):
            value = remembered[spec["name"]]
        elif spec.get("from_text"):
            m = re.search(spec["from_text"], (ctx.get("task") or {}).get("text", ""))
            value = m.group(1) if m else ""
        if value in ("", None) and "default" in spec:
            try:
                value = render(spec["default"], ctx)
            except RecipeError:
                value = ""
        fields.append({**spec, "value": "" if value is None else value})
    return fields


def coerce_inputs(recipe: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    """폼 값 정리. 필수(optional이 아닌) 값이 비면 RecipeError."""
    out: dict[str, Any] = {}
    for spec in recipe.get("inputs", []):
        v = raw.get(spec["name"], "")
        v = v.strip() if isinstance(v, str) else v
        if v in ("", None):
            if not spec.get("optional"):
                raise RecipeError(f"'{spec.get('label', spec['name'])}'을(를) 입력하세요.")
            out[spec["name"]] = ""
            continue
        if spec.get("type") == "int":
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise RecipeError(f"'{spec.get('label', spec['name'])}'은(는) 숫자로 입력하세요.") from None
        out[spec["name"]] = v
    return out


def recipes_by_type(recipes: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """업무 유형 id → 그 유형을 맡는 레시피(`task_types`). 한 유형을 둘이 선언하면 나중 폴더(사용자 폴더)가 우선,
    같은 폴더면 파일 이름 순 첫 레시피."""
    out: dict[str, dict[str, Any]] = {}
    for r in sorted(recipes.values(), key=lambda r: (-r.get("_dir_index", 0), r.get("_file", ""), r["id"])):
        for t in r.get("task_types") or []:
            out.setdefault(t, r)
    return out


def foreach_items(recipe: dict[str, Any], values: dict[str, Any]) -> list[dict[str, Any]]:
    """`foreach` 값 목록 → 실행마다의 값 묶음. 없거나 목록이 비면 [{}](실행 하나).

    `episodes`는 programs[].episodes를 {program, episode}로 펼친다."""
    key = recipe.get("foreach")
    if not key:
        return [{}]
    seq = values.get(key)
    if not seq and key == "episodes":
        seq = [{"program": p.get("name"), "episode": e} for p in values.get("programs", []) or []
               for e in p.get("episodes", [])]
    if not isinstance(seq, list) or not seq:
        return [{}]
    return [x if isinstance(x, dict) else {"item": x} for x in seq]


def plan_runs(recipe: dict[str, Any], values: dict[str, Any], task: dict[str, Any]) -> list[dict[str, Any]]:
    """플래너용: 실행마다 입력을 채운다. 빠진 값은 missing·questions. from_value → from_text → default 순."""
    runs = []
    for item in foreach_items(recipe, values):
        rv = {**values, **item}
        inputs: dict[str, Any] = {}
        missing: list[str] = []
        questions: list[str] = []
        for spec in recipe.get("inputs", []):
            val: Any = None
            fv = spec.get("from_value")
            if fv and rv.get(fv) not in (None, "", []):
                val = rv[fv]
            if val in (None, "") and spec.get("from_text"):
                m = re.search(spec["from_text"], task.get("text", ""))
                val = m.group(1) if m else None
            if val in (None, "") and "default" in spec:
                try:
                    val = render(spec["default"], {"task": task, "today": task.get("date", "")})
                except RecipeError:
                    val = None
            if val not in (None, "") and spec.get("type") == "int":
                try:
                    val = int(val)
                except (TypeError, ValueError):
                    val = None
            if val in (None, ""):
                if not spec.get("optional"):
                    missing.append(spec["name"])
                    questions.append(f"'{spec.get('label', spec['name'])}'을(를) 정해 주세요.")
            else:
                inputs[spec["name"]] = val
        runs.append({"inputs": inputs, "missing": missing, "questions": questions})
    return runs


def run_recipe(recipe: dict[str, Any], ctx: dict[str, Any], apps: dict[str, App], workdir: Path,
               run: Callable[..., dict[str, Any]] = run_command, on_event: EventFn | None = None) -> dict[str, Any]:
    """단계를 순서대로 실행. error면 멈춘다. ctx에는 input, task, today, home, schedule_db 등."""
    ctx = dict(ctx)
    steps = recipe["steps"]
    done: list[dict[str, Any]] = []
    status = "ok"

    def event(i: int, pct: int, message: str) -> None:
        if on_event:
            overall = int(100 * (i + pct / 100) / len(steps))
            on_event({"step": i + 1, "steps": len(steps), "pct": overall, "message": message})

    for i, step in enumerate(steps):
        app = apps.get(step["app"])
        label = f"{i + 1}단계({app.name if app else step['app']})"
        event(i, 0, f"{label} 시작")
        args: dict[str, Any] = {}
        try:
            args = render(step.get("args", {}), ctx)
        except RecipeError as e:
            result = {"status": "error", "count": 0, "data": {}, "outputs": [], "skipped": [], "message": str(e)}
        else:
            if app is None:
                result = {"status": "error", "count": 0, "data": {}, "outputs": [], "skipped": [],
                          "message": f"'{step['app']}' 앱을 찾지 못했습니다. 설치했는지, 설정의 앱 폴더를 확인하세요."}
            else:
                result = run(app, step["command"], args, workdir / f"{i + 1}-{step['id']}",
                             on_progress=lambda pct, msg, i=i, label=label: event(i, pct, f"{label} {msg}".strip()))
        done.append({"id": step["id"], "app": step["app"], "app_name": app.name if app else step["app"],
                     "command": step["command"], "args": args, "result": result})
        ctx[step["id"]] = result
        status = worse(status, result["status"])
        if result["status"] == "error":
            return {"status": "error", "steps": done, "failed_step": i + 1, "actions": {},
                    "message": f"{label}에서 실패: {result['message']}"}
        event(i, 100, f"{label} 끝")

    actions = _actions(recipe.get("result") or {}, ctx)
    last = done[-1]["result"]["message"]
    skipped = [s for d in done for s in d["result"]["skipped"]]
    message = last + (f" · 건너뜀 {len(skipped)}건" if skipped and status == "partial" else "")
    return {"status": status, "steps": done, "failed_step": None, "actions": actions, "message": message}


def _actions(spec: dict[str, Any], ctx: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """결과 버튼(open/copy/links). 값이 없는 버튼은 빼고 보여준다."""
    out: dict[str, list[dict[str, Any]]] = {}
    for kind in ("open", "copy", "links"):
        items = []
        for item in spec.get(kind, []):
            try:
                rendered = render(item, ctx)
            except RecipeError:
                continue
            if all(v not in ("", None) for v in rendered.values()):
                items.append(rendered)
        out[kind] = items
    return out
