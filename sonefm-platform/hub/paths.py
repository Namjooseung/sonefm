"""데이터 위치와 설정 파일 (Documents\\SoneFM\\platform\\config.json)."""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path
from typing import Any

HOME_ENV = "SONEFM_HOME"   # 테스트는 이 값을 임시 폴더로 바꿔 실제 데이터를 건드리지 않는다

DEFAULT_CONFIG: dict[str, Any] = {
    "app_dirs": [],            # 개발 중인 앱 저장소 폴더 (설치된 앱은 자동으로 찾는다)
    "scan_installed": True,    # %LOCALAPPDATA%\Programs, %ProgramFiles% 에서 앱 찾기
    "worklog_folder": "",      # 드라이브 데스크톱 앱이 동기화하는 업무일지 폴더
    "worklog_url": "",         # 업무일지 시트 링크 (있으면 폴더보다 먼저)
    "schedule_inbox": "",      # 편성 직원이 '데이터 내보내기'한 .db가 오는 폴더
    "stale_days": 3,           # 업무일지에 이 날수 안의 날짜가 없으면 경고
    "ai": {                    # AI_DESIGN.md 4.3. 단가 null = 모름 → 그 모델 호출은 막힌다. 키는 여기 두지 않는다
        "enabled": True,
        "models": {"light": "claude-haiku-5-5", "heavy": "claude-sonnet-5-5"},
        # USD/백만 토큰. Anthropic 공식 가격, 2026-11 확인. 바뀌면 설정 화면에서 수정
        # shortcut: haiku는 10만 토큰 이하 요청 기준 단가(넘으면 더 비쌈), 긴 입력 기능을 붙일 때 단가 구조를 늘린다
        "prices": {
            "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "cache_write": 0.125, "cache_read": 0.01},
            "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache_write": 2.50, "cache_read": 0.10},
        },
        "effort": {"light": "low", "heavy": "medium"},   # low|medium|high, 등급별 output_config.effort
        "limits": {"daily_usd": 1.0, "monthly_usd": 20.0},
        "masking": {"enabled": True, "names": []},
        "dev_log": False,
    },
}


def _merge(base: Any, over: Any) -> Any:
    """base 모양을 지키며 over를 덮어 쓴다 (빠진 하위 키는 기본값). 딕셔너리만 깊게 합친다."""
    if not isinstance(base, dict) or not isinstance(over, dict):
        return copy.deepcopy(over)
    out = copy.deepcopy(base)
    for k, v in over.items():
        out[k] = _merge(base[k], v) if k in base else copy.deepcopy(v)
    return out


def _fill_null_prices(ai: dict[str, Any]) -> dict[str, Any]:
    """저장된 단가가 null이면 기본값을 쓴다(숫자를 넣은 값이 우선). 한도는 null이 '무제한'이라 건드리지 않는다."""
    for m, row in ai["prices"].items():
        for k, v in row.items():
            if v is None:
                ai["prices"][m][k] = (DEFAULT_CONFIG["ai"]["prices"].get(m) or {}).get(k)
    return ai


def home() -> Path:
    override = os.environ.get(HOME_ENV)
    if override:
        return Path(override)
    return Path(os.environ.get("USERPROFILE", Path.home())) / "Documents" / "SoneFM"


def platform_dir() -> Path:
    d = home() / "platform"
    d.mkdir(parents=True, exist_ok=True)
    return d


def resource_dir() -> Path:
    """설치판은 PyInstaller 압축 폴더, 개발 중에는 저장소 루트."""
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else Path(__file__).resolve().parent.parent


def recipe_dirs() -> list[Path]:
    """기본 레시피 → 사용자 레시피 순 (같은 id면 뒤가 이긴다)."""
    user = platform_dir() / "recipes"
    user.mkdir(exist_ok=True)
    return [resource_dir() / "recipes", user]


def config_path() -> Path:
    return platform_dir() / "config.json"


def load_config() -> dict[str, Any]:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    p = config_path()
    if p.exists():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            cfg.update({k: v for k, v in raw.items() if k in DEFAULT_CONFIG and k != "ai"})
            if isinstance(raw.get("ai"), dict):
                cfg["ai"] = _fill_null_prices(_merge(DEFAULT_CONFIG["ai"], raw["ai"]))
        except (OSError, ValueError):
            pass   # 깨진 설정이면 기본값으로 연다 (화면 설정에서 다시 저장)
    return cfg


def save_config(cfg: dict[str, Any]) -> dict[str, Any]:
    clean = {k: cfg.get(k, v) for k, v in DEFAULT_CONFIG.items() if k != "ai"}
    clean["ai"] = _merge(DEFAULT_CONFIG["ai"], cfg.get("ai") or {})
    clean["app_dirs"] = [str(x).strip() for x in clean["app_dirs"] if str(x).strip()]
    try:
        clean["stale_days"] = max(1, int(clean["stale_days"]))
    except (TypeError, ValueError):
        clean["stale_days"] = DEFAULT_CONFIG["stale_days"]
    config_path().write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    return clean
