"""업무 유형 사전(task_types.json) 읽기."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MISC = "misc_manual"


def default_path() -> Path:
    from hub.paths import resource_dir
    return resource_dir() / "task_types.json"


def load_task_types(path: Path | str | None = None) -> dict[str, Any]:
    """→ {"version", "types": {id: 유형}, "order": [id…], "value_key_defs", "default_words"}"""
    raw = json.loads(Path(path or default_path()).read_text(encoding="utf-8"))
    types = {t["id"]: t for t in raw["types"]}
    return {"version": raw.get("version", 1), "types": types, "order": [t["id"] for t in raw["types"]],
            "value_key_defs": raw.get("value_key_defs", {}), "default_words": raw.get("default_words", {})}
