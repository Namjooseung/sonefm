"""업무 유형 사전(task_types.json) 읽기."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MISC = "misc_manual"


def default_path() -> Path:
    from hub.paths import resource_dir
    return resource_dir() / "task_types.json"


def _check_type(t: dict[str, Any]) -> None:
    """선택 필드 required·recipe_blockers의 모양을 검사한다(AI_DESIGN 7.4). 틀리면 ValueError."""
    tid = t.get("id")
    req = t.get("required")
    if req is not None:
        ok = isinstance(req, dict) and all(isinstance(k, str) and isinstance(v, str) and v.strip() for k, v in req.items())
        if not ok:
            raise ValueError(f"{tid}: required는 {{값 키: 질문 글자}}여야 합니다")
        unknown = [k for k in req if k not in t.get("value_keys", [])]
        if unknown:
            raise ValueError(f"{tid}: required 키가 value_keys에 없습니다: {unknown}")
    rb = t.get("recipe_blockers")
    if rb is not None:
        ok = (isinstance(rb, dict) and isinstance(rb.get("keywords"), list) and rb["keywords"]
              and all(isinstance(k, str) and k.strip() for k in rb["keywords"])
              and isinstance(rb.get("question_key"), str) and rb["question_key"].strip()
              and isinstance(rb.get("question"), str) and rb["question"].strip())
        if not ok:
            raise ValueError(f"{tid}: recipe_blockers는 {{keywords: [글자…], question_key, question}}여야 합니다")


def load_task_types(path: Path | str | None = None) -> dict[str, Any]:
    """→ {"version", "types": {id: 유형}, "order": [id…], "value_key_defs", "default_words"}"""
    raw = json.loads(Path(path or default_path()).read_text(encoding="utf-8"))
    for t in raw["types"]:
        _check_type(t)
    types = {t["id"]: t for t in raw["types"]}
    return {"version": raw.get("version", 1), "types": types, "order": [t["id"] for t in raw["types"]],
            "value_key_defs": raw.get("value_key_defs", {}), "default_words": raw.get("default_words", {})}
