"""1c: Claude 공급자. 실제 API는 부르지 않는다(httpx2 MockTransport). real_ai 표시 테스트만 --real-ai 로."""
import json
import logging
import os

import httpx2
import pytest

from hub import db
from hub.ai import gateway as G
from hub.ai.claude import ClaudeProvider
from hub.ai.gateway import AIError, Gateway
from hub.ai.providers import ProviderAuthError, ProviderError, ProviderFormatError, ProviderNetworkError
from hub.paths import config_path, load_config, platform_dir, save_config

KEY = "sk-ant-FAKE-0123456789abcdefghij"
USER = [{"role": "user", "content": "a"}]


def reply(text="안녕", stop="end_turn", usage=None, content=None):
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": "m", "stop_reason": stop,
            "stop_sequence": None, "content": content or [{"type": "text", "text": text}],
            "usage": usage or {"input_tokens": 10, "output_tokens": 5}}


def mock_client(handler):
    return httpx2.Client(transport=httpx2.MockTransport(handler))


def provider(handler, retries=None):
    seen = []

    def h(req):
        seen.append(req)
        return handler(req)
    p = ClaudeProvider(KEY, http_client=mock_client(h))
    if retries is not None:
        p._client = p._client.with_options(max_retries=retries)
    return p, seen


def ok(data=None):
    return lambda req: httpx2.Response(200, json=data or reply())


def body(req):
    return json.loads(req.content)


def err(status, msg=KEY):
    return lambda req: httpx2.Response(status, json={"type": "error", "error": {"type": "x", "message": msg}})


def test_환경변수가_끼어들지_않음(monkeypatch):
    for k, v in (("ANTHROPIC_AUTH_TOKEN", "tok-ENV"), ("ANTHROPIC_PROFILE", "evil"),
                 ("ANTHROPIC_BASE_URL", "https://evil.example"), ("ANTHROPIC_API_KEY", "sk-ENV"),
                 ("ANTHROPIC_CUSTOM_HEADERS", "X-Evil: 1"), ("ANTHROPIC_CONFIG_DIR", "C:/nowhere")):
        monkeypatch.setenv(k, v)
    p, seen = provider(ok())
    p.complete("m", "시스템", USER, None, 100)
    r = seen[0]
    assert r.url.host == "api.anthropic.com" and r.url.scheme == "https"
    assert r.headers["authorization"] == f"Bearer {KEY}"   # anthropic 2.x SDK는 api_key를 Bearer로 보낸다
    assert "x-evil" not in r.headers
    assert "tok-ENV" not in str(r.headers) and "sk-ENV" not in str(r.headers)
    assert os.environ["ANTHROPIC_BASE_URL"] == "https://evil.example"   # 만든 뒤 환경은 원래대로


def test_요청_모양_캐시_effort_금지_매개변수():
    p, seen = provider(ok())
    p.complete("claude-x", "고정 시스템", USER, None, 4096, effort="low")
    b = body(seen[0])
    assert b["system"] == [{"type": "text", "text": "고정 시스템", "cache_control": {"type": "ephemeral"}}]
    assert b["output_config"] == {"effort": "low"} and b["model"] == "claude-x" and b["max_tokens"] == 4096
    for k in ("thinking", "temperature", "top_p", "top_k", "tools"):
        assert k not in b
    assert b["messages"][-1]["role"] == "user"

    blocks = [{"type": "text", "text": "A"}, {"type": "text", "text": "B"}]
    p.complete("m", blocks, USER, None, 10)
    s = body(seen[1])["system"]
    assert "cache_control" not in s[0] and s[1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in blocks[1] and "output_config" not in body(seen[1])   # 입력을 바꾸지 않음
    p.complete("m", None, USER, None, 10)
    assert "system" not in body(seen[2])


def test_응답_text만_이어붙이고_usage_네종():
    usage = {"input_tokens": 1, "output_tokens": 2, "cache_read_input_tokens": 3, "cache_creation_input_tokens": 4}
    content = [{"type": "thinking", "thinking": "비밀생각", "signature": "s"}, {"type": "text", "text": "가"},
               {"type": "text", "text": "나"}]
    p, _ = provider(ok(reply(usage=usage, content=content)))
    r = p.complete("m", None, USER, None, 10)
    assert r.text == "가나"
    assert (r.input_tokens, r.output_tokens, r.cache_read_tokens, r.cache_write_tokens) == (1, 2, 3, 4)
    p, _ = provider(ok(reply()))   # 캐시 필드가 없어도 0
    r = p.complete("m", None, USER, None, 10)
    assert (r.cache_read_tokens, r.cache_write_tokens) == (0, 0)


def test_refusal과_max_tokens():
    p, _ = provider(ok(reply(stop="refusal", text="")))
    with pytest.raises(ProviderFormatError, match="처리하지 않았"):
        p.complete("m", None, USER, None, 10)
    p, _ = provider(ok(reply(stop="max_tokens")))
    with pytest.raises(ProviderFormatError, match="길어 잘렸"):
        p.complete("m", None, USER, None, 10)


@pytest.mark.parametrize("status,cls,text", [(401, ProviderAuthError, None), (403, ProviderAuthError, None),
                                             (429, ProviderNetworkError, "요청이 많아"), (500, ProviderNetworkError, "과부하"),
                                             (529, ProviderNetworkError, "과부하"), (400, ProviderError, "400")])
def test_상태코드_변환과_키_비노출(status, cls, text, caplog):
    p, _ = provider(err(status), retries=0)
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(ProviderError) as e:
            p.complete("m", None, USER, None, 10)
    assert type(e.value) is cls
    if text:
        assert text in str(e.value)
    assert KEY not in str(e.value) and KEY not in caplog.text and e.value.__cause__ is None


def test_연결오류와_시간초과():
    def boom(req):
        raise httpx2.ConnectError("연결 " + KEY)

    def slow(req):
        raise httpx2.ReadTimeout("시간 " + KEY)
    for h in (boom, slow):
        p, _ = provider(h, retries=0)
        with pytest.raises(ProviderNetworkError) as e:
            p.complete("m", None, USER, None, 10)
        assert KEY not in str(e.value)


def gw(handler):
    return Gateway(key_getter=lambda: KEY, provider_factory=lambda k: ClaudeProvider(k, http_client=mock_client(handler)))


def test_게이트웨이_effort_등급별과_실제_단가():
    sent = []

    def handler(req):
        sent.append(body(req))
        return httpx2.Response(200, json=reply(usage={"input_tokens": 1_000_000, "output_tokens": 1_000_000,
                                                      "cache_read_input_tokens": 1_000_000,
                                                      "cache_creation_input_tokens": 1_000_000}))
    g = gw(handler)
    r = g.complete("t", "light", USER, system="시스템")
    g.complete("t", "heavy", USER)
    assert [b["output_config"]["effort"] for b in sent] == ["low", "medium"]
    assert sent[0]["max_tokens"] == 4096 and sent[0]["system"][0]["cache_control"]
    assert r.cost_usd == pytest.approx(0.10 + 0.50 + 0.01 + 0.125)   # haiku 실제 단가
    assert G._default._provider_factory is ClaudeProvider


def test_게이트웨이_오류_종류와_로그에_키_없음(caplog):
    with caplog.at_level(logging.DEBUG):
        for kind, st in (("auth", 401), ("network", 529), ("network", 429), ("internal", 400)):
            with pytest.raises(AIError) as e:
                gw(err(st)).complete("t", "light", USER)
            assert e.value.kind == kind and KEY not in e.value.message

        def bad(req):
            raise ValueError("내부 " + KEY)
        with pytest.raises(AIError) as e:
            gw(bad).complete("t", "light", USER)
        assert e.value.kind == "network" and KEY not in e.value.message   # SDK가 전송 중 예외를 연결 오류로 감쌈
        p, _ = provider(ok(reply(stop="refusal", text="")))
        with pytest.raises(AIError) as e:
            Gateway(key_getter=lambda: KEY, provider_factory=lambda k: p).complete("t", "light", USER)
        assert e.value.kind == "format" and "처리하지 않았" in e.value.message
    assert KEY not in caplog.text
    with db.connect() as c:
        assert KEY not in json.dumps([dict(r) for r in c.execute("SELECT * FROM ai_calls")])


def test_공급자_밖_예외도_로그에_메시지_없음(caplog):
    g = Gateway(key_getter=lambda: KEY, provider_factory=lambda k: 1 / 0)
    with caplog.at_level(logging.DEBUG), pytest.raises(AIError):
        g.complete("t", "light", USER)
    assert "ZeroDivisionError" in caplog.text and "Traceback" not in caplog.text


# ── 설정: 단가 기본값·null 병합·effort ─────────────────
def test_실제_단가_기본값과_null_병합():
    ai = load_config()["ai"]
    assert ai["prices"]["claude-haiku-5-5"] == {"input": 0.10, "output": 0.50, "cache_write": 0.125, "cache_read": 0.01}
    assert ai["prices"]["claude-sonnet-5-5"]["output"] == 10.0
    assert ai["effort"] == {"light": "low", "heavy": "medium"}
    config_path().write_text(json.dumps({"ai": {"prices": {
        "claude-haiku-5-5": {"input": None, "output": 9.0, "cache_write": None, "cache_read": None},
        "other": {"input": None, "output": 1.0, "cache_write": 1.0, "cache_read": 1.0}},
        "limits": {"daily_usd": None}}}), encoding="utf-8")
    ai = load_config()["ai"]
    assert ai["prices"]["claude-haiku-5-5"] == {"input": 0.10, "output": 9.0, "cache_write": 0.125, "cache_read": 0.01}
    assert ai["prices"]["other"]["input"] is None            # 기본값이 없는 모델은 그대로 null
    assert ai["limits"]["daily_usd"] is None                 # 한도 null은 '무제한' 그대로


def test_effort와_dev_log_설정_검증():
    from hub.ai import settings
    base = {"enabled": True, "models": {"light": "claude-haiku-5-5", "heavy": "claude-sonnet-5-5"}}
    settings.save({**base, "effort": {"light": "high"}, "dev_log": True})
    ai = load_config()["ai"]
    assert ai["effort"] == {"light": "high", "heavy": "medium"} and ai["dev_log"] is True
    assert settings.status()["dev_log"] is True
    with pytest.raises(ValueError):
        settings.save({**base, "effort": {"heavy": "ultra"}})
    settings.save(base)
    assert load_config()["ai"]["dev_log"] is False and load_config()["ai"]["effort"]["light"] == "high"


# ── dev_log ───────────────────────────────────────
def run_gateway(text="전화 010-0000-0000"):
    p, _ = provider(ok(reply("답변")))
    g = Gateway(key_getter=lambda: KEY, provider_factory=lambda k: p)
    return g.complete("planner", "light", [{"role": "user", "content": text}], system="시스템")


def test_dev_log_꺼짐이면_파일없음_켜지면_마스킹된_값만():
    run_gateway()
    assert not (platform_dir() / "ai-dev").exists()
    cfg = load_config()
    cfg["ai"]["dev_log"] = True
    save_config(cfg)
    run_gateway()
    files = list((platform_dir() / "ai-dev").rglob("*_planner.json"))
    assert len(files) == 1
    raw = files[0].read_text(encoding="utf-8")
    d = json.loads(raw)
    assert d["messages"][0]["content"] == "전화 [전화1]" and d["text"] == "답변" and d["system"] == "시스템"
    assert d["usage"]["input_tokens"] == 10 and "010-0000" not in raw and KEY not in raw
    run_gateway()
    assert len(list((platform_dir() / "ai-dev").rglob("*_planner*.json"))) == 2   # 같은 초여도 덮지 않음


def test_dev_log_쓰기_실패해도_호출은_성공(monkeypatch):
    cfg = load_config()
    cfg["ai"]["dev_log"] = True
    save_config(cfg)

    def boom():
        raise OSError("디스크")
    monkeypatch.setattr(G, "platform_dir", boom)
    assert run_gateway().text == "답변"


# ── 실제 API (--real-ai 일 때만) ─────────────────────
@pytest.mark.real_ai
def test_실제_API_light_짧은_호출과_스키마():
    from hub.ai import keys
    if not keys.get_key():
        pytest.skip("자격 증명 관리자에 키가 없음")
    r = G.complete("real_test", "light", [{"role": "user", "content": "한 단어로 인사해 주세요."}], max_tokens=1024)
    assert r.text.strip() and r.cost_usd > 0
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}
    r = G.complete("real_test", "light", [{"role": "user", "content": "n은 7입니다. JSON으로 답하세요."}],
                   schema=schema, max_tokens=1024)
    assert r.data["n"] == 7
