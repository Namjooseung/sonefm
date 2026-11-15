"""업무일지 받기: 국장의 구글 시트 → 업무 목록 (PLATFORM_DESIGN.md 2절).

구글 API 없이 읽는다: 드라이브 데스크톱 앱의 .gsheet 바로가기(시트 id) 또는 시트 링크 →
'링크가 있는 모든 사용자' 공유 시트를 xlsx로 내려받는다. 사람이 내려받은 .xlsx/.csv도 읽는다.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable

EXPORT_URL = "https://docs.google.com/spreadsheets/d/{id}/export?format=xlsx"
HEADER_WORDS = {"날짜", "일자", "업무", "업무내용", "업무 내용", "내용", "담당", "담당자", "비고", "상태", "완료", "요일"}
SHEET_ID = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]{20,})")
DATE_PATTERNS = [
    (re.compile(r"^(\d{4})[-./](\d{1,2})[-./](\d{1,2})\.?(\s*\(.\))?$"), ("y", "m", "d")),
    (re.compile(r"^(\d{1,2})[/.](\d{1,2})\.?(\s*\(.\))?$"), ("m", "d")),
    (re.compile(r"^(?:(\d{4})년\s*)?(\d{1,2})월\s*(\d{1,2})일(\s*\(.\))?$"), ("y?", "m", "d")),
]


class WorklogError(Exception):
    pass


@dataclass
class Task:
    key: str
    date: str | None      # YYYY-MM-DD 또는 None(날짜 없는 줄)
    text: str
    source: str = "worklog"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def task_key(d: date | None, text: str) -> str:
    return hashlib.sha1(f"{d.isoformat() if d else ''}|{text}".encode("utf-8")).hexdigest()[:16]


def parse_date(value: Any, today: date) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    s = value.strip()
    for pat, parts in DATE_PATTERNS:
        m = pat.match(s)
        if not m:
            continue
        g = dict(zip([p.rstrip("?") for p in parts], m.groups()))
        try:
            if g.get("y"):
                return date(int(g["y"]), int(g["m"]), int(g["d"]))
            d = date(today.year, int(g["m"]), int(g["d"]))
        except ValueError:
            return None
        # 연도 없는 날짜: 반년 넘게 앞이면 작년 것으로 본다 (12월 업무일지를 1월에 읽을 때)
        return date(d.year - 1, d.month, d.day) if d - today > timedelta(days=183) else d
    return None


def parse_rows(rows: Iterable[list[Any]], today: date) -> list[Task]:
    """(가) 날짜 열 + 업무 / (나) 날짜 줄 아래 업무 줄 / (다) 날짜 없는 업무 줄을 모두 읽는다."""
    tasks: list[Task] = []
    current: date | None = None
    for row in rows:
        cells = [c for c in row if c not in (None, "") and not (isinstance(c, str) and not c.strip())]
        if not cells:
            continue
        row_date = next((d for c in cells if (d := parse_date(c, today))), None)
        texts = [str(c).strip() for c in cells if parse_date(c, today) is None]
        if row_date and not texts:
            current = row_date          # (나) 날짜만 있는 줄
            continue
        if not texts or all(t in HEADER_WORDS for t in texts):
            continue                    # 머리글 줄
        d = row_date or current
        text = " · ".join(texts)
        tasks.append(Task(task_key(d, text), d.isoformat() if d else None, text))
    return tasks


def rows_from_xlsx(data: bytes | Path) -> list[list[Any]]:
    import openpyxl
    src = io.BytesIO(data) if isinstance(data, bytes) else data
    wb = openpyxl.load_workbook(src, read_only=True, data_only=True)
    try:
        return [list(r) for ws in wb.worksheets for r in ws.iter_rows(values_only=True)]
    finally:
        wb.close()


def rows_from_csv(path: Path) -> list[list[Any]]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp949"):
        try:
            return list(csv.reader(io.StringIO(raw.decode(enc))))
        except UnicodeDecodeError:
            continue
    raise WorklogError(f"{path.name}의 글자 인코딩을 읽지 못했습니다.")


def sheet_id_from_link(text: str) -> str | None:
    m = SHEET_ID.search(text or "")
    return m.group(1) if m else None


def sheet_id_from_gsheet(path: Path) -> str | None:
    """드라이브 데스크톱 앱의 .gsheet 파일(JSON)에서 시트 id."""
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return info.get("doc_id") or sheet_id_from_link(info.get("url", ""))


def download_xlsx(sheet_id: str, timeout: float = 30) -> bytes:
    req = urllib.request.Request(EXPORT_URL.format(id=sheet_id), headers={"User-Agent": "SoneFM-Platform"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 404):
            raise WorklogError("업무일지 시트를 열 권한이 없습니다. 시트(또는 폴더) 공유를 "
                               "'링크가 있는 모든 사용자: 뷰어'로 바꿔 주세요.") from None
        raise WorklogError(f"업무일지를 내려받지 못했습니다 (HTTP {e.code}).") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise WorklogError(f"인터넷 연결을 확인하세요. 업무일지를 내려받지 못했습니다 ({e}).") from None
    if not data.startswith(b"PK"):   # xlsx는 zip. 로그인 화면(HTML)이 오면 공유가 꺼진 것
        raise WorklogError("업무일지 시트가 공유되어 있지 않습니다. 공유를 '링크가 있는 모든 사용자: 뷰어'로 바꿔 주세요.")
    return data


def load(cfg: dict[str, Any], today: date, download: Callable[[str], bytes] = download_xlsx) -> dict[str, Any]:
    """→ {tasks, label, last_date, warning}. 실패해도 예외 대신 warning에 이유를 담는다."""
    try:
        rows, label = _rows(cfg, download)
    except WorklogError as e:
        return {"tasks": [], "label": "업무일지 없음", "last_date": None, "warning": str(e)}
    tasks = parse_rows(rows, today)
    dates = [t.date for t in tasks if t.date and t.date <= today.isoformat()]
    last = max(dates) if dates else None
    warning = None
    if any(t.date for t in tasks) and (last is None or
                                      date.fromisoformat(last) < today - timedelta(days=int(cfg.get("stale_days", 3)))):
        warning = (f"업무일지에 최근 {cfg.get('stale_days', 3)}일 안의 내용이 없습니다. 국장님이 업무일지 폴더 밖에 "
                   "새 시트를 만들었는지 확인하세요. (설정 → 업무일지 링크로 다시 연결할 수 있습니다)")
    elif not tasks:
        warning = "업무일지에서 업무를 찾지 못했습니다. 시트 형식이 바뀌었는지 확인하세요."
    return {"tasks": [t.to_dict() for t in tasks], "label": label, "last_date": last, "warning": warning}


def _rows(cfg: dict[str, Any], download: Callable[[str], bytes]) -> tuple[list[list[Any]], str]:
    if cfg.get("worklog_url"):
        sid = sheet_id_from_link(cfg["worklog_url"])
        if not sid:
            raise WorklogError("설정의 업무일지 링크가 구글 시트 주소가 아닙니다.")
        return rows_from_xlsx(download(sid)), "업무일지(링크)"
    folder = Path(cfg.get("worklog_folder") or "")
    if not cfg.get("worklog_folder"):
        raise WorklogError("업무일지 폴더가 설정되지 않았습니다. 설정에서 폴더를 지정하거나 업무를 직접 입력하세요.")
    if not folder.is_dir():
        raise WorklogError(f"업무일지 폴더를 찾지 못했습니다: {folder} (드라이브 데스크톱 앱이 켜져 있는지 확인하세요)")
    files = sorted((p for p in folder.iterdir() if p.suffix.lower() in (".gsheet", ".xlsx", ".csv")
                    and not p.name.startswith("~$")), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise WorklogError(f"업무일지 폴더가 비어 있습니다: {folder}")
    f = files[0]
    if f.suffix.lower() == ".gsheet":
        sid = sheet_id_from_gsheet(f)
        if not sid:
            raise WorklogError(f"{f.name}에서 시트 주소를 읽지 못했습니다.")
        return rows_from_xlsx(download(sid)), f.stem
    if f.suffix.lower() == ".xlsx":
        return rows_from_xlsx(f), f.name
    return rows_from_csv(f), f.name
