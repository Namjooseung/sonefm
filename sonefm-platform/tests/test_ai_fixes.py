"""LLM 게이트웨이 검토 결함 4건: 거절·잘림 비용 기록, 기록 실패 보관, 타임아웃, 짧은 이름 마스킹."""
import pytest

from hub import db
from hub.ai import gateway as G
from hub.ai import settings
from hub.ai.claude import ClaudeProvider
from hub.ai.gateway import AIError, Gateway
from hub.ai.mask import mask
from hub.ai.providers import FakeProvider, ProviderFormatError, ProviderReply
from hub.paths import load_config, save_config
from tests.test_ai_claude import KEY, USER, mock_client, ok, provider, reply

PRICE = {"input": 1.0, "output": 5.0, "cache_write": 2.0, "cache_read": 0.5}
USAGE = {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 10, "cache_creation_input_tokens": 20}
EXPECT = (100 + 250 + 5 + 40) / 1_000_000


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    G._pending.clear()
    monkeypatch.setattr(G, "RECORD_RETRY_WAIT_S", 0)
    cfg = load_config()
    cfg["ai"]["prices"] = {m: dict(PRICE) for m in cfg["ai"]["models"].values()}
    save_config(cfg)
    yield
    G._pending.clear()


def rows():
    with db.connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM ai_calls ORDER BY at")]


def gw(p):
    return Gateway(key_getter=lambda: "k", provider_factory=lambda k: p)


# ── 1. 거절·잘림 ──────────────────────────────────
@pytest.mark.parametrize("stop", ["refusal", "max_tokens"])
def test_거절_잘림도_토큰과_비용이_기록됨(stop):
    p, _ = provider(ok(reply(stop=stop, usage=USAGE)))
    with pytest.raises(AIError) as e:
        gw(p).complete("t", "light", USER)
    assert e.value.kind == "format"
    r = rows()[-1]
    assert (r["input_tokens"], r["output_tokens"], r["cache_read_tokens"], r["cache_write_tokens"]) == (100, 50, 10, 20)
    assert r["cost_usd"] == pytest.approx(EXPECT) and r["ok"] == 0 and r["error_kind"] == "format"


def test_공급자_예외에_usage가_실림():
    p, _ = provider(ok(reply(stop="max_tokens", usage=USAGE)))
    with pytest.raises(ProviderFormatError) as e:
        p.complete("m", None, USER, None, 10)
    assert e.value.usage == (100, 50, 10, 20)


def test_스키마_재시도에서_둘째가_잘려도_첫째와_합산():
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}
    fp = FakeProvider([ProviderReply("아님", 10, 5),
                       ProviderFormatError("잘림", (7, 3, 0, 0))])
    with pytest.raises(AIError):
        gw(fp).complete("t", "light", USER, schema=schema)
    r = rows()[-1]
    assert (r["input_tokens"], r["output_tokens"]) == (17, 8) and r["retried"] == 1
    assert r["cost_usd"] == pytest.approx((17 + 40) / 1_000_000)


def test_거절_비용이_한도에_반영됨():
    cfg = load_config()
    cfg["ai"]["limits"]["daily_usd"] = 0.0005
    save_config(cfg)
    fp = FakeProvider([ProviderFormatError("거절", (1000, 0, 0, 0))])   # 0.001달러
    with pytest.raises(AIError) as e:
        gw(fp).complete("t", "light", USER)
    assert e.value.kind == "format"
    with pytest.raises(AIError) as e:
        gw(FakeProvider(["a"])).complete("t", "light", USER)
    assert e.value.kind == "budget"


# ── 2. 기록 실패 보관 ─────────────────────────────
def test_기록_실패_재시도_후_보관_한도_반영_다음_호출에_기록(monkeypatch, caplog):
    cfg = load_config()
    cfg["ai"]["limits"]["daily_usd"] = 0.0015
    save_config(cfg)
    real = db.record_ai_call
    state = {"fail": True, "tries": 0}

    def flaky(**f):
        if state["fail"]:
            state["tries"] += 1
            raise RuntimeError("database is locked " + KEY)
        return real(**f)
    monkeypatch.setattr(db, "record_ai_call", flaky)

    fp = FakeProvider([ProviderReply("첫째", 1000, 0), "둘째", "셋째"])   # 첫 호출 0.001달러
    r = gw(fp).complete("t", "light", USER)
    assert r.text == "첫째"                       # 응답은 그대로 돌려준다
    assert state["tries"] == 2 and len(G._pending) == 1 and not rows()   # 한 번 다시 시도, 그래도 실패 → 보관
    assert KEY not in caplog.text

    # 다음 한도 확인: DB는 아직 안 되지만 보관분이 더해진다. 0.001 + 0.001 > 0.0015
    fp.calls.clear()
    fp._replies = [ProviderReply("둘째", 1000, 0)]
    assert gw(fp).complete("t", "light", USER).text == "둘째"
    assert len(G._pending) == 2
    with pytest.raises(AIError) as e:
        gw(fp).complete("t", "light", USER)
    assert e.value.kind == "budget"

    # DB가 돌아오면 다음 호출 때 보관분을 기록한다
    state["fail"] = False
    cfg = load_config()
    cfg["ai"]["limits"]["daily_usd"] = None
    save_config(cfg)
    gw(FakeProvider(["셋째"])).complete("t", "light", USER)
    assert not G._pending and len(rows()) == 3
    assert sum(r["cost_usd"] for r in rows()) == pytest.approx(0.002, abs=1e-9)


# ── 3. 타임아웃 ───────────────────────────────────
def test_연결_타임아웃은_짧고_재시도는_1회():
    p = ClaudeProvider(KEY, http_client=mock_client(ok()))
    t = p._client.timeout
    assert t.connect == 10.0 and t.read == 120.0 and p._client.max_retries == 1


# ── 4. 짧은 이름 ──────────────────────────────────
@pytest.mark.parametrize("names", [["김"], ["김철수", " 박 "], "김철수\n이"])
def test_한글자_이름_설정_저장_거절(names):
    base = {"enabled": True, "models": {"light": "claude-haiku-5-5", "heavy": "claude-sonnet-5-5"}}
    with pytest.raises(ValueError, match="2글자 이상"):
        settings.save({**base, "masking": {"enabled": True, "names": names}})
    assert load_config()["ai"]["masking"]["names"] == []


def test_두글자_이름은_저장됨():
    base = {"enabled": True, "models": {"light": "claude-haiku-5-5", "heavy": "claude-sonnet-5-5"}}
    settings.save({**base, "masking": {"enabled": True, "names": ["김 철", "홍길동"]}})
    assert load_config()["ai"]["masking"]["names"] == ["김 철", "홍길동"]


def test_마스킹은_한글자_이름을_무시():
    out, c = mask("김밥을 먹은 김철수", ["김", "김철수"])
    assert out == "김밥을 먹은 [이름1]" and c["name"] == 1
    assert mask("가나다", ["가", " "])[0] == "가나다"
