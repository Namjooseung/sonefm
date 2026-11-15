"""테스트용 가짜 앱 (계약 v1). 입력의 mode로 동작을 고른다.

ok / partial / error: 그 상태로 결과를 쓰고 종료 코드 0 / 2 / 1
sleep: 오래 멈춤(시간 초과 확인) / garbage: 결과 없이 아무 글자 / stdout: 결과 파일 없이 표준출력만
liar: 상태는 ok인데 종료 코드 1 / check-db 명령: db 파일 내용에 bad가 있으면 실패
"""
import json
import sys
import time
from pathlib import Path

argv = sys.argv[1:]
command = argv[0]
inp = json.loads(Path(argv[argv.index("--input") + 1]).read_text(encoding="utf-8"))
res = Path(argv[argv.index("--result") + 1])
mode = inp.get("mode", "ok")
if command == "check-db":
    mode = "error" if b"bad" in Path(inp["db"]).read_bytes() else "ok"

print("로그 한 줄", file=sys.stderr, flush=True)
for pct in (10, 50, 90):
    print(f"@progress {pct} 단계 {pct}", file=sys.stderr, flush=True)

if mode == "sleep":
    time.sleep(60)
if mode == "garbage":
    print("결과가 아님")
    sys.exit(0)

status = {"liar": "ok", "stdout": "ok"}.get(mode, mode)
result = {"status": status, "count": 0 if status == "error" else 1,
          "data": {"echo": inp.get("echo"), "n": inp.get("n"), "command": command, "input": inp},
          "outputs": [str(res.parent / "out.txt")],
          "skipped": [{"item": "x", "reason": "가짜 건너뜀"}] if status == "partial" else [],
          "message": f"가짜 {command} {status}"}
line = json.dumps(result, ensure_ascii=False)
if mode != "stdout":
    res.write_text(line, encoding="utf-8")
print(line)
sys.exit(1 if mode == "liar" else {"ok": 0, "partial": 2, "error": 1}[status])
