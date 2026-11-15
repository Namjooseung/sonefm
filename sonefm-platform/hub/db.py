"""플랫폼 DB (Documents\\SoneFM\\platform\\platform.db): 실행 기록, 업무 처리 상태, 직접 입력 업무, 키-값.

나중에 방송일지·보고서가 '언제 무엇을 처리했나'를 여기서 읽는다. 저널 모드는 기본값(DELETE).
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator

from hub.paths import platform_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, task_key TEXT, task_text TEXT, task_date TEXT, recipe_id TEXT,
    started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL, message TEXT,
    steps TEXT, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_task ON runs(task_key);
CREATE TABLE IF NOT EXISTS task_marks (
    task_key TEXT PRIMARY KEY, status TEXT NOT NULL, note TEXT, task_text TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS manual_tasks (
    id TEXT PRIMARY KEY, date TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ai_calls (
    id TEXT PRIMARY KEY, at TEXT NOT NULL, feature TEXT, tier TEXT, model TEXT,
    input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER,
    cost_usd REAL, duration_ms INTEGER, ok INTEGER, error_kind TEXT, retried INTEGER, masked TEXT
);
CREATE INDEX IF NOT EXISTS ai_calls_at ON ai_calls(at);
"""


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(platform_dir() / "platform.db", timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def start_run(task: dict[str, Any], recipe_id: str) -> str:
    rid = uuid.uuid4().hex
    with connect() as c:
        c.execute("INSERT INTO runs (id, task_key, task_text, task_date, recipe_id, started_at, status, created_at)"
                  " VALUES (?, ?, ?, ?, ?, ?, 'running', ?)",
                  (rid, task.get("key"), task.get("text"), task.get("date"), recipe_id, now(), now()))
    return rid


def finish_run(run_id: str, result: dict[str, Any]) -> None:
    with connect() as c:
        c.execute("UPDATE runs SET finished_at = ?, status = ?, message = ?, steps = ? WHERE id = ?",
                  (now(), result["status"], result.get("message"),
                   json.dumps(result.get("steps", []), ensure_ascii=False, default=str), run_id))


def recent_runs(limit: int = 30) -> list[dict[str, Any]]:
    with connect() as c:
        rows = c.execute("SELECT id, task_key, task_text, task_date, recipe_id, started_at, finished_at, status, message"
                         " FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def last_runs_by_task() -> dict[str, dict[str, Any]]:
    with connect() as c:
        rows = c.execute("SELECT task_key, status, message, started_at FROM runs r WHERE started_at ="
                         " (SELECT MAX(started_at) FROM runs WHERE task_key = r.task_key)").fetchall()
    return {r["task_key"]: dict(r) for r in rows}


def mark(task_key: str, status: str | None, task_text: str = "", note: str = "") -> None:
    """status None이면 표시를 지운다."""
    with connect() as c:
        if status is None:
            c.execute("DELETE FROM task_marks WHERE task_key = ?", (task_key,))
        else:
            c.execute("INSERT INTO task_marks (task_key, status, note, task_text, created_at, updated_at)"
                      " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(task_key) DO UPDATE SET status = excluded.status,"
                      " note = excluded.note, updated_at = excluded.updated_at",
                      (task_key, status, note, task_text, now(), now()))


def marks() -> dict[str, str]:
    with connect() as c:
        return {r["task_key"]: r["status"] for r in c.execute("SELECT task_key, status FROM task_marks")}


def add_manual(day: str, text: str) -> dict[str, Any]:
    row = {"id": uuid.uuid4().hex, "date": day, "text": text.strip(), "created_at": now()}
    with connect() as c:
        c.execute("INSERT INTO manual_tasks (id, date, text, created_at) VALUES (:id, :date, :text, :created_at)", row)
    return row


def delete_manual(task_id: str) -> None:
    with connect() as c:
        c.execute("DELETE FROM manual_tasks WHERE id = ?", (task_id,))


def manual_for(day: str) -> list[dict[str, Any]]:
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM manual_tasks WHERE date = ? ORDER BY created_at", (day,))]


def get_meta(key: str, default: Any = None) -> Any:
    with connect() as c:
        r = c.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return json.loads(r["value"]) if r else default


def record_ai_call(at: str | None = None, masked: dict[str, int] | None = None, **f: Any) -> None:
    """AI 호출 한 건. 프롬프트·응답 원문은 받지도 저장하지도 않는다."""
    with connect() as c:
        c.execute("INSERT INTO ai_calls (id, at, feature, tier, model, input_tokens, output_tokens, cache_read_tokens,"
                  " cache_write_tokens, cost_usd, duration_ms, ok, error_kind, retried, masked)"
                  " VALUES (:id, :at, :feature, :tier, :model, :input_tokens, :output_tokens, :cache_read_tokens,"
                  " :cache_write_tokens, :cost_usd, :duration_ms, :ok, :error_kind, :retried, :masked)",
                  {"error_kind": None, **f, "id": uuid.uuid4().hex, "at": at or now(), "ok": int(f["ok"]),
                   "retried": int(f["retried"]), "masked": json.dumps(masked or {}, ensure_ascii=False)})


def ai_cost_sum(start: str, end: str) -> float:
    """start <= at < end (ISO 로컬 시각 문자열) 구간의 cost_usd 합계."""
    with connect() as c:
        return c.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM ai_calls WHERE at >= ? AND at < ?",
                         (start, end)).fetchone()[0]


def ai_call_count(start: str, end: str) -> int:
    with connect() as c:
        return c.execute("SELECT COUNT(*) FROM ai_calls WHERE at >= ? AND at < ?", (start, end)).fetchone()[0]


def set_meta(key: str, value: Any) -> None:
    with connect() as c:
        c.execute("INSERT INTO meta (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET"
                  " value = excluded.value, updated_at = excluded.updated_at",
                  (key, json.dumps(value, ensure_ascii=False), now()))
