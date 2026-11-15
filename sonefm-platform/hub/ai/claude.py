"""Claude 공급자 (anthropic SDK). 키는 생성자로만 받는다.

로그·예외에는 예외 클래스 이름과 상태 코드만 남긴다(str(e)·요청 헤더 금지: 키가 섞일 수 있다).
thinking·temperature·top_p·top_k·prefill 은 보내지 않는다(이 모델들은 thinking 을 생략하면 adaptive).
"""
from __future__ import annotations

import copy
import logging
import os
import threading
from typing import Any

import anthropic

from hub.ai.providers import (ProviderAuthError, ProviderError, ProviderFormatError, ProviderNetworkError,
                              ProviderReply)

log = logging.getLogger("hub.ai")
BASE_URL = "https://api.anthropic.com"
TIMEOUT_S = 120.0
_ENV_LOCK = threading.Lock()
logging.getLogger("anthropic").setLevel(logging.WARNING)   # SDK의 디버그 로그가 예외 문구(키 포함 가능)를 찍지 못하게


def _make_client(api_key: str, http_client: Any = None) -> anthropic.Anthropic:
    """SDK는 base_url·ANTHROPIC_CUSTOM_HEADERS 등을 환경 변수에서도 읽는다(api_key를 줘도).
    만드는 동안만 ANTHROPIC_* 를 치워서 우리 키와 공식 주소만 쓰게 한다."""
    with _ENV_LOCK:
        saved = {k: v for k, v in os.environ.items() if k.startswith("ANTHROPIC_")}
        for k in saved:
            del os.environ[k]
        try:
            return anthropic.Anthropic(api_key=api_key, base_url=BASE_URL, max_retries=2, timeout=TIMEOUT_S,
                                       http_client=http_client)
        finally:
            os.environ.update(saved)


def _system_blocks(system: Any) -> list[dict] | None:
    if not system:
        return None
    blocks = [{"type": "text", "text": system}] if isinstance(system, str) else copy.deepcopy(list(system))
    blocks[-1]["cache_control"] = {"type": "ephemeral"}   # 고정된 앞부분 캐시. 512토큰보다 짧으면 그냥 캐시 안 됨
    return blocks


class ClaudeProvider:
    def __init__(self, api_key: str, http_client: Any = None):
        self._client = _make_client(api_key, http_client)

    def complete(self, model: str, system: Any, messages: list[dict], tools: Any, max_tokens: int,
                 effort: str | None = None) -> ProviderReply:
        kw: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "messages": messages}
        blocks = _system_blocks(system)
        if blocks:
            kw["system"] = blocks
        if tools:
            kw["tools"] = tools
        if effort:
            kw["output_config"] = {"effort": effort}
        try:
            resp = self._client.messages.create(**kw)
        except anthropic.APIError as e:
            raise _convert(e) from None
        if resp.stop_reason == "refusal":
            raise ProviderFormatError("AI가 이 요청을 처리하지 않았습니다. 내용을 바꿔 다시 시도해 주세요.")
        if resp.stop_reason == "max_tokens":
            raise ProviderFormatError("AI 응답이 길어 잘렸습니다. 요청을 나누어 다시 시도해 주세요.")
        u = resp.usage
        return ProviderReply(
            text="".join(b.text for b in resp.content if b.type == "text"),
            input_tokens=u.input_tokens or 0, output_tokens=u.output_tokens or 0,
            cache_read_tokens=u.cache_read_input_tokens or 0, cache_write_tokens=u.cache_creation_input_tokens or 0)


def _convert(e: anthropic.APIError) -> ProviderError:
    name = type(e).__name__
    status = getattr(e, "status_code", None)
    log.warning("Claude 호출 실패: %s status=%s", name, status)
    if isinstance(e, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return ProviderAuthError(name)
    if isinstance(e, anthropic.RateLimitError):
        return ProviderNetworkError("요청이 많아 잠시 후 다시 시도해 주세요.")
    if isinstance(e, (anthropic.APITimeoutError, anthropic.APIConnectionError)):
        return ProviderNetworkError("AI 서비스에 연결하지 못했습니다. 인터넷 연결을 확인하고 잠시 뒤 다시 시도해 주세요.")
    if isinstance(e, anthropic.InternalServerError):
        return ProviderNetworkError("AI 서비스가 과부하이거나 문제가 있습니다. 잠시 뒤 다시 시도해 주세요.")
    return ProviderError(f"{name} {status}")
