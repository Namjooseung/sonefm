"""플래너 가리기 G1~G6 (AI_DESIGN 4.7). 화면·DB·LLM에는 G2~G4를 적용한 글자만 쓴다."""
from __future__ import annotations

import re

LABEL = r"(?:아이디|계정|비밀번호|비번|PW|pw|Pw|password|Password|PASSWORD|ID|id|Id)"
# G1: 칸 전체를 LLM에 보내지 않는다
_G1 = re.compile(r"아이디|비번|비밀번호|계정|(?<![A-Za-z])(?:pw|password)(?![A-Za-z])|(?<![A-Za-z])id\s*[:：]", re.I)
_URL = re.compile(r"(?:https?://|www\.)[^\s\]\)>\"']+", re.I)
_DOMAIN = re.compile(
    r"(?<![\w@.])(?:[A-Za-z0-9-]+\.)+(?:com|net|org|kr|io|me|ly|co|go|or|ac)(?:\.[a-z]{2})?(?:/[^\s\]\)>\"']*)?(?![\w@])", re.I)
_PLACEHOLDER = re.compile(r"\[(?:링크|가림|이름|기관명)\]")
# G3: '아이디: 값' / '(비번) 값' / 메일 바로 뒤 토큰
_G3_COLON = re.compile(rf"({LABEL})(\s*\)?\s*[:：]\s*)(?!\[(?:가림|링크)\])([^\s/,，;]+)")
_G3_PAREN = re.compile(rf"(\({LABEL}\)\s*)(?!\[(?:가림|링크)\])([^\s/,，;]+)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_AFTER_EMAIL = re.compile(r"(?P<m>[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}[ \t]+)"
                          r"(?P<t>[\x21-\x7e]{6,})(?![가-힣])")
# G4: 영문+숫자(+기호) 8자 이상 토큰
_TOKEN = re.compile(r"[A-Za-z0-9!@#$%^&*_\-+=.~]{8,}")


def is_credential_cell(text: str) -> bool:
    """G1: 계정 칸이면 True. 이런 칸은 LLM 단계로 보내지 않는다."""
    return bool(_G1.search(text or ""))


def _g4(m: re.Match) -> str:
    t = m.group()
    if re.search(r"[A-Za-z]", t) and re.search(r"\d", t):
        return "[가림]"
    return t


def mask_text(text: str) -> str:
    """G2 링크 → [링크], G3 라벨 뒤 값 → [가림], G4 비밀번호 모양 → [가림]. 이미 가려진 글자는 그대로."""
    s = text or ""
    s = _G3_COLON.sub(lambda m: f"{m.group(1)}{m.group(2)}[가림]", s)
    s = _G3_PAREN.sub(lambda m: f"{m.group(1)}[가림]", s)
    s = _AFTER_EMAIL.sub(lambda m: f"{m.group('m')}[가림]", s)
    s = _URL.sub("[링크]", s)
    # 도메인·메일 주소는 G4가 건드리기 전에 처리한다 (메일은 게이트웨이 마스킹이 맡는다)
    parts = []
    last = 0
    for m in _EMAIL.finditer(s):
        parts.append(_DOMAIN.sub("[링크]", s[last:m.start()]))
        parts.append(m.group())
        last = m.end()
    parts.append(_DOMAIN.sub("[링크]", s[last:]))
    s = "".join(parts)
    # 자리표시([링크] 등)와 메일은 G4 대상에서 뺀다
    out, last = [], 0
    keep = [(m.start(), m.end()) for m in _PLACEHOLDER.finditer(s)] + [(m.start(), m.end()) for m in _EMAIL.finditer(s)]
    keep.sort()
    for a, b in keep + [(len(s), len(s))]:
        out.append(_TOKEN.sub(_g4, s[last:a]))
        out.append(s[a:b])
        last = b
    return "".join(out)


def mask_names(text: str, names: list[str] | None) -> str:
    """G6: 근무자 이름(2글자 이상)을 [이름]으로. 게이트웨이 name 마스킹이 한 번 더 한다."""
    ns = sorted({n.strip() for n in (names or []) if n and len("".join(n.split())) >= 2}, key=len, reverse=True)
    if not ns:
        return text
    return re.compile("|".join(re.escape(n) for n in ns)).sub("[이름]", text)


def find_violations(text: str) -> list[str]:
    """LLM으로 나가는 글자에 G1~G4 패턴이 남았는지 → 위반 종류 목록 (M7)."""
    out: list[str] = []
    if is_credential_cell(text):
        out.append("G1")
    if _URL.search(text) or _DOMAIN.search(_EMAIL.sub("", text)):
        out.append("G2")
    if _G3_COLON.search(text) or _G3_PAREN.search(text) or _AFTER_EMAIL.search(text):
        out.append("G3")
    stripped = _PLACEHOLDER.sub(" ", _EMAIL.sub(" ", text))
    if any(_g4(m) == "[가림]" for m in _TOKEN.finditer(stripped)):
        out.append("G4")
    return out
