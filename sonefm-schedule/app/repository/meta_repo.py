"""DB 메타 정보 조회."""
from __future__ import annotations

import sqlite3


def schema_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def table_names(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def schedule_counts(conn: sqlite3.Connection) -> tuple[int, int, str | None]:
    """(프로그램 수, 편성 칸 수, 가장 늦은 칸 날짜). 읽기만 한다."""
    programs = int(conn.execute("SELECT COUNT(*) FROM programs").fetchone()[0])
    cells, max_date = conn.execute("SELECT COUNT(*), MAX(date) FROM schedule_cells").fetchone()
    return programs, int(cells), max_date


def journal_mode(conn: sqlite3.Connection) -> str:
    return str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
