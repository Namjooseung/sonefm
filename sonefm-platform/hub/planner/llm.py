"""⑥ LLM 분류 (AI_DESIGN 7.1 ⑥). 애매·미분류·문장 종류가 애매한 업무 묶음을 게이트웨이로 보내 유형·문장 종류·값을 받는다.

plan_cells(llm=LLMClassifier(...)) 로 꽂는다. 결과 합치기(유형 검증, 값 형식 검사, 쪼개기, 표시)는 plan.apply_llm_result가 한다.
호출 오류(AIError)가 나면 그 묶음은 키워드 결과로 두고 failed_ids에 담는다(plan_cells가 llm_failed 표시).
묶음에는 가린 글자만 들어간다(G1 계정 칸 제외·G2~G6는 build_llm_bundle이 이미 처리).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable

from hub.ai.gateway import AIError
from hub.planner.kind import DONE, INSTRUCT, PROGRESS

log = logging.getLogger("hub.planner")

BATCH_SIZE = 12
FEATURE_USE, FEATURE_EVAL = "planner.classify", "planner.eval"
KINDS = (INSTRUCT, PROGRESS, DONE)
OUT_TOKENS_PER_UNIT = 110        # 비용 추정용 대략값: 업무 하나에 대한 JSON 출력 토큰
OUT_CHARS_PER_UNIT = 220         # 같은 값을 글자 수로
_STOP_KINDS = ("off", "budget", "auth")   # 이 오류는 다음 묶음도 똑같이 실패하므로 남은 호출을 하지 않는다


def build_system(ref_types: dict[str, Any]) -> str:
    """매번 같은 글(캐시 대상). 유형 사전 요약 + 값 키 정의 + 규칙."""
    lines = [
        "당신은 공동체라디오 방송국 업무일지의 한 줄(업무)을 정해진 업무 유형으로 분류하는 도우미입니다.",
        "아래 '업무' 목록의 각 항목에 대해 유형(type), 문장 종류(sentence_kind), 글에서 읽은 값(values)을 JSON으로만 답합니다.",
        "", "## 규칙",
        "- 기준일은 각 업무의 date(업무일지 날짜)입니다. 글에 연도가 없으면 기준일의 연도로 채우고, "
        "'지난달'·'이번 주' 같은 말도 기준일에서 계산합니다.",
        f"- sentence_kind는 '{INSTRUCT}'(해 달라는 일), '{PROGRESS}'(진행 중이라는 알림), '{DONE}'(이미 했다는 알림) 중 하나입니다. "
        "명사로 끝나는 글은 지시입니다.",
        "- 한 업무에 서로 다른 일이 둘 이상 들어 있으면 tasks에 여러 개로 나눠 답합니다.",
        "- values에는 해당 유형의 '값 키'만 쓰고, 글에서 확실히 읽을 수 있는 값만 넣습니다. 짐작하지 않습니다. "
        "형식은 값 키 정의를 따릅니다(날짜 YYYY-MM-DD, 월 YYYY-MM, 시각 HH:MM, 회차는 정수).",
        "- keyword_top은 참고용 후보일 뿐입니다. 글의 뜻이 다르면 따르지 않아도 됩니다.",
        "- 어느 유형인지 모르면 misc_manual로 답합니다.",
        "- 답은 JSON 하나뿐입니다: {\"units\":[{\"unit_id\":..., \"tasks\":[{\"type\":..., \"sentence_kind\":..., "
        "\"values\":{...}, \"confidence\":0~1}]}]}. 받은 unit_id를 모두 한 번씩 답합니다.",
        "", "## 값 키 정의"]
    for k, d in ref_types.get("value_key_defs", {}).items():
        lines.append(f"- {k}: {d}")
    lines += ["", "## 업무 유형 (id | 이름 | 설명 | 값 키 | 예문)"]
    for tid in ref_types["order"]:
        t = ref_types["types"][tid]
        ex = " / ".join(str(e)[:80] for e in (t.get("examples") or [])[:2])
        lines.append(f"- {tid} | {t.get('name', '')} | {t.get('description', '')} | "
                     f"{','.join(t.get('value_keys', []))} | {ex}")
    return "\n".join(lines)


def build_schema(ref_types: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "required": ["units"], "properties": {"units": {"type": "array", "items": {
        "type": "object", "required": ["unit_id", "tasks"], "properties": {
            "unit_id": {"type": "string"},
            "tasks": {"type": "array", "items": {
                "type": "object", "required": ["type", "sentence_kind"], "properties": {
                    "type": {"type": "string", "enum": list(ref_types["order"])},
                    "sentence_kind": {"type": "string", "enum": list(KINDS)},
                    "values": {"type": "object"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1}}}}}}}}}


def build_messages(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """묶음 항목(build_llm_bundle의 items) → 사용자 메시지 한 개."""
    units = [{"unit_id": it["id"], "date": it.get("date"), "dept": it.get("dept", ""), "text": it["text"],
              "keyword_top": [k for k in (it["keyword"]["top"], it["keyword"]["second"]) if k]} for it in items]
    return [{"role": "user", "content": "다음 업무를 분류해 주세요.\n" + json.dumps({"units": units}, ensure_ascii=False)}]


def chunks(items: list[Any], n: int) -> list[list[Any]]:
    n = max(1, n)
    return [items[i:i + n] for i in range(0, len(items), n)]


class LLMClassifier:
    """plan_cells의 llm 자리에 꽂는 호출 가능 객체. 호출마다 기록(calls_log)을 쌓아 평가 실행기가 쓴다."""

    def __init__(self, ref_types: dict[str, Any], *, tier: str = "light", feature: str = FEATURE_USE,
                 batch_size: int = BATCH_SIZE, complete: Callable[..., Any] | None = None):
        self.ref_types, self.tier, self.feature, self.batch_size = ref_types, tier, feature, batch_size
        self._complete = complete
        self.system = build_system(ref_types)
        self.schema = build_schema(ref_types)
        self.calls_log: list[dict[str, Any]] = []     # {"ok","units","cost_usd","duration_ms","cache_read_tokens",...,"error"}
        self.model: str | None = None

    def __call__(self, bundle: dict[str, Any]) -> dict[str, Any]:
        complete = self._complete
        if complete is None:
            from hub.ai import gateway
            complete = gateway.complete
        items_out: list[dict[str, Any]] = []
        failed: list[str] = []
        calls, cost, stop = 0, 0.0, False
        for batch in chunks(bundle["items"], self.batch_size):
            ids = [it["id"] for it in batch]
            if stop:
                failed += ids
                continue
            calls += 1
            try:
                r = complete(self.feature, self.tier, build_messages(batch), system=self.system, schema=self.schema,
                             max_tokens=4096 + 300 * len(batch))
            except AIError as e:
                log.warning("플래너 LLM 분류 실패(%s): 묶음 %d건은 키워드 결과로 둡니다", e.kind, len(batch))
                self.calls_log.append({"ok": False, "units": len(batch), "error": e.kind, "cost_usd": 0.0,
                                       "duration_ms": None, "cache_read_tokens": 0})
                failed += ids
                stop = e.kind in _STOP_KINDS
                continue
            cost += r.cost_usd
            self.model = r.model
            self.calls_log.append({"ok": True, "units": len(batch), "error": None, "cost_usd": r.cost_usd,
                                   "duration_ms": r.duration_ms, "cache_read_tokens": r.cache_read_tokens,
                                   "input_tokens": r.input_tokens, "output_tokens": r.output_tokens})
            answered = set()
            for u in (r.data or {}).get("units", []):
                if u.get("unit_id") in ids and u["unit_id"] not in answered:
                    answered.add(u["unit_id"])
                    items_out.append({"id": u["unit_id"], "tasks": u.get("tasks", [])})
            failed += [i for i in ids if i not in answered]
        return {"items": items_out, "failed_ids": failed,
                "stats": {"calls": calls, "tier": self.tier, "model": self.model, "cost_usd": cost}}
