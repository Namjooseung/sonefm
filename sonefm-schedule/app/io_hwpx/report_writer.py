"""방송실시결과 보고서 hwpx 쓰기: 양식(지난달 보고서)의 정해진 칸 글자만 바꾸고 두 목록 표의 행 수를 맞춘다.

양식 표 번호(문서 순서, 0부터) — 별지 제13호서식 기준
  0 제목 / 2 전문편성 / 3 국내제작물 / 7 대중음악 / 8 제작주체 / 9 기타(청취자참여)
  16 4면 대중음악 목록 (머리 1행 + 자료 + 합계 1행)
  21 5면 청취자참여 목록 (머리 1행 + 자료 + 총합 1행)
양식이 바뀌면 이 번호와 칸 위치를 같이 고친다.
"""
from __future__ import annotations

import copy
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from app.domain.report import Report, ReportRow, fmt

HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
SECTION = "Contents/section0.xml"
T_TITLE, T_SPECIAL, T_DOMESTIC, T_MUSIC, T_PRODUCER, T_ETC, T_MUSIC_LIST, T_LISTENER_LIST = 0, 2, 3, 7, 8, 9, 16, 21


class TemplateError(Exception):
    """양식 구조가 예상과 다름."""


# ── 칸 다루기 ────────────────────────────────────

def _cells(tbl: ET.Element) -> dict[tuple[int, int], ET.Element]:
    out = {}
    for tc in tbl.iter(HP + "tc"):
        a = tc.find(HP + "cellAddr")
        out[(int(a.get("rowAddr")), int(a.get("colAddr")))] = tc
    return out


def _drop_lineseg(p: ET.Element) -> None:
    """줄 배치 캐시는 글자를 바꾸면 틀리므로 지운다 (한글이 열 때 다시 계산)."""
    for ls in p.findall(HP + "linesegarray"):
        p.remove(ls)


def _set_value(tc: ET.Element, text: str) -> None:
    """한 줄 값 칸: 글자가 있는 첫 hp:t를 바꾼다 (필드 등 다른 컨트롤은 그대로). 없으면 마지막 run에 넣는다."""
    ts = [t for t in tc.iter(HP + "t")]
    target = next((t for t in ts if (t.text or "").strip()), None)
    if target is None:
        runs = list(tc.iter(HP + "run"))
        if not runs:
            raise TemplateError("값을 넣을 칸에 글자 자리가 없습니다.")
        target = ET.SubElement(runs[-1], HP + "t")
    target.text = text
    for p in tc.iter(HP + "p"):
        _drop_lineseg(p)


def _set_lines(tc: ET.Element, lines: list[str]) -> None:
    """목록 칸: 첫 문단 모양으로 줄마다 문단 하나 (칸 안 줄바꿈)."""
    sub = tc.find(HP + "subList")
    paras = sub.findall(HP + "p")
    proto = paras[0]
    for p in paras:
        sub.remove(p)
    for line in lines or [""]:
        p = copy.deepcopy(proto)
        _drop_lineseg(p)
        runs = p.findall(HP + "run")
        for r in runs[1:]:
            p.remove(r)
        run = runs[0] if runs else ET.SubElement(p, HP + "run")
        for child in list(run):
            run.remove(child)
        if line:
            ET.SubElement(run, HP + "t").text = line
        sub.append(p)


def _fill_list(tbl: ET.Element, rows: list[ReportRow], total: int, music: bool) -> None:
    """머리 1행·합계 1행을 두고 가운데 자료 행을 rows 수만큼 다시 만든다."""
    trs = tbl.findall(HP + "tr")
    if len(trs) < 4:
        raise TemplateError("목록 표의 행이 너무 적습니다.")
    head, first_proto, proto, foot = trs[0], trs[1], trs[2], trs[-1]
    for tr in trs[1:]:
        tbl.remove(tr)
    new = []
    for i, row in enumerate(rows):
        tr = copy.deepcopy(first_proto if i == 0 else proto)
        cols = [[row.name], row.dates, row.starts, row.ends, [fmt(row.minutes)]]
        if music:
            cols += [["국내"], []]
        for c, tc in enumerate(tr.findall(HP + "tc")):
            tc.find(HP + "cellAddr").set("rowAddr", str(i + 1))
            _set_lines(tc, cols[c] if c < len(cols) else [])
        new.append(tr)
    foot_tcs = foot.findall(HP + "tc")
    for tc in foot_tcs:
        tc.find(HP + "cellAddr").set("rowAddr", str(len(rows) + 1))
    _set_value(foot_tcs[4], fmt(total))
    for tr in new + [foot]:
        tbl.append(tr)
    tbl.set("rowCnt", str(len(rows) + 2))
    height = sum(int(tr.find(HP + "tc").find(HP + "cellSz").get("height")) for tr in [head, *new, foot])
    tbl.find(HP + "sz").set("height", str(height))


def fill_section(xml: bytes, report: Report) -> bytes:
    nss = dict(ns for _ev, ns in ET.iterparse(io.BytesIO(xml), events=("start-ns",)))
    for prefix, uri in nss.items():
        ET.register_namespace(prefix, uri)
    root = ET.fromstring(xml)
    tbls = list(root.iter(HP + "tbl"))
    if len(tbls) < 22:
        raise TemplateError("보고서 양식의 표 개수가 다릅니다.")
    c = {i: _cells(tbls[i]) for i in (T_TITLE, T_SPECIAL, T_DOMESTIC, T_MUSIC, T_PRODUCER, T_ETC)}

    title_t = next((t for t in c[T_TITLE][(0, 0)].iter(HP + "t") if re.search(r"\d", t.text or "")), None)
    if title_t is None:
        raise TemplateError("제목의 월 자리를 찾지 못했습니다.")
    title_t.text = re.sub(r"\d+", str(report.month), title_t.text, count=1)

    total = fmt(report.total)
    for t, cells_values in (
        (T_SPECIAL, {(1, 1): total, (1, 2): total}),
        (T_DOMESTIC, {(1, 1): total, (1, 2): total, (1, 3): "0"}),
        (T_MUSIC, {(1, 1): fmt(report.music), (1, 2): fmt(report.music)}),
        (T_PRODUCER, {(1, 1): total, (1, 4): fmt(report.external), (2, 4): report.external_pct}),
        (T_ETC, {(2, 5): fmt(report.listener)}),
    ):
        for rc, v in cells_values.items():
            _set_value(c[t][rc], v)

    _fill_list(tbls[T_MUSIC_LIST], report.music_rows, report.music, music=True)
    _fill_list(tbls[T_LISTENER_LIST], report.listener_rows, report.listener_rows_sum, music=False)

    head = xml[: xml.index(b"?>") + 2] if xml.startswith(b"<?xml") else b""
    return head + ET.tostring(root, encoding="unicode").encode("utf-8")


def write_report(report: Report, template: str | Path, path: str | Path) -> None:
    """mimetype을 맨 앞·무압축으로 두고, section0.xml만 바꿔 나머지 파일은 그대로 옮긴다."""
    with zipfile.ZipFile(template) as zin:
        new_section = fill_section(zin.read(SECTION), report)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zout:
            for info in zin.infolist():
                data = new_section if info.filename == SECTION else zin.read(info.filename)
                zout.writestr(info, data, compress_type=info.compress_type)
    Path(path).write_bytes(buf.getvalue())
