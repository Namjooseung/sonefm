"""플래너 본체: 칸 → 업무 단위 → 계획 JSON (AI_DESIGN 7.1~7.2). LLM은 `llm` 자리에 함수를 꽂으면 된다.

llm(bundle) -> {"items": [{"id", "tasks": [{"type", "sentence_kind", "values"}]}]}  (이번에는 구현하지 않음)
llm이 None이면: 애매 = 1위 유형, 미분류 = misc_manual, 애매한 문장 종류 = 규칙 결과.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Callable

from hub import recipes as R
from hub.planner import classify as C
from hub.planner import values as V
from hub.planner.kind import INSTRUCT, sentence_kind
from hub.planner.masking import is_credential_cell, mask_names, mask_text
from hub.planner.split import split_by_pieces, split_structure
from hub.worklog import task_key

RULES_VERSION = "planner-rules-1"
LLM_FN = Callable[[dict[str, Any]], dict[str, Any]]

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_YM = re.compile(r"^\d{4}-\d{2}$")
_HM = re.compile(r"^\d{2}:\d{2}$")


def _units_of(cell: dict[str, Any], idx: C.KeywordIndex) -> list[dict[str, Any]]:
    """한 칸 → 업무 단위 기록들(가린 글자, 문장 종류, 키워드 결과)."""
    raw = cell.get("text") or ""
    ref = date.fromisoformat(cell["date"][:10])
    key = cell.get("key") or task_key(ref, raw)
    cred = is_credential_cell(raw)
    units = []
    for u in split_structure(mask_text(raw)):
        units.extend(split_by_pieces(u, idx.confirmed_type))
    recs = []
    for n, u in enumerate(units, 1):
        kind, unsure = sentence_kind(u.text)
        recs.append({"id": f"{key}#{n}", "cell_key": key, "dept": cell.get("dept") or "", "text": u.text,
                     "steps": u.steps, "split_by": u.split_by, "ref": ref, "credential": cred,
                     "sentence_kind": kind, "kind_unsure": unsure, "keyword": idx.classify(u.text),
                     "type": None, "classified_by": "keyword", "llm_type": None, "llm_values": {},
                     "flags": ["credential_cell"] if cred else [], "confidence": None})
    return recs


def pending(rec: dict[str, Any]) -> bool:
    """⑥ LLM으로 보낼 대상: 애매·미분류이거나 문장 종류가 애매한 업무. 계정 칸(G1)은 절대 안 보낸다."""
    return not rec["credential"] and (rec["keyword"]["state"] != C.CONFIRMED or rec["kind_unsure"])


def build_llm_bundle(recs: list[dict[str, Any]], names: list[str] | None = None, all_units: bool = False) -> dict:
    """LLM에 넘길 묶음. 가린 글자(G2~G4)와 근무자 이름(G6)만 담고, G1 칸·비고(G5)는 넣지 않는다."""
    items = []
    for r in recs:
        if r["credential"] or not (all_units or pending(r)):
            continue
        items.append({"id": r["id"], "dept": r["dept"], "text": mask_names(r["text"], names),
                      "sentence_kind_unsure": r["kind_unsure"],
                      "keyword": {"top": r["keyword"]["top"], "second": r["keyword"]["second"]}})
    return {"items": items}


def _valid_value(key: str, val: Any, programs: list[str]) -> Any:
    """LLM 값 다시 검사. 통과한 값(정리한 것)을 돌려주고, 못 쓰면 None."""
    def iso(s: Any) -> bool:
        try:
            return isinstance(s, str) and bool(_ISO.match(s)) and bool(date.fromisoformat(s))
        except ValueError:
            return False
    if key in ("date", "deadline", "base_date"):
        return val if iso(val) else None
    if key in ("month", "done_month"):
        return val if isinstance(val, str) and _YM.match(val) and 1 <= int(val[5:7]) <= 12 else None
    if key == "months":
        ok = isinstance(val, list) and val and all(isinstance(x, str) and _YM.match(x) for x in val)
        return val if ok else None
    if key == "time":
        return val if isinstance(val, str) and _HM.match(val) and int(val[:2]) <= 24 and int(val[3:]) < 60 else None
    if key in ("programs", "not_selected"):
        if not isinstance(val, list):
            return None
        names = {V.norm_name(p): p for p in programs}
        out = []
        for e in val:
            if not isinstance(e, dict) or not all(isinstance(n, int) and not isinstance(n, bool) for n in e.get("episodes", [])):
                return None
            nm = e.get("name")
            if nm is not None and V.norm_name(nm) not in names:
                continue
            out.append({"name": names[V.norm_name(nm)], "episodes": e.get("episodes", [])} if nm is not None
                       else {"episodes": e.get("episodes", [])})
        return out or None
    if key == "weekdays":
        return val if isinstance(val, list) and all(x in "월화수목금토일" for x in val) else None
    return val if isinstance(val, (str, int)) and not isinstance(val, bool) and str(val).strip() else None


def apply_llm_result(recs: list[dict[str, Any]], result: dict[str, Any], ref_types: dict[str, Any],
                     programs: list[str] | None = None) -> list[dict[str, Any]]:
    """⑥ 결과 합치기. 모르는 유형·id는 버린다. 업무가 여러 개로 오면 쪼갠다(split_by_llm). → 새 기록 목록."""
    by_id = {r["id"]: r for r in recs}
    out: list[dict[str, Any]] = []
    for r in recs:
        out.append(r)
        entry = next((e for e in (result or {}).get("items", []) if e.get("id") == r["id"]), None)
        if not entry:
            continue
        tasks = [t for t in entry.get("tasks", []) if t.get("type") in ref_types["types"]]
        if not tasks or r["id"] not in by_id:
            continue
        for k, t in enumerate(tasks):
            tgt = r
            if k > 0:
                tgt = {**r, "id": f"{r['id']}.{k + 1}", "flags": [f for f in r["flags"]], "steps": [], "llm_values": {}}
                tgt["flags"].append("split_by_llm")
                out.append(tgt)
            vkeys = ref_types["types"][t["type"]]["value_keys"]
            tgt["llm_type"] = t["type"]
            tgt["classified_by"] = "llm"
            tgt["type"] = t["type"]
            tgt["confidence"] = t.get("confidence", 0.7)
            if tgt["keyword"]["top"] and tgt["keyword"]["top"] != t["type"]:
                tgt["flags"].append("keyword_llm_mismatch")
            if t.get("sentence_kind") in (INSTRUCT, "진행 보고", "완료 보고") and tgt["kind_unsure"]:
                tgt["sentence_kind"] = t["sentence_kind"]
                tgt["kind_unsure"] = False
            for key, val in (t.get("values") or {}).items():
                if key in vkeys:
                    ok = _valid_value(key, val, programs or [])
                    if ok is not None:
                        tgt["llm_values"][key] = ok
    return out


def _title(type_def: dict[str, Any], values: dict[str, Any]) -> str:
    for k in ("month", "months", "date", "period", "deadline"):
        if values.get(k):
            v = values[k]
            return f"{type_def['name']} · {', '.join(v) if isinstance(v, list) else v}"
    return type_def["name"]


def _item(rec: dict[str, Any], ref_types: dict[str, Any], by_type: dict[str, dict], programs: list[str]) -> dict:
    type_def = ref_types["types"][rec["type"]]
    vkeys = type_def.get("value_keys", [])
    values, sources = V.extract_values(rec["text"], vkeys, rec["ref"], programs)
    flags = list(rec["flags"])
    for k, v in rec["llm_values"].items():
        if k in values:
            if values[k] != v and "value_mismatch" not in flags:
                flags.append("value_mismatch")
        else:
            values[k], sources[k] = v, "llm"
    V.apply_defaults(values, sources, type_def.get("defaults", {}), vkeys, rec["ref"])
    kw = rec["keyword"]
    if kw["state"] == C.AMBIGUOUS and rec["classified_by"] == "keyword" and "low_confidence" not in flags:
        flags.append("low_confidence")
    conf = rec["confidence"]
    if conf is None:
        conf = {C.CONFIRMED: 0.9, C.AMBIGUOUS: 0.5, C.UNCLASSIFIED: 0.2}[kw["state"]]
    item: dict[str, Any] = {
        "id": rec["id"], "cell_key": rec["cell_key"], "dept": rec["dept"], "text": rec["text"],
        "sentence_kind": rec["sentence_kind"], "type": rec["type"], "type_name": type_def["name"],
        "classified_by": rec["classified_by"], "keyword": kw, "llm_type": rec["llm_type"], "confidence": conf,
        "values": values, "value_sources": sources, "mode": "checklist", "recipe": None, "checklist": None,
        "automation_hint": None, "depends_on": [], "flags": flags, "status": "draft",
    }
    if rec["sentence_kind"] != INSTRUCT:
        item["mode"] = "excluded"
        return item
    recipe = by_type.get(rec["type"])
    if recipe:
        item["mode"] = "recipe"
        item["recipe"] = {"id": recipe["id"],
                          "runs": R.plan_runs(recipe, values, {"date": rec["ref"].isoformat(), "text": rec["text"]})}
        return item
    item["checklist"] = {"title": _title(type_def, values),
                         "steps": [{"text": s, "done": False} for s in rec["steps"]]}
    auto = type_def.get("automation") or {}
    if auto.get("status") == "buildable":
        item["automation_hint"] = f"자동화 예정: {auto.get('needs', '')}".rstrip(": ")
    if rec["credential"] and rec["keyword"]["state"] != C.CONFIRMED and rec["classified_by"] == "keyword":
        item["note"] = "계정 정보가 있는 칸이라 사람이 분류해 주세요."
    return item


def analyze_cells(cells: list[dict[str, Any]], ref_types: dict[str, Any], idx: C.KeywordIndex | None = None
                  ) -> list[dict[str, Any]]:
    """①~⑤: 칸 → 업무 단위 기록 (LLM 전, 유형 미정)."""
    idx = idx or C.KeywordIndex(ref_types)
    recs: list[dict[str, Any]] = []
    for cell in cells:
        recs.extend(_units_of(cell, idx))
    return recs


def plan_cells(cells: list[dict[str, Any]], ref_types: dict[str, Any], recipes: dict[str, dict[str, Any]],
               programs: list[str] | None = None, llm: LLM_FN | None = None, *, names: list[str] | None = None,
               now: datetime | None = None, source: dict[str, Any] | None = None) -> dict[str, Any]:
    """cells = [{date, date_end, dept, text, note}] → 계획 dict(AI_DESIGN 7.2). 비고(note)는 쓰지 않는다(G5)."""
    programs = list(programs or [])
    recs = analyze_cells(cells, ref_types)
    referred = [r for r in recs if pending(r)]
    llm_info = {"used": False, "tier": None, "model": None, "calls": 0, "cost_usd": 0.0}
    if llm is not None and referred:
        result = llm(build_llm_bundle(recs, names))
        recs = apply_llm_result(recs, result, ref_types, programs)
        llm_info["used"], llm_info["calls"] = True, 1
    for r in recs:
        if r["type"] is None:
            r["type"] = C.decide(r["keyword"])
    by_type = R.recipes_by_type(recipes or {})
    items, reports = [], []
    for r in recs:
        it = _item(r, ref_types, by_type, programs)
        if it["mode"] == "excluded":
            reports.append({"cell_key": it["cell_key"], "sentence_kind": it["sentence_kind"], "type": it["type"],
                            "text": it["text"], "values": it["values"], "value_sources": it["value_sources"]})
        else:
            items.append(it)
    dates = sorted(c["date"][:10] for c in cells) or [""]
    ends = sorted((c.get("date_end") or c["date"])[:10] for c in cells) or [""]
    src = {"worklog": None, "date": dates[0], "date_end": ends[-1], "sheet": (cells[0].get("sheet") if cells else None)}
    src.update(source or {})
    return {
        "plan_version": 1, "source": src,
        "generated_at": (now or datetime.now().astimezone()).isoformat(timespec="seconds"),
        "engine": {"task_types_version": ref_types.get("version", 1), "rules_version": RULES_VERSION, "llm": llm_info,
                   "units": len(recs), "referred": len(referred)},
        "items": items, "reports": reports,
    }
