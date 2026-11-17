"""⑤ 키워드 분류: 유형마다 걸린 키워드의 글자 수 합이 점수.

확정 = 1위 점수 >= CONFIRM_MIN 이고 2위와의 차이 >= CONFIRM_MARGIN. 그렇지 않으면 애매(점수는 있는데 못 정함),
하나도 안 걸리면 미분류. 둘 다 ⑥ LLM으로 간다(LLM이 없으면 애매 = 1위 유형, 미분류 = misc_manual).
기준값은 평가 세트(eval_set)로 정했다: tools/planner_eval.py.
"""
from __future__ import annotations

import re
from typing import Any

from hub.planner.types import MISC

CONFIRM_MIN = 3
CONFIRM_MARGIN = 3
MAX_REPEAT = 2

CONFIRMED, AMBIGUOUS, UNCLASSIFIED = "confirmed", "ambiguous", "unclassified"


def _norm(s: str) -> str:
    # '드라이브'가 '라이브'로 잡히지 않게 같은 길이의 다른 글자로 바꿔 둔다(키워드에도 똑같이 적용)
    return re.sub(r"\s+", "", s or "").lower().replace("드라이브", "드랍이브")


class KeywordIndex:
    """유형 사전에서 (유형, 정규화 키워드) 목록을 한 번 만들어 둔다."""

    def __init__(self, ref_types: dict[str, Any]):
        self.entries: list[tuple[str, list[str]]] = []
        for tid in ref_types["order"]:
            kws = sorted({_norm(k) for k in ref_types["types"][tid].get("keywords", []) if k and _norm(k)},
                         key=len, reverse=True)
            if kws:
                self.entries.append((tid, kws))

    def scores(self, text: str) -> dict[str, int]:
        t = _norm(text)
        found: dict[str, list[str]] = {}
        for tid, kws in self.entries:
            hit: list[str] = []
            for k in kws:
                if k in t and not any(k in h for h in hit):   # 더 긴 키워드에 포함된 짧은 키워드는 세지 않는다
                    hit.append(k)
            if hit:
                found[tid] = hit
        every = {k for hit in found.values() for k in hit}
        out: dict[str, int] = {}
        for tid, hit in found.items():
            # 다른 유형의 더 긴 키워드(더 구체적)에 포함되는 짧은 키워드는 세지 않는다. 두 번 나오면 두 번(최대 2)
            own = [k for k in hit if not any(len(o) > len(k) and k in o for o in every)]
            if own:
                out[tid] = sum(len(k) * min(t.count(k), MAX_REPEAT) for k in own)
        return out

    def classify(self, text: str) -> dict[str, Any]:
        """제목(머리 줄)만으로 확정되면 그것을 쓰고, 아니면 하위 단계까지 포함한 전체로 점수를 낸다."""
        head = (text or "").split("\n", 1)[0]
        if "\n" in (text or ""):
            r = self._classify(head)
            if r["state"] == CONFIRMED:
                return r
        return self._classify(text)

    def _classify(self, text: str) -> dict[str, Any]:
        sc = self.scores(text)
        ranked = sorted(sc.items(), key=lambda kv: -kv[1])   # 같은 점수면 사전 순서(빈도 순) 유지
        top, ts = ranked[0] if ranked else (None, 0)
        second, ss = ranked[1] if len(ranked) > 1 else (None, 0)
        if top is None:
            state = UNCLASSIFIED
        elif ts >= CONFIRM_MIN and ts - ss >= CONFIRM_MARGIN:
            state = CONFIRMED
        else:
            state = AMBIGUOUS
        return {"top": top, "score": ts, "second": second, "second_score": ss, "state": state}

    def confirmed_type(self, text: str) -> str | None:
        r = self._classify(text)
        return r["top"] if r["state"] == CONFIRMED else None


def decide(kw: dict[str, Any]) -> str:
    """LLM이 없을 때의 유형: 확정·애매 = 1위, 미분류 = misc_manual."""
    return kw["top"] if kw["top"] else MISC
