"""1b: 키 보관(keyring 메모리 백엔드)과 AI 설정 화면 API."""
import json
import logging

import pytest

from hub import db, paths
from hub.ai import keys
from hub.api import Api

KEY = "sk-ant-FAKE-0123456789abcdefghij"


def test_키_저장_읽기_삭제():
    assert keys.get_key() is None and not keys.has_key()
    keys.save_key(f"  {KEY}  ")
    assert keys.get_key() == KEY and keys.has_key()
    keys.delete_key()
    assert keys.get_key() is None
    keys.delete_key()   # 이미 없어도 오류 없음


@pytest.mark.parametrize("bad", ["", "   ", "a" * 19, "a" * 501, "abc def" + "x" * 20, "a" * 30 + "\nb"])
def test_키_거절(bad):
    with pytest.raises(keys.KeyRejected):
        keys.save_key(bad)
    assert keys.get_key() is None


def test_백엔드_오류는_키없음_앱_안죽음(memory_keyring, monkeypatch, caplog):
    def boom(*a):
        raise OSError("secret " + KEY)
    monkeypatch.setattr(memory_keyring, "get_password", boom)
    monkeypatch.setattr(memory_keyring, "set_password", boom)
    with caplog.at_level(logging.WARNING, logger="hub.ai"):
        assert keys.get_key() is None
        with pytest.raises(RuntimeError) as e:
            keys.save_key(KEY)
    assert KEY not in str(e.value) and KEY not in caplog.text and "OSError" in caplog.text
    r = Api().ai_save_key(KEY)
    assert r["ok"] is False and KEY not in json.dumps(r)


def test_환경변수는_읽지_않음(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    assert keys.get_key() is None


def full(**over):
    ai = {"enabled": True, "models": {"light": "m-light", "heavy": "m-heavy"},
          "prices": {"m-light": {"input": "1", "output": "5", "cache_write": "", "cache_read": 0.5}},
          "limits": {"daily_usd": "", "monthly_usd": "20"},
          "masking": {"enabled": True, "names": " 홍길동 \n\n김철수\n  "}}
    return {**ai, **over}


def test_설정_저장_검증과_정리():
    a = Api()
    assert a.ai_save_settings(full())["ok"]
    ai = paths.load_config()["ai"]
    assert ai["prices"]["m-light"] == {"input": 1.0, "output": 5.0, "cache_write": None, "cache_read": 0.5}
    assert ai["prices"]["m-heavy"] == {"input": None, "output": None, "cache_write": None, "cache_read": None}
    assert ai["limits"] == {"daily_usd": None, "monthly_usd": 20.0}
    assert ai["masking"] == {"enabled": True, "names": ["홍길동", "김철수"]}
    st = a.ai_status()
    assert st["models"] == {"light": "m-light", "heavy": "m-heavy"} and set(st["prices"]) == {"m-light", "m-heavy"}
    for bad in (full(limits={"daily_usd": "-1"}), full(limits={"monthly_usd": "abc"}),
                full(prices={"m-light": {"input": "-0.1"}}), full(prices={"m-light": {"input": "nan"}}),
                full(models={"light": "", "heavy": "x"}), full(models={"light": "a b", "heavy": "x"})):
        r = a.ai_save_settings(bad)
        assert r["ok"] is False and r["errors"]
    assert paths.load_config()["ai"]["limits"]["monthly_usd"] == 20.0   # 거절된 저장은 반영 안 됨
    assert a.ai_save_settings(None)["ok"] is False


def test_기존_설정_저장이_ai를_유지():
    a = Api()
    a.ai_save_settings(full())
    before = paths.load_config()["ai"]
    assert a.save_settings({"stale_days": 5, "scan_installed": True})["ok"]
    assert paths.load_config()["ai"] == before and paths.load_config()["stale_days"] == 5


def test_ai_test는_off이고_예외없음():
    a = Api()
    r = a.ai_test()
    assert r["ok"] is False and r["kind"] == "off"
    keys.save_key(KEY)
    r = a.ai_test()
    assert r["ok"] is False and r["kind"] == "off" and "준비되지 않았습니다" in r["errors"][0] and KEY not in json.dumps(r)


def test_사용량_합계():
    a = Api()
    from datetime import datetime
    for cost in (0.25, 0.5):
        db.record_ai_call(feature="t", tier="light", model="m", input_tokens=1, output_tokens=1, cache_read_tokens=0,
                          cache_write_tokens=0, cost_usd=cost, duration_ms=1, ok=True, retried=False)
    db.record_ai_call(at="2000-01-01T00:00:00", feature="t", tier="light", model="m", input_tokens=1, output_tokens=1,
                      cache_read_tokens=0, cache_write_tokens=0, cost_usd=9, duration_ms=1, ok=True, retried=False)
    u = a.ai_status()["usage"]
    assert u == {"today_usd": 0.75, "month_usd": 0.75, "month_calls": 2}


def test_키_값이_어디에도_없음(tmp_path):
    a = Api()
    assert a.ai_save_key(KEY)["ok"]
    a.ai_save_settings(full())
    st = a.ai_status()
    assert st["has_key"] is True and KEY not in json.dumps(st) and KEY[-4:] not in json.dumps(st).replace("claude", "")
    a.ai_test()
    assert a.ai_delete_key()["ok"] and a.ai_status()["has_key"] is False
    for f in (tmp_path / "SoneFM").rglob("*"):
        if f.is_file():
            assert KEY.encode() not in f.read_bytes(), f
