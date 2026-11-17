"""보내기 직전 개인정보 가리기. 패턴은 '형태'만 본다. 이름은 목록에 있는 것만 (정규식으로 찾지 않는다)."""
from __future__ import annotations

import re

MIN_NAME_LEN = 2
KINDS = (("phone", "전화"), ("rrn", "주민번호"), ("account", "계좌"), ("email", "이메일"), ("name", "이름"))

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_RRN = re.compile(r"(?<!\d)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])-?[1-4]\d{6}(?!\d)")
_PHONE = re.compile(r"(?<![\d-])0(?:1[016789]|2|[3-6]\d|70)[-\s]?\d{3,4}[-\s]?\d{4}(?!-?\d)")
_ACCOUNT = re.compile(r"(?<![\d-])\d{2,6}(?:-\d{1,8}){2,}(?!-?\d)")


def mask(text: str, names: list[str] | None = None) -> tuple[str, dict[str, int]]:
    counts = {k: 0 for k, _ in KINDS}
    seen: dict[tuple[str, str], int] = {}
    label = dict(KINDS)

    def token(kind: str, value: str) -> str:
        key = (kind, re.sub(r"\D", "", value) if kind in ("phone", "rrn", "account") else value)
        if key not in seen:
            counts[kind] += 1
            seen[key] = counts[kind]
        return f"[{label[kind]}{seen[key]}]"

    def sub(kind: str, pattern: re.Pattern, s: str, ok=lambda v: True) -> str:
        return pattern.sub(lambda m: token(kind, m.group()) if ok(m.group()) else m.group(), s)

    out = sub("email", _EMAIL, text)
    # 1글자 이름은 부분 문자열로 문장을 망가뜨리므로 무시한다(설정 저장에서도 거절)
    ns = sorted({n.strip() for n in (names or []) if n and len("".join(n.split())) >= MIN_NAME_LEN}, key=len,
                reverse=True)
    if ns:
        out = sub("name", re.compile("|".join(re.escape(n) for n in ns)), out)
    out = sub("rrn", _RRN, out)
    out = sub("phone", _PHONE, out)
    # shortcut: 계좌는 하이픈 덩어리 3개 이상·숫자 10~16자리만, 하이픈 없는 긴 숫자는 안 가린다
    out = sub("account", _ACCOUNT, out, lambda v: 10 <= len(re.sub(r"\D", "", v)) <= 16)
    return out, counts
