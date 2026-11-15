"""공급자 인터페이스와 테스트용 가짜 공급자. 실제 공급자(Claude)는 1c."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol


@dataclass
class ProviderReply:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


class ProviderError(Exception):
    """공급자가 던지는 오류의 뿌리. 상세 문구에 키를 넣지 않는다."""


class ProviderAuthError(ProviderError):
    """키가 틀렸거나 만료."""


class ProviderNetworkError(ProviderError):
    """연결 실패, 시간 초과, 과부하."""


class Provider(Protocol):
    def complete(self, model: str, system: Any, messages: list[dict], tools: Any, max_tokens: int) -> ProviderReply: ...


class FakeProvider:
    """정해 둔 응답을 차례로 돌려준다. 항목: ProviderReply | str | 예외 | 함수(call)->위 둘 중 하나."""

    def __init__(self, replies: list[Any] | Callable[[dict], Any]):
        self._replies = replies
        self.calls: list[dict] = []

    def complete(self, model, system, messages, tools, max_tokens) -> ProviderReply:
        call = {"model": model, "system": system, "messages": messages, "tools": tools, "max_tokens": max_tokens}
        self.calls.append(call)
        item = self._replies(call) if callable(self._replies) else self._replies[len(self.calls) - 1]
        if callable(item):
            item = item(call)
        if isinstance(item, BaseException):
            raise item
        return ProviderReply(text=item) if isinstance(item, str) else item
