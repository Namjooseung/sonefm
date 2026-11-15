"""JSON 스키마 부분 집합 검증 (의존성 없음). 모르는 키워드는 ValueError."""
from __future__ import annotations

import json
import re
from typing import Any

_TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}
_KEYWORDS = {"type", "properties", "required", "additionalProperties", "enum", "items", "minimum", "maximum",
             "description", "title"}   # description·title 은 설명일 뿐이라 허용


def check_schema(schema: Any, path: str = "$") -> None:
    if not isinstance(schema, dict):
        raise ValueError(f"스키마는 객체여야 합니다: {path}")
    for k in schema:
        if k not in _KEYWORDS:
            raise ValueError(f"지원하지 않는 스키마 키워드: {k} ({path})")
    if "type" in schema and schema["type"] not in _TYPES:
        raise ValueError(f"지원하지 않는 type: {schema['type']!r} ({path})")
    if "additionalProperties" in schema and not isinstance(schema["additionalProperties"], bool):
        raise ValueError(f"additionalProperties는 true/false만 지원합니다 ({path})")
    for name, sub in schema.get("properties", {}).items():
        check_schema(sub, f"{path}.{name}")
    if "items" in schema:
        check_schema(schema["items"], f"{path}[]")


def _is(value: Any, t: str) -> bool:
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}[t])


def validate(value: Any, schema: dict, path: str = "$") -> list[str]:
    """어긋난 곳을 문장 목록으로 돌려준다 (비면 통과)."""
    errs: list[str] = []
    t = schema.get("type")
    if t and not _is(value, t):
        return [f"{path}: {t} 형식이어야 합니다"]
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{path}: {schema['enum']} 중 하나여야 합니다")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errs.append(f"{path}: {schema['minimum']} 이상이어야 합니다")
        if "maximum" in schema and value > schema["maximum"]:
            errs.append(f"{path}: {schema['maximum']} 이하여야 합니다")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        errs += [f"{path}.{r}: 꼭 필요합니다" for r in schema.get("required", []) if r not in value]
        for k, v in value.items():
            if k in props:
                errs += validate(v, props[k], f"{path}.{k}")
            elif schema.get("additionalProperties") is False:
                errs.append(f"{path}.{k}: 허용되지 않는 항목입니다")
    if isinstance(value, list) and "items" in schema:
        for i, v in enumerate(value):
            errs += validate(v, schema["items"], f"{path}[{i}]")
    return errs


def parse_json(text: str) -> Any:
    """```json 코드 블록으로 감싸여 와도 벗겨서 파싱한다. 실패하면 ValueError."""
    s = text.strip()
    m = re.match(r"^```[A-Za-z]*\s*\n?(.*?)\n?```$", s, re.DOTALL)
    return json.loads(m.group(1) if m else s)
