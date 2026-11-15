import json
from datetime import datetime, timedelta

import pytest

from hub import db
from hub.ai import schema as S
from hub.ai.gateway import AIError, Gateway, cost_usd
from hub.ai.mask import mask
from hub.ai.providers import FakeProvider, ProviderAuthError, ProviderNetworkError, ProviderReply
from hub.paths import config_path, load_config, save_config

PRICE = {"input": 1.0, "output": 5.0, "cache_write": 2.0, "cache_read": 0.5}


def setup_cfg(**ai):
    cfg = load_config()
    cfg["ai"]["prices"] = {m: dict(PRICE) for m in cfg["ai"]["models"].values()}
    for k, v in ai.items():
        cfg["ai"][k] = v
    save_config(cfg)


def gw(provider, key="k-test"):
    return Gateway(key_getter=lambda: key, provider_factory=lambda k: provider)


def ask(g, **kw):
    kw.setdefault("messages", [{"role": "user", "content": "안녕"}])
    return g.complete("test", kw.pop("tier", "light"), **kw)


def calls():
    with db.connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM ai_calls ORDER BY at")]


def test_등급_모델_매핑과_설정_변경():
    setup_cfg()
    p = FakeProvider(["a", "b"])
    g = gw(p)
    ask(g, tier="light")
    ask(g, tier="heavy")
    assert [c["model"] for c in p.calls] == ["claude-haiku-5-5", "claude-sonnet-5-5"]
    cfg = load_config()
    cfg["ai"]["models"]["light"] = "new-model"
    cfg["ai"]["prices"]["new-model"] = dict(PRICE)
    save_config(cfg)
    p2 = FakeProvider(["a"])
    ask(gw(p2), tier="light")
    assert p2.calls[0]["model"] == "new-model"


def test_키없음과_꺼짐은_off이고_공급자를_안_부름():
    setup_cfg()
    p = FakeProvider(["a"])
    with pytest.raises(AIError) as e:
        ask(gw(p, key=None))
    assert e.value.kind == "off"
    setup_cfg(enabled=False)
    with pytest.raises(AIError) as e:
        ask(gw(p))
    assert e.value.kind == "off" and not p.calls and not calls()
    from hub.ai import gateway
    with pytest.raises(AIError) as e:   # 기본 게이트웨이도 키가 없으면 off
        gateway.complete("x", "light", [{"role": "user", "content": "a"}])
    assert e.value.kind == "off"


SCHEMA = {"type": "object", "properties": {"n": {"type": "integer", "minimum": 0}, "k": {"enum": ["a", "b"]}},
          "required": ["n"], "additionalProperties": False}


def test_스키마_성공_재시도_성공_두번_실패():
    setup_cfg()
    r = ask(gw(FakeProvider(['{"n": 3}'])), schema=SCHEMA)
    assert r.data == {"n": 3} and not calls()[-1]["retried"]

    p = FakeProvider([ProviderReply('{"n": "x"}', 10, 1), ProviderReply('{"n": 1}', 10, 1)])
    r = ask(gw(p), schema=SCHEMA)
    assert r.data == {"n": 1} and len(p.calls) == 2 and r.input_tokens == 20
    assert "$.n" in p.calls[1]["messages"][-1]["content"]
    assert calls()[-1]["retried"] == 1

    p = FakeProvider(["아님", '{"n": -1}'])
    with pytest.raises(AIError) as e:
        ask(gw(p), schema=SCHEMA)
    assert e.value.kind == "format" and len(p.calls) == 2
    last = calls()[-1]
    assert last["ok"] == 0 and last["error_kind"] == "format"


def test_코드블록과_스키마_부분집합():
    assert S.parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert S.validate({"n": 1, "z": 1}, SCHEMA) == ["$.z: 허용되지 않는 항목입니다"]
    assert S.validate([1, "x"], {"type": "array", "items": {"type": "integer"}})
    assert S.validate(True, {"type": "integer"})
    with pytest.raises(ValueError):
        S.check_schema({"type": "object", "pattern": "x"})
    # 스키마 키워드 실수는 게이트웨이 밖에서 AIError(internal)
    setup_cfg()
    with pytest.raises(AIError) as e:
        ask(gw(FakeProvider(["{}"])), schema={"oneOf": []})
    assert e.value.kind == "internal"


def test_단가_null이면_budget():
    cfg = load_config()
    cfg["ai"]["models"]["light"] = "no-price-model"   # 기본 단가가 있는 모델은 null이어도 기본값을 쓴다
    cfg["ai"]["prices"]["no-price-model"] = {"input": None, "output": None, "cache_write": None, "cache_read": None}
    save_config(cfg)
    p = FakeProvider(["a"])
    with pytest.raises(AIError) as e:
        ask(gw(p))
    assert e.value.kind == "budget" and "단가" in e.value.message and not p.calls


def _insert(at, cost):
    db.record_ai_call(at=at, feature="f", tier="light", model="m", input_tokens=0, output_tokens=0,
                      cache_read_tokens=0, cache_write_tokens=0, cost_usd=cost, duration_ms=1, ok=True, retried=False)


def test_일_월_한도():
    setup_cfg()
    now = datetime.now()
    first = now.replace(day=1, hour=12)
    yesterday = now - timedelta(days=1)
    last_month = first - timedelta(days=3)
    _insert(last_month.isoformat(timespec="seconds"), 999)       # 지난달: 안 셈
    if yesterday.month == now.month:
        _insert(yesterday.isoformat(timespec="seconds"), 0.5)    # 어제 (이번 달 안): 일 한도엔 안 셈
    p = FakeProvider(["a", "b", "c"])
    ask(gw(p))   # 통과
    _insert(now.isoformat(timespec="seconds"), 1.0)
    with pytest.raises(AIError) as e:
        ask(gw(p))
    assert e.value.kind == "budget" and "내일" in e.value.message and "1" in e.value.message
    setup_cfg(limits={"daily_usd": 100.0, "monthly_usd": 1.0})
    with pytest.raises(AIError) as e:
        ask(gw(p))
    assert e.value.kind == "budget" and "다음 달 1일" in e.value.message
    assert len(p.calls) == 1


def test_마스킹_종류별():
    t, c = mask("전화 010-0000-0000, 02-000-0000, 01000000000 / 주민 900101-1000000 / 계좌 000-00-000000-0 / a@example.com", [])
    assert c == {"phone": 2, "rrn": 1, "account": 1, "email": 1, "name": 0}
    assert "[전화1]" in t and "[주민번호1]" in t and "[계좌1]" in t and "[이메일1]" in t
    assert "0000" not in t.replace("[", "")


def test_마스킹_같은값_같은번호_이름목록_전화는_계좌아님_날짜():
    t, c = mask("010-0000-0000 그리고 010 0000 0000", [])
    assert t == "[전화1] 그리고 [전화1]" and c["phone"] == 1
    t, c = mask("홍길동과 홍길 그리고 길동", ["홍길", "홍길동", ""])
    assert t == "[이름1]과 [이름2] 그리고 길동" and c["name"] == 2
    t, c = mask("홍길동", [])
    assert t == "홍길동"
    t, c = mask("연락 010-0000-0000", [])
    assert c["account"] == 0 and c["phone"] == 1
    t, c = mask("2026-11-15 방송, 2026-11-30 종료", [])
    assert t.startswith("2026-11-15") and sum(c.values()) == 0


def test_마스킹_게이트웨이_끄기와_기록():
    setup_cfg()
    msgs = [{"role": "user", "content": [{"type": "text", "text": "a@example.com 010-0000-0000"}]}]
    p = FakeProvider(["ok"])
    r = ask(gw(p), messages=msgs, system="시스템 010-0000-0000")
    sent = p.calls[0]["messages"][0]["content"][0]["text"]
    assert sent == "[이메일1] [전화1]" and msgs[0]["content"][0]["text"].startswith("a@")
    assert p.calls[0]["system"] == "시스템 010-0000-0000"
    assert r.masked["email"] == 1 and r.masked["phone"] == 1
    row = calls()[-1]
    assert json.loads(row["masked"]) == r.masked
    assert "example" not in json.dumps(row) and "k-test" not in json.dumps(row)

    setup_cfg(masking={"enabled": False, "names": []})
    p = FakeProvider(["ok"])
    ask(gw(p), messages=msgs)
    assert "example.com" in p.calls[0]["messages"][0]["content"][0]["text"]


def test_공급자_오류_변환과_기록():
    setup_cfg()
    for exc, kind in ((ProviderAuthError("k-test"), "auth"), (ProviderNetworkError("x"), "network"),
                      (RuntimeError("k-test 터짐"), "internal")):
        with pytest.raises(AIError) as e:
            ask(gw(FakeProvider([exc])))
        assert e.value.kind == kind and "k-test" not in e.value.message
    rows = calls()
    assert [r["error_kind"] for r in rows] == ["auth", "network", "internal"] and not any(r["ok"] for r in rows)
    with pytest.raises(AIError) as e:   # 공급자 만들기 실패도 AIError
        Gateway(lambda: "k", lambda k: 1 / 0).complete("t", "light", [{"role": "user", "content": "a"}])
    assert e.value.kind == "internal"


def test_결과_비용과_기록행():
    setup_cfg()
    r = ask(gw(FakeProvider([ProviderReply("답", 1_000_000, 100_000, 200_000, 100_000)])))
    assert r.text == "답" and r.data is None and r.model == "claude-haiku-5-5"
    assert r.cost_usd == pytest.approx(1.0 + 0.5 + 0.1 + 0.2)
    assert cost_usd(PRICE, 1_000_000, 0) == 1.0
    row = calls()[-1]
    assert row["feature"] == "test" and row["tier"] == "light" and row["ok"] == 1
    assert row["cache_read_tokens"] == 200_000 and row["cost_usd"] == pytest.approx(r.cost_usd)
    assert "답" not in json.dumps(row, ensure_ascii=False) and "안녕" not in json.dumps(row, ensure_ascii=False)


def test_설정_깊은_병합():
    config_path().write_text(json.dumps({"stale_days": 5, "ai": {"limits": {"daily_usd": 2.0},
                                         "prices": {"claude-haiku-5-5": {"input": 1.0}}}}), encoding="utf-8")
    ai = load_config()["ai"]
    assert ai["limits"] == {"daily_usd": 2.0, "monthly_usd": 20.0}
    assert ai["prices"]["claude-haiku-5-5"] == {"input": 1.0, "output": 0.5, "cache_write": 0.125, "cache_read": 0.01}
    assert ai["models"]["heavy"] == "claude-sonnet-5-5" and ai["masking"]["enabled"] is True
    assert load_config()["stale_days"] == 5
    saved = save_config({"ai": {"enabled": False}})
    assert saved["ai"]["enabled"] is False and saved["ai"]["limits"]["daily_usd"] == 1.0
