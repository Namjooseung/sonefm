"""③ 쪼개기: 구조로 쪼갠다.

번호줄(`1.`)이 2개 이상이면 번호줄마다, `<제목>` 줄은 새 업무. `-`·`*` 줄은 앞 업무의 하위 단계(steps).
`-`줄만 2개 이상인 칸(머리 줄이 없는 목록)은 줄마다 업무. `/`·`,`·줄바꿈으로 나뉜 조각은
**서로 다른 유형으로 키워드 확정될 때만** 쪼갠다(pieces_split). 뜻으로 쪼개는 것은 LLM 몫.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

_NUMBERED = re.compile(r"^\s*\d{1,2}\s*[.)]\s*\S")
_NUM_PREFIX = re.compile(r"^\s*\d{1,2}\s*[.)]\s*")
_TITLE = re.compile(r"^\s*<[^<>]+>")
_STEP = re.compile(r"^\s*[-*•·ㆍ]\s*(\S.*)$")
_DASH = re.compile(r"^\s*-\s*\S")
_PIECE_SEP = re.compile(r"\s*/\s*|\s*[,，、]\s*|\n+")
_SLASH_DIGITS = re.compile(r"(?<=\d)/(?=\d)")


@dataclass
class Unit:
    text: str                                  # 업무 글자(머리 줄 + 하위 단계)
    steps: list[str] = field(default_factory=list)
    split_by: str = "none"                     # none | number | title | dash | pieces


def _unit(lines: list[str], split_by: str) -> Unit:
    steps = []
    lines = list(lines)
    if split_by == "dash":
        lines[0] = re.sub(r"^\s*[-*•·ㆍ]\s*", "", lines[0])   # 머리 줄의 '-'는 단계 표시가 아니다
    for ln in lines[1:]:                                       # 첫 줄은 머리 줄이라 단계가 아니다
        m = _STEP.match(ln)
        if m:
            steps.append(m.group(1).strip())
    return Unit("\n".join(l.rstrip() for l in lines).strip(), steps, split_by)


def split_structure(text: str) -> list[Unit]:
    lines = [l for l in (text or "").splitlines() if l.strip()]
    if not lines:
        return []
    numbered = [i for i, l in enumerate(lines) if _NUMBERED.match(l)]
    titles = [i for i, l in enumerate(lines) if _TITLE.match(l)]
    starts: set[int] = set(titles)
    kind = "title" if titles else "none"
    if len(numbered) >= 2:
        starts |= set(numbered)
        kind = "number"
    elif not titles:
        dashes = [i for i, l in enumerate(lines) if _DASH.match(l)]
        # 머리 줄 없이 '-' 줄만 이어지는 칸: 줄마다 업무 (링크·자리표시만 있는 줄은 앞 줄에 붙인다)
        body = [l for l in lines if not re.fullmatch(r"\s*(\[링크\]|\(링크\)|\[가림\])\s*", l)]
        if len(dashes) >= 2 and all(_DASH.match(l) for l in body):
            starts |= set(dashes)
            kind = "dash"
    if len(starts) < 2 and not (starts and 0 not in starts):
        return [_unit(lines, "none")]
    cuts = sorted(starts | {0})
    units = []
    for a, b in zip(cuts, cuts[1:] + [len(lines)]):
        units.append(_unit(lines[a:b], kind if a in starts else "none"))
    # 앞에 붙은 번호는 지운다 (번호는 구조 신호일 뿐)
    for u in units:
        u.text = _NUM_PREFIX.sub("", u.text, count=1) if _NUMBERED.match(u.text) else u.text
    return units


def pieces(text: str) -> list[str]:
    """머리 줄 글자를 `/`·`,`·줄바꿈 조각으로 나눈다(날짜의 6/21, 숫자 사이 쉼표는 나누지 않는다)."""
    protected = _SLASH_DIGITS.sub("\u0001", text)
    protected = re.sub(r"(?<=\d),(?=\d)", "\u0002", protected)
    out = []
    for p in _PIECE_SEP.split(protected):
        p = p.replace("\u0001", "/").replace("\u0002", ",").strip()
        p = _NUM_PREFIX.sub("", p)
        if p:
            out.append(p)
    return out


def split_by_pieces(unit: Unit, confirm: Callable[[str], "str | None"]) -> list[Unit]:
    """머리 줄(하위 단계 앞)의 조각이 모두 키워드 확정이고 유형이 서로 다르면 조각마다 업무로 쪼갠다.

    confirm(조각) → 확정된 유형 id 또는 None.
    """
    if unit.split_by == "pieces":
        return [unit]
    head_lines = []
    for i, ln in enumerate(unit.text.splitlines()):
        if i > 0 and _STEP.match(ln):
            break
        head_lines.append(ln)
    head = "\n".join(head_lines)
    ps = pieces(head)
    if len(ps) < 2 or len(ps) > 6:
        return [unit]
    types = [confirm(p) for p in ps]
    if any(t is None for t in types) or len(set(types)) != len(types):
        return [unit]
    rest = unit.text[len(head):].strip("\n")
    out = []
    for i, p in enumerate(ps):
        out.append(Unit(p + (("\n" + rest) if i == len(ps) - 1 and rest else ""),
                        unit.steps if i == len(ps) - 1 else [], "pieces"))
    return out
