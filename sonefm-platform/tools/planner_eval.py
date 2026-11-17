"""플래너 평가 실행기 (AI_DESIGN 10절). 평가 세트는 저장소 밖에 둔다. 결과는 --out 경로에만 쓴다.

  python tools/planner_eval.py --set eval_set.csv --types task_types.json [--programs names.json]
         --mode keyword|llm-light|llm-heavy|llm-only-heavy --runs 1 --out result.json
  python tools/planner_eval.py ... --mode llm-light --estimate      (실제 호출 없이 호출 수·글자 수만 계산)

LLM 모드는 실제 API를 부른다(돈이 든다). 키가 없거나 AI가 꺼져 있으면 호출하지 않고 안내만 한다.
llm-only-heavy = 키워드 결과와 상관없이 모든 업무(계정 칸 제외)를 heavy로 보낸다(평가 D 구성).

지표: M1 업무 단위 분류(F1·줄 완전 일치·업무 개수), M2 문장 종류, M3 추출값(전체·날짜 계열),
M4 처리 방식(recipe 정밀도·재현율), M5·M6 LLM 회부(keyword 모드는 회부 대상 수만), M7 가리기 위반,
M8 질문(줄마다 정답 질문 키 집합 `missing:*`·`input_file` 대 예측 집합: 재현율·정밀도). CSV 열 "필요한 질문"(JSON 배열, 없으면 빈 목록).
결과 JSON에는 숫자와 줄 번호별 오답(번호·정답 유형·예측 유형)만 둔다. 원문은 담지 않는다.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
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
from hub.planner.llm import (BATCH_SIZE, FEATURE_EVAL, OUT_CHARS_PER_UNIT, OUT_TOKENS_PER_UNIT,  # noqa: E402
                             LLMClassifier, build_messages, build_system, chunks)
from hub.planner.plan import analyze_cells, build_llm_bundle, pending, plan_cells  # noqa: E402
from hub.planner.types import load_task_types  # noqa: E402
from hub.planner.values import norm_name  # noqa: E402

MODES = {"keyword": (None, False), "llm-light": ("light", False), "llm-heavy": ("heavy", False),
         "llm-only-heavy": ("heavy", True)}      # 모드 → (등급, 전부 보내기)
DATE_KEYS = ("date", "month", "months", "period", "deadline")
REPORT_KINDS = ("진행 보고", "완료 보고")


# ---- 읽기 ---------------------------------------------------------------------------

def read_set(path: Path) -> list[dict[str, Any]]:
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            rows.append({"no": r["번호"], "date": r["날짜"], "dept": r["부서"], "text": r["원문"],
                         "kind": r["문장 종류"], "gold": json.loads(r["정답 업무 목록"]),
                         "questions": json.loads(r.get("필요한 질문") or "[]"),
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

def predict_all(rows: list[dict[str, Any]], ref_types: dict, recipes: dict, programs: list[str],
                llm: Any = None, all_units: bool = False) -> dict[str, Any]:
    """모든 줄을 한 번에 계획한다(LLM 묶음 호출이 줄 경계를 넘어 묶이도록). by_row: 줄 번호 → {"tasks": [...]}."""
    cells = [{"date": r["date"], "date_end": r["date"], "dept": r["dept"], "text": r["text"], "note": "",
              "key": f"r{r['no']}"} for r in rows]
    plan = plan_cells(cells, ref_types, recipes, programs, llm=llm, now=None, all_units=all_units)
    out: dict[str, dict[str, Any]] = {r["no"]: {"tasks": []} for r in rows}
    for it in plan["items"]:
        out[it["cell_key"][1:]]["tasks"].append({"type": it["type"], "values": it["values"], "sources": it["value_sources"],
                                                 "mode": it["mode"], "kind": it["sentence_kind"],
                                                 "questions": [q["key"] for q in it.get("questions", [])]})
    for rp in plan["reports"]:
        out[rp["cell_key"][1:]]["tasks"].append({"type": rp["type"], "values": rp["values"],
                                                 "sources": rp["value_sources"], "mode": "excluded",
                                                 "kind": rp["sentence_kind"], "questions": []})
    return {"by_row": out, "plan": plan}


def row_kind(tasks: list[dict[str, Any]]) -> str:
    kinds = Counter(t["kind"] for t in tasks)
    if not kinds:
        return "지시"
    top, n = kinds.most_common(1)[0]
    return top if list(kinds.values()).count(n) == 1 else "지시"


def question_keys(keys: Any) -> set[str]:
    """M8에서 비교하는 질문 키만: `missing:*`와 `input_file`."""
    return {k for k in keys if k == "input_file" or str(k).startswith("missing:")}


def prf(tp: int, p: int, g: int) -> dict[str, float | int]:
    pr = tp / p if p else 0.0
    rc = tp / g if g else 0.0
    f1 = 2 * pr * rc / (pr + rc) if pr + rc else 0.0
    return {"tp": tp, "pred": p, "gold": g, "precision": round(pr, 4), "recall": round(rc, 4), "f1": round(f1, 4)}


def evaluate(rows: list[dict[str, Any]], ref_types: dict, recipes: dict, programs: list[str],
             clf: LLMClassifier | None = None, all_units: bool = False) -> dict[str, Any]:
    by_type = R.recipes_by_type(recipes)
    m1_tp = m1_p = m1_g = 0
    exact = multi_total = multi_ok = 0
    kind_ok = rep_total = rep_hit = 0
    all_pairs = {"all": [0, 0, 0], "date": [0, 0, 0]}   # tp, pred, gold
    r_tp = r_p = r_g = 0
    mode_ok = 0
    err_m1, err_m2, err_m4, err_m3, err_m8 = [], [], [], [], []
    q_tp = q_p = q_g = q_rows = 0
    predicted = predict_all(rows, ref_types, recipes, programs, llm=clf, all_units=all_units)
    for row in rows:
        tasks = predicted["by_row"][row["no"]]["tasks"]
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
        # 정답 질문에 input_file이 있는 줄(받은 파일을 올리는 업무)은 recipe로 세지 않는다(AI_DESIGN 7.4)
        gr = Counter(g["type"] for g in gold if g["type"] in by_type and "input_file" not in row["questions"])
        prr = Counter(t["type"] for t in tasks if t["mode"] == "recipe")
        r_tp, r_p, r_g = r_tp + sum((gr & prr).values()), r_p + sum(prr.values()), r_g + sum(gr.values())
        if gr != prr:
            err_m4.append({"번호": row["no"], "정답": sorted(gr.elements()), "예측": sorted(prr.elements())})
        gq = question_keys(row["questions"])
        pq = question_keys(k for t in tasks for k in t.get("questions", []))
        q_tp, q_p, q_g = q_tp + len(gq & pq), q_p + len(pq), q_g + len(gq)
        if gq or pq:
            q_rows += 1
        if gq != pq:
            err_m8.append({"번호": row["no"], "정답": sorted(gq), "예측": sorted(pq)})
        pm = {t["mode"] for t in tasks if t["mode"] != "excluded"} or {"checklist"}
        mode_ok += pm == (row["modes"] or {"checklist"})
    # 회부 대상과 가리기
    cells = [{"date": r["date"], "date_end": r["date"], "dept": r["dept"], "text": r["text"], "note": ""} for r in rows]
    recs = analyze_cells(cells, ref_types)
    referred = [r for r in recs if pending(r)]
    bundle = build_llm_bundle(recs, names=None, all_units=True)
    viol = sum(len(find_violations(it["text"])) for it in bundle["items"])
    n = len(rows)
    res = {
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
        "M8": {**prf(q_tp, q_p, q_g), "rows_with_questions": q_rows, "rows": n,
               "note": "줄마다 정답 질문 키(missing:*·input_file) 집합 대 예측 집합"},
        "M7": {"violations": viol, "bundle_units": len(bundle["items"]),
               "credential_cells_in_set": sum(1 for r in recs if r["credential"]),
               "credential_units_sent": sum(1 for it in bundle["items"] if any(r["id"] == it["id"] and r["credential"] for r in recs))},
        "errors": {"M1": err_m1, "M2": err_m2, "M3": err_m3, "M4": err_m4, "M8": err_m8},
    }
    if clf is not None:
        res["M5_M6"] = llm_metrics(clf, len(rows), len(recs), res["M5_M6"], predicted["plan"])
    return res


def _percentile(vals: list[float], q: float) -> float | None:
    if not vals:
        return None
    v = sorted(vals)
    return v[max(0, math.ceil(q * len(v)) - 1)]


def llm_metrics(clf: LLMClassifier, rows: int, units: int, base: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    """M5·M6: 호출 수, 실패, 비용, 회부율, 지연, 캐시. 재시도 수는 main이 호출 기록(DB)에서 채운다."""
    log = clf.calls_log
    ok = [c for c in log if c["ok"]]
    sent = sum(c["units"] for c in log)
    cost = sum(c["cost_usd"] for c in log)
    ms = [c["duration_ms"] for c in ok if c["duration_ms"] is not None]
    return {**base, "mode": "llm", "tier": clf.tier, "model": clf.model, "calls": len(log),
            "failed_calls": len(log) - len(ok), "retried_calls": None, "cost_usd": round(cost, 6),
            "cost_per_row": round(cost / rows, 6) if rows else 0.0,
            "cost_per_llm_unit": round(cost / sent, 6) if sent else 0.0,
            "units_sent": sent, "referral_rate": round(sent / units, 4) if units else 0.0,
            "latency_ms": {"median": statistics.median(ms) if ms else None, "p95": _percentile(ms, 0.95)},
            "cache_read_tokens": sum(c["cache_read_tokens"] for c in ok),
            "llm_failed_units": sum(1 for it in plan["items"] if "llm_failed" in it["flags"])}


def retried_since(feature: str, since: str) -> int:
    """호출 기록(DB)에서 이번 실행 중 재시도한 호출 수."""
    from hub import db
    with db.connect() as c:
        return c.execute("SELECT COUNT(*) FROM ai_calls WHERE feature = ? AND at >= ? AND retried = 1",
                         (feature, since)).fetchone()[0]


def _leaves(d: Any, path: str = "") -> dict[str, float]:
    out: dict[str, float] = {}
    if isinstance(d, dict):
        for k, v in d.items():
            out.update(_leaves(v, f"{path}.{k}" if path else str(k)))
    elif isinstance(d, (int, float)) and not isinstance(d, bool):
        out[path] = d
    return out


def summarize_runs(results: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """반복 실행의 숫자 지표 평균·최소·최대 (M1~M6)."""
    flat = [_leaves({k: r[k] for k in ("M1", "M2", "M3", "M4", "M5_M6", "M8")}) for r in results]
    return {k: {"mean": round(statistics.mean(f[k] for f in flat), 4),
                "min": min(f[k] for f in flat), "max": max(f[k] for f in flat)}
            for k in flat[0] if all(k in f for f in flat)}


def estimate(rows: list[dict[str, Any]], ref_types: dict, mode: str, batch_size: int = BATCH_SIZE) -> dict[str, Any]:
    """실제 호출 없이: 이 모드에서 보낼 호출 수와 글자 수. 게이트웨이를 부르지 않는다."""
    tier, all_units = MODES[mode]
    cells = [{"date": r["date"], "date_end": r["date"], "dept": r["dept"], "text": r["text"], "note": "",
              "key": f"r{r['no']}"} for r in rows]
    recs = analyze_cells(cells, ref_types)
    items = build_llm_bundle(recs, names=None, all_units=all_units)["items"] if tier else []
    batches = chunks(items, batch_size)
    msg_chars = [sum(len(m["content"]) for m in build_messages(b)) for b in batches]
    return {"mode": mode, "tier": tier, "rows": len(rows), "units": len(recs), "units_sent": len(items),
            "batch_size": batch_size, "calls": len(batches),
            "system_chars": len(build_system(ref_types)) if batches else 0,
            "messages_chars_per_call": msg_chars, "messages_chars_total": sum(msg_chars),
            "est_output_tokens": len(items) * OUT_TOKENS_PER_UNIT, "est_output_chars": len(items) * OUT_CHARS_PER_UNIT,
            "note": f"출력은 업무당 {OUT_TOKENS_PER_UNIT}토큰(약 {OUT_CHARS_PER_UNIT}자)으로 본 대략값. 실제 호출 없음"}


def _ai_ready() -> tuple[bool, str]:
    from hub.ai import settings
    st = settings.status()
    if not st["enabled"]:
        return False, "AI 기능이 꺼져 있습니다. 플랫폼 설정 화면의 [AI]에서 AI를 켜 주세요."
    if not st["has_key"]:
        return False, "API 키가 없습니다. 플랫폼 설정 화면의 [AI]에서 방송국 API 키를 넣어 주세요."
    return True, ""


def main(argv: list[str] | None = None, ai_ready: Any = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", required=True, type=Path, help="평가 세트 CSV (저장소 밖)")
    ap.add_argument("--types", type=Path, default=None, help="업무 유형 사전 JSON (기본: 저장소의 task_types.json)")
    ap.add_argument("--programs", type=Path, default=None, help="프로그램 이름 목록 JSON (없으면 빈 목록)")
    ap.add_argument("--mode", choices=list(MODES), default="keyword")
    ap.add_argument("--runs", type=int, default=1, help="LLM 모드 반복 횟수(지표 평균·최소·최대)")
    ap.add_argument("--estimate", action="store_true", help="실제 호출 없이 호출 수·글자 수만 계산")
    ap.add_argument("--out", type=Path, default=None, help="결과 JSON 경로 (이 경로에만 쓴다)")
    a = ap.parse_args(argv)
    ref_types = load_task_types(a.types)
    from hub.paths import resource_dir
    recipes, problems = R.load_recipes([resource_dir() / "recipes"])
    if problems:
        print("\n".join(problems), file=sys.stderr)
    rows, programs = read_set(a.set), read_programs(a.programs)
    if a.estimate:
        print(json.dumps(estimate(rows, ref_types, a.mode), ensure_ascii=False, indent=1))
        return 0
    tier, all_units = MODES[a.mode]
    if tier is None:
        result = evaluate(rows, ref_types, recipes, programs)
        result["mode"], result["runs"] = a.mode, 1     # keyword 모드는 결과가 매번 같아 한 번만 돈다
    else:
        ok, why = (ai_ready or _ai_ready)()
        if not ok:
            print(f"LLM 모드를 시작하지 않았습니다(호출 없음). {why}", file=sys.stderr)
            return 1
        from hub import db
        results = []
        for _ in range(max(1, a.runs)):
            since = db.now()
            clf = LLMClassifier(ref_types, tier=tier, feature=FEATURE_EVAL)
            res = evaluate(rows, ref_types, recipes, programs, clf=clf, all_units=all_units)
            res["M5_M6"]["retried_calls"] = retried_since(FEATURE_EVAL, since)
            results.append(res)
        result = results[0]
        result["mode"], result["runs"] = a.mode, len(results)
        if len(results) > 1:
            result["runs_summary"] = summarize_runs(results)
    text = json.dumps(result, ensure_ascii=False, indent=1)
    if a.out:
        a.out.write_text(text, encoding="utf-8")
    brief = {k: result[k] for k in ("rows", "units", "M1", "M2", "M3", "M4", "M5_M6", "M7", "M8", "runs_summary") if k in result}
    print(json.dumps(brief, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
