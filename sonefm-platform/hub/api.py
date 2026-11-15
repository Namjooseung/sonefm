"""화면(ui/app.js)이 부르는 함수 모음: window.pywebview.api.*

오래 걸리는 레시피 실행은 작업 스레드에서 돌리고, 화면은 run_status로 진행률을 가져간다.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import webbrowser
from datetime import date
from pathlib import Path
from typing import Any, Callable

from hub import APP_NAME, APP_VERSION, db, registry, schedule_inbox, worklog
from hub.ai import gateway, keys
from hub.ai import settings as ai_settings
from hub.paths import home, load_config, platform_dir, recipe_dirs, save_config
from hub.recipes import Classifier, RecipeError, coerce_inputs, form, keyword_classifier, load_recipes, run_recipe

log = logging.getLogger("sonefm.platform")
RECENT_UNDATED = 50


class Api:
    def __init__(self, today: Callable[[], date] = date.today, classifier: Classifier = keyword_classifier,
                 download: Callable[[str], bytes] = worklog.download_xlsx):
        self._today = today
        self._classify = classifier
        self._download = download
        self._worklog: dict[str, Any] = {"tasks": [], "label": "아직 불러오지 않음", "last_date": None, "warning": None}
        self._schedule: dict[str, Any] = {"path": "", "label": "아직 불러오지 않음", "received_at": None, "warning": None}
        self._runs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._window = None   # main.py가 넣는다 (파일 고르기 창). 공개 속성이면 pywebview가 훑다가 죽는다

    # ── 목록 ─────────────────────────────────────
    def _apps(self) -> dict[str, registry.App]:
        cfg = load_config()
        return registry.discover(cfg["app_dirs"], None if cfg["scan_installed"] else [])

    def _recipes(self) -> tuple[dict[str, dict[str, Any]], list[str]]:
        return load_recipes(recipe_dirs())

    def refresh(self) -> dict[str, Any]:
        """업무일지와 편성 파일을 새로 받는다."""
        cfg = load_config()
        self._worklog = worklog.load(cfg, self._today(), self._download)
        try:
            self._schedule = schedule_inbox.receive(cfg, self._apps())
        except Exception as e:  # noqa: BLE001  편성 받기 실패가 화면 전체를 막지 않게
            log.exception("편성 받기 실패")
            self._schedule = {**self._schedule, "warning": f"편성 파일을 받지 못했습니다: {e}"}
        return self.state()

    def _tasks(self, recipes: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        today = self._today().isoformat()
        marks, last = db.marks(), db.last_runs_by_task()
        src = [t for t in self._worklog["tasks"] if t["date"] == today]
        undated = [t for t in self._worklog["tasks"] if t["date"] is None and t["key"] not in marks]
        src += undated[-RECENT_UNDATED:]
        src += [{"key": f"m-{m['id']}", "date": today, "text": m["text"], "source": "manual", "id": m["id"]}
                for m in db.manual_for(today)]
        running = {r["task_key"]: r for r in self._runs.values() if not r["done"]}
        ordered = list(recipes.values())
        out = []
        for t in src:
            rid = self._classify(t["text"], ordered)
            r = running.get(t["key"])
            out.append({**t, "recipe_id": rid, "recipe_name": recipes[rid]["name"] if rid else "수동",
                        "mark": marks.get(t["key"]), "last_run": last.get(t["key"]),
                        "running": {"run_id": r["id"], "pct": r["pct"], "message": r["message"]} if r else None})
        return out

    def state(self) -> dict[str, Any]:
        recipes, problems = self._recipes()
        apps = self._apps()
        return {
            "app": {"name": APP_NAME, "version": APP_VERSION}, "today": self._today().isoformat(),
            "worklog": {k: v for k, v in self._worklog.items() if k != "tasks"},
            "schedule": self._schedule,
            "tasks": self._tasks(recipes),
            "recipes": [{"id": r["id"], "name": r["name"], "summary": r.get("summary", "")} for r in recipes.values()],
            "recipe_problems": problems,
            "apps": [a.summary() for a in apps.values()],
            "history": db.recent_runs(15),
        }

    def _find_task(self, key: str) -> dict[str, Any] | None:
        recipes, _ = self._recipes()
        return next((t for t in self._tasks(recipes) if t["key"] == key), None)

    # ── 업무 ─────────────────────────────────────
    def add_task(self, text: str) -> dict[str, Any]:
        if not (text or "").strip():
            return {"ok": False, "message": "업무 내용을 입력하세요."}
        db.add_manual(self._today().isoformat(), text)
        return {"ok": True}

    def delete_task(self, task_id: str) -> dict[str, Any]:
        db.delete_manual(task_id)
        return {"ok": True}

    def mark(self, key: str, done: bool) -> dict[str, Any]:
        t = self._find_task(key)
        db.mark(key, "완료" if done else None, t["text"] if t else "")
        return {"ok": True}

    # ── 레시피 실행 ───────────────────────────────
    def _ctx(self, task: dict[str, Any]) -> dict[str, Any]:
        today = self._today().isoformat()
        return {"task": {**task, "date": task.get("date") or today}, "today": today,
                "home": str(home()).replace("\\", "/"), "schedule_db": self._schedule.get("path", "")}

    def form(self, key: str, recipe_id: str) -> dict[str, Any]:
        recipes, _ = self._recipes()
        task, recipe = self._find_task(key), recipes.get(recipe_id)
        if task is None or recipe is None:
            return {"ok": False, "message": "업무나 레시피를 찾지 못했습니다. 새로고침하세요."}
        remembered = db.get_meta(f"remember:{recipe_id}", {})
        return {"ok": True, "recipe": {"id": recipe["id"], "name": recipe["name"], "summary": recipe.get("summary", "")},
                "fields": form(recipe, self._ctx(task), remembered)}

    def start(self, key: str, recipe_id: str, values: dict[str, Any]) -> dict[str, Any]:
        recipes, _ = self._recipes()
        task, recipe = self._find_task(key), recipes.get(recipe_id)
        if task is None or recipe is None:
            return {"ok": False, "message": "업무나 레시피를 찾지 못했습니다. 새로고침하세요."}
        if task.get("running"):
            return {"ok": False, "message": "이미 실행 중입니다."}
        try:
            inputs = coerce_inputs(recipe, values or {})
        except RecipeError as e:
            return {"ok": False, "message": str(e)}
        keep = {s["name"]: inputs[s["name"]] for s in recipe.get("inputs", []) if s.get("remember")}
        if keep:
            db.set_meta(f"remember:{recipe_id}", keep)
        run_id = db.start_run(task, recipe_id)
        state = {"id": run_id, "task_key": key, "done": False, "pct": 0, "message": "시작", "result": None}
        with self._lock:
            self._runs[run_id] = state
        ctx = {**self._ctx(task), "input": inputs}
        apps = self._apps()

        def work() -> None:
            def on_event(e: dict[str, Any]) -> None:
                state.update(pct=e["pct"], message=e["message"])
            try:
                result = run_recipe(recipe, ctx, apps, platform_dir() / "runs" / run_id, on_event=on_event)
            except Exception as e:  # noqa: BLE001
                log.exception("레시피 실행 오류")
                result = {"status": "error", "steps": [], "actions": {}, "failed_step": None,
                          "message": f"플랫폼 오류: {e}"}
            db.finish_run(run_id, result)
            if result["status"] == "ok":
                db.mark(key, "완료", task["text"])
            log.info("실행 %s %s %s: %s", run_id, recipe_id, result["status"], result["message"])
            state.update(done=True, pct=100, message=result["message"], result=result)

        threading.Thread(target=work, daemon=True).start()
        return {"ok": True, "run_id": run_id}

    def run_status(self, run_id: str) -> dict[str, Any]:
        r = self._runs.get(run_id)
        return dict(r) if r else {"done": True, "result": None, "message": "실행 기록이 없습니다."}

    # ── 바깥 열기 ─────────────────────────────────
    def pick_file(self, current: str = "") -> str:
        import webview
        folder = str(Path(current).parent) if current else ""
        picked = self._window.create_file_dialog(webview.OPEN_DIALOG, directory=folder) if self._window else None
        return picked[0] if picked else ""

    def pick_folder(self, current: str = "") -> str:
        import webview
        picked = self._window.create_file_dialog(webview.FOLDER_DIALOG, directory=current or "") if self._window else None
        return picked[0] if picked else ""

    def open_path(self, path: str) -> dict[str, Any]:
        p = Path(path)
        if not p.exists():
            return {"ok": False, "message": f"찾지 못했습니다: {path}"}
        os.startfile(p)  # noqa: S606  Windows 탐색기·기본 프로그램으로 연다
        return {"ok": True}

    def open_url(self, url: str) -> dict[str, Any]:
        if not url.startswith("https://"):
            return {"ok": False, "message": "https 주소만 엽니다."}
        webbrowser.open(url)
        return {"ok": True}

    def open_app(self, app_id: str) -> dict[str, Any]:
        app = self._apps().get(app_id)
        if app is None or not app.gui:
            return {"ok": False, "message": "이 앱은 화면을 열 수 없습니다."}
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        subprocess.Popen(app.gui, cwd=app.dir, creationflags=flags, close_fds=True)
        return {"ok": True}

    # ── 설정 ─────────────────────────────────────
    def settings(self) -> dict[str, Any]:
        return {"config": load_config(), "data_dir": str(platform_dir()),
                "recipe_dir": str(recipe_dirs()[-1])}

    def save_settings(self, cfg: dict[str, Any]) -> dict[str, Any]:
        if isinstance(cfg.get("app_dirs"), str):
            cfg["app_dirs"] = cfg["app_dirs"].splitlines()
        if cfg.get("worklog_url") and not worklog.sheet_id_from_link(cfg["worklog_url"]):
            return {"ok": False, "message": "업무일지 링크가 구글 시트 주소가 아닙니다."}
        save_config({**load_config(), **cfg})
        return {"ok": True}

    # ── AI (키 값은 어떤 응답에도 담지 않는다) ─────────
    def _guard(self, fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        try:
            return fn()
        except (ValueError, RuntimeError) as e:   # keys·settings가 던지는 한국어 메시지
            return {"ok": False, "errors": [str(e)]}
        except Exception:  # noqa: BLE001
            log.exception("AI 설정 처리 오류")
            return {"ok": False, "errors": ["예상하지 못한 문제가 생겼습니다. 로그(platform.log)를 확인하거나 담당자에게 알려 주세요."]}

    def ai_status(self) -> dict[str, Any]:
        return self._guard(lambda: {"ok": True, **ai_settings.status()})

    def ai_save_key(self, key: str) -> dict[str, Any]:
        def go() -> dict[str, Any]:
            keys.save_key(key)
            return {"ok": True, "message": "API 키를 저장했습니다."}
        return self._guard(go)

    def ai_delete_key(self) -> dict[str, Any]:
        def go() -> dict[str, Any]:
            keys.delete_key()
            return {"ok": True, "message": "API 키를 지웠습니다."}
        return self._guard(go)

    def ai_test(self) -> dict[str, Any]:
        def go() -> dict[str, Any]:
            try:
                r = gateway.complete("connection_test", "light", [{"role": "user", "content": "연결 확인입니다. 한 단어로 답해 주세요."}],
                                     max_tokens=16)
            except gateway.AIError as e:
                return {"ok": False, "kind": e.kind, "errors": [e.message]}
            return {"ok": True, "message": f"연결되었습니다. (모델 {r.model})"}
        return self._guard(go)

    def ai_save_settings(self, ai: dict[str, Any]) -> dict[str, Any]:
        def go() -> dict[str, Any]:
            ai_settings.save(ai if isinstance(ai, dict) else {})
            return {"ok": True, "message": "AI 설정을 저장했습니다."}
        return self._guard(go)
