"""이 PC에 한글이 깔려 있으면 hwpx를 hwp로도 저장 (한글 자동화, PowerShell 경유 — 새 패키지 없음).

한글이 없거나 실패하면 False. 보고서는 hwpx만으로도 완성이므로 실패해도 오류로 보지 않는다.
"""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)

_PS = r"""
$ErrorActionPreference = 'Stop'
$hwp = New-Object -ComObject HWPFrame.HwpObject
try {
  $hwp.XHwpWindows.Item(0).Visible = $false
  if (-not $hwp.Open($env:SONEFM_SRC, 'HWPX', 'forceopen:true')) { exit 2 }
  if (-not $hwp.SaveAs($env:SONEFM_DST, 'HWP', '')) { exit 3 }
} finally { $hwp.Clear(1); $hwp.Quit() }
"""


def save_as_hwp(src: str | Path, dst: str | Path, timeout: int = 90) -> bool:
    if sys.platform != "win32":
        return False
    import os
    env = {**os.environ, "SONEFM_SRC": str(src), "SONEFM_DST": str(dst)}
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS], env=env,
                           capture_output=True, timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        log.exception("한글 hwp 저장 실패")
        return False
    if r.returncode != 0 or not Path(dst).exists():
        log.warning("한글 hwp 저장 실패 (%s): %s", r.returncode, r.stderr.decode("utf-8", "replace")[-500:])
        return False
    return True
