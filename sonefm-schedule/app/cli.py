"""플랫폼용 명령줄 (SoneFM 앱 계약 v1, 상위 폴더 APP_CONTRACT.md).

    python app/main.py --cli lookup --date 2026-11-16 --hour 14 [--db 사본.db]
    python app/main.py --cli month --year 2026 --month 11
    python app/main.py --cli set-prep --date 2026-11-16 --hour 14 --status 송출완료
    python app/main.py --cli check-db --db 받은파일.db
    (플랫폼은 --input 입력.json --result 결과.json 으로 부른다)

결과는 --result 파일과 표준출력 마지막 줄에 JSON 한 줄. 종료 코드 0 성공 / 1 실패.
--db를 주지 않으면 config.json의 DB(이 PC의 편성 데이터)를 쓴다.
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import date
from pathlib import Path
from typing import Any

from app.config import load_config
from app.db.connection import open_db
from app.repository import schedule_repo
from app.services import cell_edit, data
from app.services import schedule as schedule_svc

EXIT = {"ok": 0, "partial": 2, "error": 1}


def _result(status: str, message: str, data_: dict | None = None, count: int = 0) -> dict[str, Any]:
    return {"status": status, "count": count, "data": data_ or {}, "outputs": [], "skipped": [], "message": message}


def _error(message: str) -> dict[str, Any]:
    return _result("error", message)


def _db(args: dict[str, Any]) -> Path | None:
    if args.get("db"):
        return Path(args["db"])
    p = Path(load_config().db_path)
    return p if p.exists() else None


def _date_hour(args: dict[str, Any]) -> tuple[date, int] | None:
    try:
        d, h = date.fromisoformat(str(args["date"])), int(args["hour"])
    except (KeyError, TypeError, ValueError):
        return None
    return (d, h) if 6 <= h <= 22 else None


def lookup(args: dict[str, Any]) -> dict[str, Any]:
    dh = _date_hour(args)
    if dh is None:
        return _error("날짜(YYYY-MM-DD)와 시각(6~22)을 확인해 주세요.")
    d, h = dh
    db = _db(args)
    if db is None or not db.exists():
        return _error("편성 데이터 파일이 없습니다.")
    with open_db(db) as conn:
        m = schedule_svc.get_month(conn, d.year, d.month)   # 회차는 반드시 get_month 계산을 거친다
        cell = m["cells"].get(f"{d.isoformat()}|{h}")
        status = schedule_repo.month_status_of(conn, f"{d.year:04d}-{d.month:02d}") or schedule_svc.STATUS_NONE
    if cell is None:
        return _error(f"{d.isoformat()} {h}시는 편성표에 빈칸입니다.")
    show = cell["episode"] is not None and cell["show_episode"] and not cell["is_fixed"]
    label = f"{cell['name']} {cell['episode']}회" if show else cell["name"]
    out = {"date": d.isoformat(), "hour": h, "name": cell["name"], "episode": cell["episode"] if show else None,
           "kind": cell["kind"], "label": label, "category": cell["category"], "program_id": cell["program_id"],
           "prep_status": cell["prep_status"], "month_status": status}
    msg = f"{d.isoformat()} {h}시: {label} ({cell['kind']})"
    if status != "확정" and status != "마감":
        msg += f" · {d.month}월 편성은 아직 '{status}' 상태입니다"
    return _result("ok", msg, out, 1)


def month(args: dict[str, Any]) -> dict[str, Any]:
    db = _db(args)
    if db is None or not db.exists():
        return _error("편성 데이터 파일이 없습니다.")
    with open_db(db) as conn:
        m = schedule_svc.get_month(conn, args.get("year"), args.get("month"))
    if not m.get("ok"):
        return _error(" ".join(m.get("errors", [])) or "월 편성을 읽지 못했습니다.")
    return _result("ok", f"{m['year']}년 {m['month']}월 편성 ({m['status']})", m, len(m["cells"]))


def set_prep(args: dict[str, Any]) -> dict[str, Any]:
    db = _db(args)
    if db is None or not db.exists():
        return _error("편성 데이터 파일이 없습니다.")
    with open_db(db) as conn:
        r = cell_edit.set_prep(conn, args.get("date"), args.get("hour"), str(args.get("status", "")))
        if not r.get("ok"):
            conn.rollback()
            return _error(" ".join(r.get("errors", [])) or "준비 상태를 바꾸지 못했습니다.")
    return _result("ok", r["message"], {"date": args.get("date"), "hour": args.get("hour"),
                                        "prep_status": args.get("status")}, 1)


def check_db(args: dict[str, Any]) -> dict[str, Any]:
    if not args.get("db"):
        return _error("검사할 데이터 파일(db)을 주세요.")
    problem = data.check_db_file(args["db"])
    if problem:
        return _error(problem)
    data._migrate(args["db"])   # noqa: SLF001  받은 사본을 이 버전 형식으로 맞춘다 (원본이 아니라 사본에만 쓴다)
    with open_db(args["db"]) as conn:
        statuses = schedule_repo.month_statuses(conn, date.today().year)
    return _result("ok", "편성 데이터 파일 확인", {"month_statuses": statuses}, 1)


COMMANDS = {"lookup": lookup, "month": month, "set-prep": set_prep, "check-db": check_db}


def _emit(result: dict[str, Any], result_path: str | None) -> None:
    line = json.dumps(result, ensure_ascii=False, default=str)
    if result_path:
        Path(result_path).write_text(line, encoding="utf-8")
    if sys.stdout is not None:   # 콘솔 없는 exe에서는 표준출력이 없다 → 결과 파일이 기준
        try:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
        except (OSError, ValueError):
            pass   # 화면용 exe에서 처리 안 된 예외는 오류 창을 띄우고 멈춘다 → 출력 실패도 삼킨다


def main(argv: list[str] | None = None) -> int:
    # 설치판 exe는 PYTHONIOENCODING을 무시하고 cp949로 연다 → 결과 JSON(UTF-8) 출력 전에 맞춘다
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(prog="SoneFM --cli", description="SoneFM 편성표 명령줄")
    p.add_argument("command", choices=sorted(COMMANDS))
    p.add_argument("--input", help="입력 JSON 파일")
    p.add_argument("--result", help="결과 JSON을 쓸 파일")
    for name in ("db", "date", "hour", "year", "month", "status"):
        p.add_argument(f"--{name}")
    ns = p.parse_args(argv)

    args: dict[str, Any] = {}
    try:
        if ns.input:
            args.update(json.loads(Path(ns.input).read_text(encoding="utf-8")))
        args.update({k: v for k, v in vars(ns).items()
                     if v is not None and k not in ("command", "input", "result")})
        result = COMMANDS[ns.command](args)
    except Exception as e:  # noqa: BLE001  어떤 오류든 계약 형식으로 돌려준다
        if sys.stderr is not None:
            traceback.print_exc()
        result = _error(f"처리 중 오류: {e}")
    _emit(result, ns.result)
    return EXIT[result["status"]]
