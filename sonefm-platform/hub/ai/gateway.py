"""LLM 게이트웨이. complete() 하나가 진입점이고 밖으로는 AIError만 나간다.

순서: 꺼짐/키 확인 → 모델 → 단가 → 한도 → 마스킹 → 공급자 호출 → (스키마 검증, 1회 재시도) → 기록.
키는 key_getter 로만 받는다. 환경 변수를 읽지 않고, 키를 로그·예외·기록에 쓰지 않는다.
호출 전에 막힌 경우(off·budget)는 기록하지 않는다. 공급자까지 간 호출은 실패해도 기록한다.
"""
from __future__ import annotations

import copy
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from hub import db
from hub.ai import schema as S
from hub.ai.keys import get_key
from hub.ai.mask import KINDS, mask
from hub.ai.claude import ClaudeProvider
from hub.ai.providers import (Provider, ProviderAuthError, ProviderError, ProviderFormatError, ProviderNetworkError,
                              ProviderReply)
from hub.paths import load_config, platform_dir

log = logging.getLogger("hub.ai")

_MSG = {
    "off": "AI 기능이 꺼져 있습니다. 설정 화면에서 AI를 켜고 API 키를 입력해 주세요.",
    "auth": "API 키를 확인해 주세요. 키가 틀렸거나 만료되었을 수 있습니다. 설정 화면에서 다시 입력해 주세요.",
    "network": "AI 서비스에 연결하지 못했습니다. 인터넷 연결을 확인하고 잠시 뒤 다시 시도해 주세요.",
    "format": "AI 응답 형식이 맞지 않아 이번 결과는 쓰지 않았습니다. 잠시 뒤 다시 시도해 주세요.",
    "internal": "AI 기능에서 예상하지 못한 문제가 생겼습니다. 로그(platform.log)를 확인하거나 담당자에게 알려 주세요.",
}


class AIError(Exception):
    """kind: off | budget | auth | network | format | internal. message는 사용자에게 그대로 보여 준다."""

    def __init__(self, kind: str, message: str | None = None):
        self.kind = kind
        self.message = message or _MSG[kind]
        super().__init__(self.message)


@dataclass
class AIResult:
    text: str
    data: Any
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float
    duration_ms: int
    masked: dict[str, int] = field(default_factory=dict)


def cost_usd(price: dict[str, float], input_tokens: int, output_tokens: int, cache_read: int = 0,
             cache_write: int = 0) -> float:
    """단가는 USD/백만 토큰."""
    return (input_tokens * price["input"] + output_tokens * price["output"]
            + cache_read * price["cache_read"] + cache_write * price["cache_write"]) / 1_000_000


def _mask_messages(messages: list[dict], names: list[str]) -> tuple[list[dict], dict[str, int]]:
    total = {k: 0 for k, _ in KINDS}
    out = copy.deepcopy(messages)

    def one(s: str) -> str:
        r, c = mask(s, names)
        for k, n in c.items():
            total[k] += n
        return r

    for m in out:
        if isinstance(m.get("content"), str):
            m["content"] = one(m["content"])
        elif isinstance(m.get("content"), list):
            for b in m["content"]:
                if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str):
                    b["text"] = one(b["text"])
    return out, total


def _dev_log(feature: str, model: str, system: Any, messages: list[dict], reply: ProviderReply) -> None:
    """ai.dev_log가 켜졌을 때만 (마스킹 후) 원문을 로컬 파일로. 키는 인자로 오지 않는다. 실패해도 호출에 영향 없음."""
    try:
        now = datetime.now()
        d = platform_dir() / "ai-dev" / now.strftime("%Y-%m-%d")
        d.mkdir(parents=True, exist_ok=True)
        base = f"{now.strftime('%H%M%S')}_{feature}"
        p, n = d / f"{base}.json", 1
        while p.exists():
            n += 1
            p = d / f"{base}_{n}.json"
        usage = {"input_tokens": reply.input_tokens, "output_tokens": reply.output_tokens,
                 "cache_read_tokens": reply.cache_read_tokens, "cache_write_tokens": reply.cache_write_tokens}
        p.write_text(json.dumps({"model": model, "system": system, "messages": messages, "text": reply.text,
                                 "usage": usage}, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        log.warning("AI 개발 모드 기록 실패(%s)", type(e).__name__)


# DB에 못 쓴 호출 기록. 한도 계산에는 계속 더하고, 다음 호출 때 DB 기록을 다시 시도한다(프로세스 메모리만).
_pending: list[dict[str, Any]] = []
_pending_lock = threading.Lock()
RECORD_RETRY_WAIT_S = 0.2


def _record(fields: dict[str, Any]) -> None:
    """호출 기록 쓰기. 실패하면 한 번 더, 그래도 안 되면 메모리에 보관(응답은 그대로 돌려준다)."""
    for attempt in (0, 1):
        try:
            db.record_ai_call(**fields)
            return
        except Exception as e:  # noqa: BLE001  원문·키는 로그에 남기지 않는다
            log.warning("AI 호출 기록 실패(%s) 시도 %d/2", type(e).__name__, attempt + 1)
            if attempt == 0:
                time.sleep(RECORD_RETRY_WAIT_S)
    with _pending_lock:
        _pending.append(fields)
    log.error("AI 호출 기록을 DB에 못 써서 메모리에 보관합니다(한도 계산에는 반영). 보관 %d건", len(_pending))


def _flush_pending() -> None:
    with _pending_lock:
        while _pending:
            try:
                db.record_ai_call(**_pending[0])
            except Exception as e:  # noqa: BLE001
                log.warning("보관한 AI 호출 기록 재시도 실패(%s)", type(e).__name__)
                return
            _pending.pop(0)


def _pending_cost(start: str, end: str) -> float:
    with _pending_lock:
        return sum(p["cost_usd"] for p in _pending if start <= p["at"] < end)


def _no_key() -> None:
    return None


class Gateway:
    def __init__(self, key_getter: Callable[[], str | None] = _no_key,
                 provider_factory: Callable[[str], Provider] = ClaudeProvider):
        self._key_getter = key_getter
        self._provider_factory = provider_factory

    # 기본 max_tokens: thinking(adaptive) 토큰도 여기에 포함되므로 넉넉히
    def complete(self, feature: str, tier: str, messages: list[dict], system: Any = None, tools: Any = None,
                 schema: dict | None = None, max_tokens: int = 4096) -> AIResult:
        try:
            return self._complete(feature, tier, messages, system, tools, schema, max_tokens)
        except AIError:
            raise
        except Exception as e:  # noqa: BLE001  메시지에 키가 섞일 수 있어 클래스 이름만 남긴다
            log.error("AI 호출 준비 중 예상 못 한 오류(%s)", type(e).__name__)
            raise AIError("internal") from None

    def _complete(self, feature, tier, messages, system, tools, schema, max_tokens) -> AIResult:
        cfg = load_config()["ai"]
        key = self._key_getter()
        if not cfg["enabled"] or not key:
            raise AIError("off")
        model = cfg["models"][tier]    # 모르는 등급은 KeyError → internal (코드 실수)
        price = cfg["prices"].get(model) or {}
        if any(price.get(k) is None for k in ("input", "output", "cache_write", "cache_read")):
            raise AIError("budget", f"모델 {model}의 단가가 설정되어 있지 않습니다. 설정에서 단가를 넣어 주세요.")
        self._check_limits(cfg["limits"])
        if schema is not None:
            S.check_schema(schema)
        masked = {k: 0 for k, _ in KINDS}
        if cfg["masking"]["enabled"]:
            messages, masked = _mask_messages(messages, cfg["masking"]["names"])

        started = time.monotonic()
        usage = [0, 0, 0, 0]
        state = {"retried": False}
        error_kind = None
        try:
            provider = self._provider_factory(key)
            text, data = self._call(provider, model, system, list(messages), tools, max_tokens, schema, usage, state,
                                    effort=(cfg.get("effort") or {}).get(tier),
                                    dev=(feature, model) if cfg.get("dev_log") else None)
        except AIError as e:
            error_kind = e.kind
            raise
        except Exception as e:  # noqa: BLE001
            error_kind = "internal"
            log.error("AI 호출 중 예상 못 한 오류(%s)", type(e).__name__)
            raise AIError("internal") from None
        finally:
            ms = int((time.monotonic() - started) * 1000)
            cost = cost_usd(price, *usage)
            _record({"feature": feature, "tier": tier, "model": model, "input_tokens": usage[0],
                     "output_tokens": usage[1], "cache_read_tokens": usage[2], "cache_write_tokens": usage[3],
                     "cost_usd": cost, "duration_ms": ms, "ok": error_kind is None, "error_kind": error_kind,
                     "retried": state["retried"], "masked": masked, "at": db.now()})
        return AIResult(text, data, model, *usage, cost, ms, masked)

    def _call(self, provider, model, system, messages, tools, max_tokens, schema, usage, state, effort=None, dev=None):
        def ask(msgs) -> ProviderReply:
            try:
                r = provider.complete(model, system, msgs, tools, max_tokens, effort=effort)
            except ProviderAuthError:
                raise AIError("auth") from None
            except ProviderNetworkError as e:
                raise AIError("network", str(e) or None) from None
            except ProviderFormatError as e:
                for i, v in enumerate(e.usage):   # 거절·잘림도 토큰은 이미 썼다
                    usage[i] += v
                raise AIError("format", str(e) or None) from None
            except ProviderError:
                raise AIError("internal") from None   # 공급자가 이미 상태 코드만 로그에 남겼다
            if dev:
                _dev_log(dev[0], dev[1], system, msgs, r)
            for i, v in enumerate((r.input_tokens, r.output_tokens, r.cache_read_tokens, r.cache_write_tokens)):
                usage[i] += v
            return r

        reply = ask(messages)
        if schema is None:
            return reply.text, None
        for attempt in (0, 1):
            try:
                data = S.parse_json(reply.text)
                errs = S.validate(data, schema)
            except ValueError as e:
                errs = [f"JSON으로 읽을 수 없습니다 ({e})"]
            if not errs:
                return reply.text, data
            if attempt == 1:
                raise AIError("format")
            state["retried"] = True
            messages = messages + [
                {"role": "assistant", "content": reply.text},
                {"role": "user", "content": "응답이 형식에 맞지 않습니다: " + "; ".join(errs[:5])
                 + ". 설명 없이 형식에 맞는 JSON만 다시 보내 주세요."}]
            reply = ask(messages)

    @staticmethod
    def _check_limits(limits: dict) -> None:
        today = datetime.now()
        day = today.strftime("%Y-%m-%d")
        nxt_day = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        month_start = today.replace(day=1).strftime("%Y-%m-%d")
        nxt_month = (today.replace(day=1) + timedelta(days=32)).replace(day=1).strftime("%Y-%m-%d")
        for key, label, start, end, release in (
                ("daily_usd", "하루", day, nxt_day, "내일 0시에 풀립니다"),
                ("monthly_usd", "한 달", month_start, nxt_month, "다음 달 1일에 풀립니다")):
            limit = limits.get(key)
            if limit is None:
                continue
            _flush_pending()
            used = db.ai_cost_sum(start, end) + _pending_cost(start, end)
            if used >= limit:
                raise AIError("budget", f"AI 사용 한도({label} {limit:g}달러)를 넘었습니다. 지금까지 {used:.4f}달러를 "
                                        f"썼고 {release}. 급하면 설정에서 한도를 올려 주세요.")


_default = Gateway(key_getter=get_key, provider_factory=ClaudeProvider)


def complete(feature: str, tier: str, messages: list[dict], system: Any = None, tools: Any = None,
             schema: dict | None = None, max_tokens: int = 4096) -> AIResult:
    return _default.complete(feature, tier, messages, system, tools, schema, max_tokens)
