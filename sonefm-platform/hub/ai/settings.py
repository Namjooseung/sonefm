"""AI 설정 화면용: 입력 검증·저장, 상태(키 값은 절대 담지 않음)."""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

from hub import db
from hub.ai import keys
from hub.paths import load_config, save_config

PRICE_KEYS = ("input", "output", "cache_write", "cache_read")
_PRICE_LABEL = {"input": "입력", "output": "출력", "cache_write": "캐시 쓰기", "cache_read": "캐시 읽기"}


def _num(v: Any, label: str) -> float | None:
    """빈칸 → None. 숫자가 아니거나 음수·무한대면 ValueError."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        if isinstance(v, bool):
            raise ValueError
        n = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{label}은(는) 숫자로 입력해 주세요.") from None
    if math.isnan(n) or math.isinf(n) or n < 0:
        raise ValueError(f"{label}은(는) 0 이상의 숫자로 입력해 주세요.")
    return n


def _model_id(v: Any, label: str) -> str:
    s = str(v or "").strip()
    if not s or any(c.isspace() for c in s):
        raise ValueError(f"{label} 모델 ID를 공백 없이 입력해 주세요.")
    return s


def save(ai: dict[str, Any]) -> None:
    """검증하고 config.json의 ai에 저장한다. 잘못이면 ValueError(한국어)."""
    cfg = load_config()
    old = cfg["ai"]
    models = {t: _model_id((ai.get("models") or {}).get(t), lab) for t, lab in (("light", "가벼운 작업용"), ("heavy", "무거운 작업용"))}
    prices = dict(old["prices"])
    given = ai.get("prices") or {}
    for mid in dict.fromkeys(models.values()):
        row = given.get(mid) or prices.get(mid) or {}
        prices[mid] = {k: _num(row.get(k), f"{mid} {_PRICE_LABEL[k]} 단가") for k in PRICE_KEYS}
    lim = ai.get("limits") or {}
    masking = ai.get("masking") or {}
    names = masking.get("names") or []
    if isinstance(names, str):
        names = names.splitlines()
    cfg["ai"] = {**old, "enabled": bool(ai.get("enabled")), "models": models, "prices": prices,
                 "limits": {"daily_usd": _num(lim.get("daily_usd"), "하루 한도"),
                            "monthly_usd": _num(lim.get("monthly_usd"), "한 달 한도")},
                 "masking": {"enabled": bool(masking.get("enabled")),
                             "names": [n.strip() for n in names if str(n).strip()]}}
    save_config(cfg)


def status() -> dict[str, Any]:
    ai = load_config()["ai"]
    now = datetime.now()
    d0, d1 = now.strftime("%Y-%m-%d"), (now + timedelta(days=1)).strftime("%Y-%m-%d")
    m0 = now.replace(day=1)
    m1 = (m0 + timedelta(days=32)).replace(day=1)
    m0, m1 = m0.strftime("%Y-%m-%d"), m1.strftime("%Y-%m-%d")
    return {"has_key": keys.has_key(), "enabled": ai["enabled"], "models": ai["models"],
            "prices": {m: {k: (ai["prices"].get(m) or {}).get(k) for k in PRICE_KEYS} for m in dict.fromkeys(ai["models"].values())},
            "limits": ai["limits"], "masking": ai["masking"],
            "usage": {"today_usd": db.ai_cost_sum(d0, d1), "month_usd": db.ai_cost_sum(m0, m1),
                      "month_calls": db.ai_call_count(m0, m1)}}
