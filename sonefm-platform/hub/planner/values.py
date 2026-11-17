"""⑦ 값 추출(코드)과 ⑦-1 유형 기본값.

기준일 ref = 그 칸의 업무일지 날짜. 값 키는 유형의 value_keys 안에서만 낸다.
연도 규칙: 같은 해로 두고, 결과가 기준일보다 10개월 넘게 앞이면 다음 해, 2개월 넘게 뒤면 전년.
org·project·items 같은 값은 코드로 뽑지 않는다(LLM 몫).
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

WEEKDAYS = "월화수목금토일"


# ---- 날짜 도우미 -------------------------------------------------------------------

def _midx(y: int, m: int) -> int:
    return y * 12 + (m - 1)


def fit_year(month: int, ref: date) -> int:
    """연도 없는 'n월'의 연도."""
    y = ref.year
    diff = _midx(y, month) - _midx(ref.year, ref.month)
    if diff < -10:
        return y + 1
    if diff > 2:
        return y - 1
    return y


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def date_md(month: int, day: int, ref: date) -> date | None:
    d = _safe_date(ref.year, month, day)
    if d is None:
        return None
    diff = _midx(d.year, d.month) - _midx(ref.year, ref.month)
    if diff < -10:
        return _safe_date(d.year + 1, month, day)
    if diff > 2:
        return _safe_date(d.year - 1, month, day)
    return d


def month_str(y: int, m: int) -> str:
    return f"{y:04d}-{m:02d}"


def add_months(y: int, m: int, n: int) -> tuple[int, int]:
    i = _midx(y, m) + n
    return i // 12, i % 12 + 1


def week_of(d: date) -> tuple[date, date]:
    mon = d - timedelta(days=d.weekday())
    return mon, mon + timedelta(days=6)


def week_str(d: date) -> str:
    a, b = week_of(d)
    return f"{a.isoformat()}~{b.isoformat()}"


# ---- 패턴 ---------------------------------------------------------------------------

_DATE_FULL = re.compile(r"(?<!\d)(\d{4})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})\s*일?")
_DATE_KR = re.compile(r"(?<!\d)(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_DATE_SLASH = re.compile(r"(?<![\d/.])(\d{1,2})/(\d{1,2})(?![\d/])")
_MONTH_RANGE = re.compile(r"(?<!\d)(\d{1,2})\s*[~\-]\s*(\d{1,2})\s*월")
_MONTH = re.compile(r"(?<!\d)(?:(\d{4})\s*년\s*)?(\d{1,2})\s*월(?:\s*(\d)\s*주)?")
_DONE_MONTH = re.compile(r"(?<!\d)(\d{1,2})\s*월\s*완료")
_YEAR_RANGE = re.compile(r"(?<!\d)(\d{4})\s*년?\s*[~\-]\s*(\d{4})\s*년?")
_YEAR = re.compile(r"(?<!\d)(20\d{2})\s*년도?|(?<!\d)(20\d{2})_")
_REL_WEEK = [(re.compile(r"이번\s*주|금주"), 0),
             (re.compile(r"다음\s*주|(?<![가-힣])(?:차주|내주)(?![가-힣])"), 1),
             (re.compile(r"지난\s*주|저번\s*주"), -1)]
_REL_MONTH = [(re.compile(r"이번\s*달"), 0), (re.compile(r"지난\s*달|저번\s*달"), -1), (re.compile(r"다음\s*달|내달"), 1)]
_DAY_UNTIL = re.compile(r"(?<![\d월])(\d{1,2})\s*일\s*까지")
_WD_UNTIL = re.compile(r"([월화수목금토일])요일\s*(?:퇴근\s*)?(?:까지|전|이전)")
_REL_UNTIL = [(re.compile(r"오늘\s*까지"), 0), (re.compile(r"내일\s*까지"), 1), (re.compile(r"모레\s*까지"), 2)]
_TIME = re.compile(r"(?<![\d:])(?:(오전|오후)\s*)?(\d{1,2})\s*시(?!간|청|민|장|작|선|정|도|설|스|험|사)(?:\s*(\d{1,2})\s*분)?")
_TIME_COLON = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})(?![\d:])")
_NOT_TIME_AFTER = re.compile(r"^\s*(?:에\s*)?(?:출근|퇴근)")
_QTY = re.compile(r"(?<![\d~])(\d{1,3}(?:\s*[~\-]\s*\d{1,3})?\s*(?:개|부|곳|명|건|편|장|권|회)(?:씩)?)(?![가-힣])")
_DURATION = re.compile(r"(?<![\d~])(\d{1,3}\s*분(?:\s*이내)?)(?!\s*[야기배리석])")
_WEEKDAY_LIST = re.compile(r"(?<![가-힣])([월화수목금토일](?:\s*[,，、/]\s*[월화수목금토일])+)(?![가-힣])")
_COHORT = re.compile(r"(?<!\d)(\d{1,2}기)(?![가-힣])")


def _blank(s: str, span: tuple[int, int]) -> str:
    return s[:span[0]] + " " * (span[1] - span[0]) + s[span[1]:]


def extract_values(text: str, vkeys: list[str], ref: date, programs: list[str] | None = None) -> tuple[dict, dict]:
    """→ (values, value_sources). 키는 vkeys 안에서만. 출처는 모두 'text'."""
    keys = set(vkeys)
    v: dict[str, Any] = {}
    work = re.sub(r"\((?:예|예시)\s*[:：)][^)]*\)", " ", text or "")    # '(예: …)' 설명 속 값은 이 업무의 값이 아니다

    # 날짜: 전체 날짜 → n월 m일 → m/d. '까지'가 붙으면 기한, '기준'이 붙으면 기준일
    dates: list[date] = []
    for pat in (_DATE_FULL, _DATE_KR, _DATE_SLASH):
        for m in list(pat.finditer(work)):
            if pat is _DATE_FULL:
                d = _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            else:
                d = date_md(int(m.group(1)), int(m.group(2)), ref)
            if d is None:
                continue
            rest = work[m.end():]
            if re.match(r"\s*(?:\([^)]*\))?\s*까지", rest) and "deadline" in keys:
                v.setdefault("deadline", d.isoformat())
            elif re.match(r"\s*(?:\([^)]*\))?\s*기준", rest) and "base_date" in keys:
                v.setdefault("base_date", d.isoformat())
            else:
                dates.append(d)
            work = _blank(work, m.span())
    if "date" in keys and dates:
        v["date"] = dates[0].isoformat()
    if "date" in keys and "date" not in v and re.search(r"오늘자", work):
        v["date"] = ref.isoformat()

    # 월 범위(1~5월), 연 범위(2021~2026) → 기간
    for m in list(_MONTH_RANGE.finditer(work)):
        a, b = int(m.group(1)), int(m.group(2))
        if 1 <= a <= 12 and 1 <= b <= 12 and "period" in keys:
            v["period"] = f"{month_str(fit_year(a, ref), a)}~{month_str(fit_year(b, ref), b)}"
        work = _blank(work, m.span())
    for m in list(_YEAR_RANGE.finditer(work)):
        if "period" in keys and "period" not in v and 2000 <= int(m.group(1)) <= int(m.group(2)) <= 2100:
            v["period"] = f"{m.group(1)}~{m.group(2)}"
        work = _blank(work, m.span())

    # 완료한 달, 월, 주
    if "done_month" in keys:
        m = _DONE_MONTH.search(work)
        if m and 1 <= int(m.group(1)) <= 12:
            v["done_month"] = month_str(fit_year(int(m.group(1)), ref), int(m.group(1)))
            work = _blank(work, m.span())
    months: list[str] = []
    for m in _MONTH.finditer(work):
        mo = int(m.group(2))
        if not 1 <= mo <= 12:
            continue
        y = int(m.group(1)) if m.group(1) else fit_year(mo, ref)
        ms = month_str(y, mo)
        if m.group(3) and "week" in keys:
            v.setdefault("week", f"{ms} {m.group(3)}주")
        if ms not in months:
            months.append(ms)
    if not months:
        for pat, delta in _REL_MONTH:
            if pat.search(work):
                y, mo = add_months(ref.year, ref.month, delta)
                months.append(month_str(y, mo))
                break
    if months:
        if len(months) >= 2 and "months" in keys:
            v["months"] = months
        elif "month" in keys:
            v["month"] = months[0]
        elif "months" in keys:
            v["months"] = months

    # 기간(상대 주)
    if "period" in keys and "period" not in v:
        for pat, delta in _REL_WEEK:
            if pat.search(work):
                v["period"] = week_str(ref + timedelta(days=7 * delta))
                break

    # 기한
    if "deadline" in keys and "deadline" not in v:
        for pat, n in _REL_UNTIL:
            if pat.search(work):
                v["deadline"] = (ref + timedelta(days=n)).isoformat()
                break
    if "deadline" in keys and "deadline" not in v:
        m = _DAY_UNTIL.search(work)
        if m:
            day = int(m.group(1))
            d = _safe_date(ref.year, ref.month, day)
            if d is not None and d < ref:
                y, mo = add_months(ref.year, ref.month, 1)
                d = _safe_date(y, mo, day)
            if d is not None:
                v["deadline"] = d.isoformat()
    if "deadline" in keys and "deadline" not in v:
        m = _WD_UNTIL.search(work)
        if m:
            target = WEEKDAYS.index(m.group(1))
            v["deadline"] = (ref + timedelta(days=(target - ref.weekday()) % 7)).isoformat()

    # 시각
    if "time" in keys:
        for m in _TIME.finditer(work):
            if _NOT_TIME_AFTER.match(work[m.end():]):
                continue
            h = int(m.group(2))
            if m.group(1) == "오후" and h < 12:
                h += 12
            if 0 <= h <= 24:
                v["time"] = f"{h:02d}:{int(m.group(3) or 0):02d}"
                break
        if "time" not in v:
            m = _TIME_COLON.search(work)
            if m and int(m.group(1)) <= 24:
                v["time"] = f"{int(m.group(1)):02d}:{m.group(2)}"
        if "time" in v and "date" in keys and "date" not in v:
            v["date"] = ref.isoformat()      # 시각만 적힌 칸은 그날 일이다

    if "year" in keys:
        m = _YEAR.search(text or "")
        if m:
            v["year"] = m.group(1) or m.group(2)
    if "quantity" in keys:
        m = _QTY.search(work)
        if m:
            v["quantity"] = re.sub(r"\s+", "", m.group(1))
    if "duration" in keys:
        m = _DURATION.search(work)
        if m:
            v["duration"] = re.sub(r"\s+", "", m.group(1)).replace("이내", " 이내")
    if "weekdays" in keys:
        m = _WEEKDAY_LIST.search(work)
        if m:
            v["weekdays"] = re.findall(r"[월화수목금토일]", m.group(1))
    if "cohort" in keys:
        found = _COHORT.findall(work)
        if found:
            v["cohort"] = found[-1]       # 'A기 → B기'로 바꾸는 글은 마지막 기수가 새 값이다
    if "programs" in keys or "not_selected" in keys:
        p, ns = match_programs(text or "", programs or [], split_not_selected="not_selected" in keys)
        if p and "programs" in keys:
            v["programs"] = p
        if ns and "not_selected" in keys:
            v["not_selected"] = ns
    v = {k: val for k, val in v.items() if k in keys}
    return v, {k: "text" for k in v}


# ---- 프로그램 이름 맞추기 -------------------------------------------------------------

_BRACKET = re.compile(r"\[[^\]]*\]")
_EP_ITEM = r"\d{1,3}\s*(?:화|회)?"
_EP_LIST = re.compile(rf"^[\s_\-:·]*((?:{_EP_ITEM})(?:(?:\s*[,，、]\s*|\s*[~]\s*|\s+)(?:{_EP_ITEM}))*)")


def norm_name(s: str) -> str:
    s = _BRACKET.sub("", s or "")
    return re.sub(r"[\W_]+", "", s)


def _variants(name: str) -> list[str]:
    raw = _BRACKET.sub("", name or "").strip()
    out = [norm_name(raw)]
    m = re.match(r"^\S{1,6}의\s+(.+)$", raw)       # '~의 ' 앞 이름 접두어는 무시하고도 맞춰 본다
    if m:
        out.append(norm_name(m.group(1)))
    return [x for x in dict.fromkeys(out) if len(x) >= 4]


def _loose(variant: str) -> re.Pattern:
    return re.compile(r"[\W_]*".join(re.escape(c) for c in variant))


def parse_episodes(tail: str) -> list[int]:
    m = _EP_LIST.match(tail)
    if not m:
        return []
    out: list[int] = []
    body = m.group(1)
    for rm in list(re.finditer(r"(\d{1,3})\s*(?:화|회)?\s*~\s*(\d{1,3})", body)):   # '48~51' 범위
        a, b = int(rm.group(1)), int(rm.group(2))
        if a < b <= a + 200:
            out.extend(range(a, b + 1))
        body = body[:rm.start()] + " " * (rm.end() - rm.start()) + body[rm.end():]
    out.extend(int(n) for n in re.findall(r"\d{1,3}", body))
    return sorted(dict.fromkeys(out))


def match_programs(text: str, programs: list[str], split_not_selected: bool = False):
    """→ (programs, not_selected): [{"name", "episodes"}]. 이름은 programs 목록의 이름으로 낸다."""
    hits: list[tuple[int, int, str]] = []
    for name in programs:
        for var in _variants(name):
            for m in _loose(var).finditer(text):
                hits.append((m.start(), m.end(), name))
    hits.sort(key=lambda h: (h[0], -(h[1] - h[0])))
    chosen: list[tuple[int, int, str]] = []
    for h in hits:                                  # 겹치면 먼저 시작하고 긴 것
        if not chosen or h[0] >= chosen[-1][1]:
            chosen.append(h)
    marker = re.search(r"미\s*선정", text) if split_not_selected else None
    sel: dict[str, list[int]] = {}
    not_sel: dict[str, list[int]] = {}
    for s, e, name in chosen:
        eps = parse_episodes(text[e:])
        bucket = not_sel if marker and s >= marker.start() else sel
        bucket[name] = sorted(set(bucket.get(name, [])) | set(eps))
    if not chosen:
        eps = sorted(dict.fromkeys(int(n) for n in re.findall(r"(?<!\d)(\d{1,3})\s*화", text)))
        if eps:
            return [{"episodes": eps}], []

    def as_list(d: dict[str, list[int]]) -> list[dict[str, Any]]:
        return [{"name": n, "episodes": e} for n, e in d.items()]

    return as_list(sel), as_list(not_sel)


# ---- ⑦-1 기본값 ---------------------------------------------------------------------

def resolve_default(word: str, ref: date) -> Any:
    if word == "ref_date":
        return ref.isoformat()
    if word == "this_month":
        return month_str(ref.year, ref.month)
    if word in ("prev_month", "next_month"):
        y, m = add_months(ref.year, ref.month, -1 if word == "prev_month" else 1)
        return month_str(y, m)
    if word == "this_week":
        return week_str(ref)
    return None


def apply_defaults(values: dict, sources: dict, defaults: dict[str, str], vkeys: list[str], ref: date) -> None:
    """문장에 값이 없으면 유형의 defaults 낱말을 쓴다. 출처는 'default'."""
    for key, word in (defaults or {}).items():
        if key in vkeys and key not in values:
            val = resolve_default(word, ref)
            if val is not None:
                values[key] = val
                sources[key] = "default"
