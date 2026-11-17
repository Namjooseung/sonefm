"""플래너 평가 실행기 (AI_DESIGN 10절). 평가 세트는 저장소 밖에 둔다. 결과는 --out 경로에만 쓴다.

  python tools/planner_eval.py --set eval_set.csv --types task_types.json [--programs names.json]
         --mode keyword --runs 1 --out result.json

지표: M1 업무 단위 분류(F1·줄 완전 일치·업무 개수), M2 문장 종류, M3 추출값(전체·날짜 계열),
M4 처리 방식(recipe 정밀도·재현율), M5·M6 LLM 회부(keyword 모드는 회부 대상 수만), M7 가리기 위반.
결과 JSON에는 숫자와 줄 번호별 오답(번호·정답 유형·예측 유형)만 둔다. 원문은 담지 않는다.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hub import recipes as R  # noqa: E402
from hub.planner import classify as C  # noqa: E402
from hub.planner.masking import find_violations  # noqa: E402
from hub.planner.plan import analyze_cells, build_llm_bundle, pending, plan_cells  # noqa: E402
from hub.planner.types import load_task_types  # noqa: E402
from hub.planner.values import norm_name  # noqa: E402

DATE_KEYS = ("date", "month", "months", "period", "deadline")
REPORT_KINDS = ("진행 보고", "완료 보고")


# ---- 읽기 ---------------------------------------------------------------------------

def read_set(path: Path) -> list[dict[str, Any]]:
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            rows.append({"no": r["번호"], "date": r["날짜"], "dept": r["부서"], "text": r["원문"],
                         "kind": r["문장 종류"], "gold": json.loads(r["정답 업무 목록"]),
                         "modes": {m.strip() for m in (r.get("처리 방식") or "").split(";") if m.strip()}})
    return rows


def read_programs(path: Path | None) -> list[str]:
    if not path:
        return []
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return list(data.get("names", [])) if isinstance(data, dict) else list(data)


# ---- 값 비교 ------------------------------------------------------------------------

def _flat(s: Any) -> str:
    return re.sub(r"[\W_]+", "", str(s)).lower()


def _name_match(pred: str | None, gold: str | None) -> bool:
    pn, gn = norm_name(pred or ""), norm_name(gold or "")
    if not pn or not gn:
        return pn == gn
    if "[이름]" in (gold or ""):
        pieces = [norm_name(x) for x in (gold or "").split("[이름]") if norm_name(x)]
        if pieces and all(p in pn for p in pieces):
            return True
    return pn in gn or gn in pn


def _prog_items(v: Any) -> list[tuple[str | None, frozenset]]:
    out = []
    for e in v if isinstance(v, list) else []:
        if isinstance(e, dict):
            out.append((e.get("name"), frozenset(int(x) for x in e.get("episodes", []) if str(x).lstrip("-").isdigit())))
    return out


def value_pairs(values: dict[str, Any], only_text: dict[str, str] | None = None) -> list[tuple[str, Any]]:
    """(키, 값) 쌍 목록. programs·not_selected는 항목마다 한 쌍. only_text가 있으면 출처가 text인 것만."""
    pairs: list[tuple[str, Any]] = []
    for k, v in values.items():
        if only_text is not None and only_text.get(k) != "text":
            continue
        if k in ("programs", "not_selected"):
            pairs.extend((k, it) for it in _prog_items(v))
        elif isinstance(v, list):
            pairs.append((k, tuple(sorted(_flat(x) for x in v))))
        else:
            pairs.append((k, str(v).strip() if k in DATE_KEYS or k in ("time", "base_date", "year") else v))
    return pairs


def pair_equal(a: tuple[str, Any], b: tuple[str, Any]) -> bool:
    if a[0] != b[0]:
        return False
    k, x, y = a[0], a[1], b[1]
    if k in ("programs", "not_selected"):
        return x[1] == y[1] and _name_match(x[0], y[0])
    if k in DATE_KEYS or k in ("time", "base_date", "year", "week", "weekdays"):
        return _flat(x) == _flat(y)
    if isinstance(x, tuple) or isinstance(y, tuple):
        return x == y
    fx, fy = _flat(x), _flat(y)
    return bool(fx) and bool(fy) and (fx == fy or fx in fy or fy in fx)


def count_matches(pred: list, gold: list) -> int:
    used = [False] * len(gold)
    tp = 0
    for p in pred:
        for i, g in enumerate(gold):
            if not used[i] and pair_equal(p, g):
                used[i] = True
                tp += 1
                break
    return tp


# ---- 실행 ---------------------------------------------------------------------------

def predict_row(row: dict[str, Any], ref_types: dict, recipes: dict, programs: list[str]) -> dict[str, Any]:
    cell = {"date": row["date"], "date_end": row["date"], "dept": row["dept"], "text": row["text"], "note": ""}
    plan = plan_cells([cell], ref_types, recipes, programs, llm=None, now=None)
    tasks = []
    for it in plan["items"]:
        tasks.append({"type": it["type"], "values": it["values"], "sources": it["value_sources"],
                      "mode": it["mode"], "kind": it["sentence_kind"]})
    for rp in plan["reports"]:
        tasks.append({"type": rp["type"], "values": rp["values"], "sources": rp["value_sources"],
                      "mode": "excluded", "kind": rp["sentence_kind"]})
    return {"tasks": tasks, "plan": plan}


def row_kind(tasks: list[dict[str, Any]]) -> str:
    kinds = Counter(t["kind"] for t in tasks)
    if not kinds:
        return "지시"
    top, n = kinds.most_common(1)[0]
    return top if list(kinds.values()).count(n) == 1 else "지시"


def prf(tp: int, p: int, g: int) -> dict[str, float | int]:
    pr = tp / p if p else 0.0
    rc = tp / g if g else 0.0
    f1 = 2 * pr * rc / (pr + rc) if pr + rc else 0.0
    return {"tp": tp, "pred": p, "gold": g, "precision": round(pr, 4), "recall": round(rc, 4), "f1": round(f1, 4)}


def evaluate(rows: list[dict[str, Any]], ref_types: dict, recipes: dict, programs: list[str]) -> dict[str, Any]:
    by_type = R.recipes_by_type(recipes)
    m1_tp = m1_p = m1_g = 0
    exact = multi_total = multi_ok = 0
    kind_ok = rep_total = rep_hit = 0
    all_pairs = {"all": [0, 0, 0], "date": [0, 0, 0]}   # tp, pred, gold
    r_tp = r_p = r_g = 0
    mode_ok = 0
    err_m1, err_m2, err_m4, err_m3 = [], [], [], []
    for row in rows:
        pr = predict_row(row, ref_types, recipes, programs)
        tasks = pr["tasks"]
        gold = row["gold"]
        gt, pt = Counter(g["type"] for g in gold), Counter(t["type"] for t in tasks)
        tp = sum((gt & pt).values())
        m1_tp, m1_p, m1_g = m1_tp + tp, m1_p + sum(pt.values()), m1_g + sum(gt.values())
        if gt == pt:
            exact += 1
        else:
            err_m1.append({"번호": row["no"], "정답": [g["type"] for g in gold], "예측": [t["type"] for t in tasks]})
        if len(gold) >= 2:
            multi_total += 1
            multi_ok += len(tasks) == len(gold)
        # M2
        kind = row_kind(tasks)
        kind_ok += kind == row["kind"]
        if kind != row["kind"]:
            err_m2.append({"번호": row["no"], "정답": row["kind"], "예측": kind})
        if row["kind"] in REPORT_KINDS:
            rep_total += 1
            rep_hit += kind in REPORT_KINDS
        # M3: 유형이 맞은 업무끼리 (같은 유형은 나온 순서대로 짝)
        pool: dict[str, list] = {}
        for t in tasks:
            pool.setdefault(t["type"], []).append(t)
        for g in gold:
            lst = pool.get(g["type"])
            if not lst:
                continue
            t = lst.pop(0)
            gp = value_pairs(g["values"])
            pp = value_pairs(t["values"], t["sources"])
            for name, flt in (("all", lambda kv: True), ("date", lambda kv: kv[0] in DATE_KEYS)):
                gg, pp2 = [x for x in gp if flt(x)], [x for x in pp if flt(x)]
                hit = count_matches(pp2, gg)
                a = all_pairs[name]
                a[0], a[1], a[2] = a[0] + hit, a[1] + len(pp2), a[2] + len(gg)
                if name == "all" and hit < max(len(pp2), len(gg)):
                    err_m3.append({"번호": row["no"], "유형": g["type"], "정답 키": sorted({k for k, _ in gg}),
                                   "예측 키": sorted({k for k, _ in pp2})})
        # M4: recipe 업무
        gr = Counter(g["type"] for g in gold if g["type"] in by_type)
        prr = Counter(t["type"] for t in tasks if t["mode"] == "recipe")
        r_tp, r_p, r_g = r_tp + sum((gr & prr).values()), r_p + sum(prr.values()), r_g + sum(gr.values())
        if gr != prr:
            err_m4.append({"번호": row["no"], "정답": sorted(gr.elements()), "예측": sorted(prr.elements())})
        pm = {t["mode"] for t in tasks if t["mode"] != "excluded"} or {"checklist"}
        mode_ok += pm == (row["modes"] or {"checklist"})
    # 회부 대상과 가리기
    cells = [{"date": r["date"], "date_end": r["date"], "dept": r["dept"], "text": r["text"], "note": ""} for r in rows]
    recs = analyze_cells(cells, ref_types)
    referred = [r for r in recs if pending(r)]
    bundle = build_llm_bundle(recs, names=None, all_units=True)
    viol = sum(len(find_violations(it["text"])) for it in bundle["items"])
    n = len(rows)
    return {
        "rows": n, "units": len(recs),
        "config": {"confirm_min": C.CONFIRM_MIN, "confirm_margin": C.CONFIRM_MARGIN},
        "M1": {**prf(m1_tp, m1_p, m1_g), "row_exact": round(exact / n, 4) if n else 0.0,
               "multi_rows": multi_total, "multi_count_accuracy": round(multi_ok / multi_total, 4) if multi_total else None},
        "M2": {"accuracy": round(kind_ok / n, 4) if n else 0.0, "report_rows": rep_total,
               "report_recall": round(rep_hit / rep_total, 4) if rep_total else None},
        "M3": {"all": prf(*all_pairs["all"]), "date_keys": prf(*all_pairs["date"]),
               "note": "유형이 맞은 업무끼리, 출처가 text인 값만 비교(default 값 제외)"},
        "M4": {"recipe": prf(r_tp, r_p, r_g), "mode_set_agreement": round(mode_ok / n, 4) if n else 0.0},
        "M5_M6": {"mode": "keyword", "referred_units": len(referred), "referral_rate": round(len(referred) / len(recs), 4) if recs else 0.0,
                  "by_reason": {"ambiguous": sum(r["keyword"]["state"] == C.AMBIGUOUS and not r["credential"] for r in recs),
                                "unclassified": sum(r["keyword"]["state"] == C.UNCLASSIFIED and not r["credential"] for r in recs),
                                "kind_unsure": sum(r["kind_unsure"] and not r["credential"] for r in recs)},
                  "calls": 0, "cost_usd": 0.0, "latency_ms": None},
        "M7": {"violations": viol, "bundle_units": len(bundle["items"]),
               "credential_cells_in_set": sum(1 for r in recs if r["credential"]),
               "credential_units_sent": sum(1 for it in bundle["items"] if any(r["id"] == it["id"] and r["credential"] for r in recs))},
        "errors": {"M1": err_m1, "M2": err_m2, "M3": err_m3, "M4": err_m4},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", required=True, type=Path, help="평가 세트 CSV (저장소 밖)")
    ap.add_argument("--types", type=Path, default=None, help="업무 유형 사전 JSON (기본: 저장소의 task_types.json)")
    ap.add_argument("--programs", type=Path, default=None, help="프로그램 이름 목록 JSON (없으면 빈 목록)")
    ap.add_argument("--mode", choices=["keyword"], default="keyword")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--out", type=Path, default=None, help="결과 JSON 경로 (이 경로에만 쓴다)")
    a = ap.parse_args(argv)
    ref_types = load_task_types(a.types)
    from hub.paths import resource_dir
    recipes, problems = R.load_recipes([resource_dir() / "recipes"])
    if problems:
        print("\n".join(problems), file=sys.stderr)
    result = evaluate(read_set(a.set), ref_types, recipes, read_programs(a.programs))
    result["mode"], result["runs"] = a.mode, a.runs     # keyword 모드는 결과가 매번 같아 한 번만 돈다
    text = json.dumps(result, ensure_ascii=False, indent=1)
    if a.out:
        a.out.write_text(text, encoding="utf-8")
    brief = {k: result[k] for k in ("rows", "units", "M1", "M2", "M3", "M4", "M5_M6", "M7")}
    print(json.dumps(brief, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
