"""공통 실행기: 매니페스트만 보고 앱 명령을 실행한다 (APP_CONTRACT.md 3~7절).

    <cli...> <명령> --input 입력.json --result 결과.json
결과 파일 → 표준출력 마지막 줄 순으로 결과 JSON을 찾고, 종료 코드와 상태 중 더 나쁜 쪽을 쓴다.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections import deque
from pathlib import Path
from typing import Any, Callable

from hub.registry import App

EXIT_STATUS = {0: "ok", 2: "partial"}
RANK = {"ok": 0, "partial": 1, "error": 2}
DEFAULT_TIMEOUT = 600
LOG_TAIL = 30

ProgressFn = Callable[[int, str], None]


def worse(a: str, b: str) -> str:
    return a if RANK[a] >= RANK[b] else b


def _error(message: str, **extra: Any) -> dict[str, Any]:
    return {"status": "error", "count": 0, "data": {}, "outputs": [], "skipped": [], "message": message, **extra}


def _kill_tree(p: subprocess.Popen) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)
    else:
        p.kill()


def _parse(text: str) -> dict[str, Any] | None:
    try:
        r = json.loads(text)
    except ValueError:
        return None
    return r if isinstance(r, dict) and r.get("status") in RANK else None


def run_command(app: App, command: str, args: dict[str, Any], workdir: Path,
                on_progress: ProgressFn | None = None, timeout: float | None = None) -> dict[str, Any]:
    """앱 명령 하나를 실행하고 계약 형식의 결과(dict)를 돌려준다. 예외를 던지지 않는다."""
    if app.error or not app.cli:
        return _error(app.error or "실행할 수 없는 앱입니다.")
    spec = app.command(command)
    if spec is None:
        return _error(f"{app.name}에 '{command}' 명령이 없습니다. 앱을 업데이트하세요.")
    timeout = timeout or spec.get("timeout") or DEFAULT_TIMEOUT

    workdir.mkdir(parents=True, exist_ok=True)
    inp, res = workdir / f"{command}.input.json", workdir / f"{command}.result.json"
    inp.write_text(json.dumps(args, ensure_ascii=False), encoding="utf-8")
    res.unlink(missing_ok=True)
    cmd = [*app.cli, command, "--input", str(inp), "--result", str(res)]
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        p = subprocess.Popen(cmd, cwd=app.dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             stdin=subprocess.DEVNULL, env=env, creationflags=flags)
    except OSError as e:
        return _error(f"{app.name}을(를) 실행하지 못했습니다: {e}")

    log: deque[str] = deque(maxlen=LOG_TAIL)
    out: list[str] = []

    def read_err() -> None:
        for raw in p.stderr:
            line = raw.decode("utf-8", "replace").rstrip()
            if line.startswith("@progress "):
                parts = line.split(" ", 2)
                try:
                    pct = max(0, min(100, int(parts[1])))
                except (IndexError, ValueError):
                    continue
                if on_progress:
                    on_progress(pct, parts[2] if len(parts) > 2 else "")
            elif line:
                log.append(line)

    def read_out() -> None:
        out.append(p.stdout.read().decode("utf-8", "replace"))

    readers = [threading.Thread(target=f, daemon=True) for f in (read_err, read_out)]
    for t in readers:
        t.start()
    try:
        code = p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(p)
        p.wait()
        return _error(f"시간 초과: {app.name}이(가) {int(timeout)}초 안에 끝나지 않아 멈췄습니다.", log=list(log))
    for t in readers:
        t.join(timeout=5)

    result = _parse(res.read_text(encoding="utf-8")) if res.is_file() else None
    if result is None:
        lines = [l for l in "".join(out).splitlines() if l.strip()]
        result = _parse(lines[-1]) if lines else None
    if result is None:
        tail = "\n".join(list(log)[-5:]) or "(로그 없음)"
        return _error(f"{app.name}이(가) 결과를 남기지 않고 끝났습니다 (종료 코드 {code}).\n{tail}",
                      exit_code=code, log=list(log))

    status = worse(result["status"], EXIT_STATUS.get(code, "error"))
    return {"status": status, "count": int(result.get("count") or 0), "data": result.get("data") or {},
            "outputs": list(result.get("outputs") or []), "skipped": list(result.get("skipped") or []),
            "message": str(result.get("message") or ""), "exit_code": code, "log": list(log)}
