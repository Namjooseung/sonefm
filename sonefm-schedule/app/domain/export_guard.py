"""드라이브로 편성을 보낼 때 막을지 판단하는 순수 함수(DB·파일을 만지지 않는다)."""
from __future__ import annotations

EMPTY_LOCAL = "이 PC 편성이 비어 있어 드라이브로 보내지 않았습니다. 먼저 [드라이브에서 가져오기]를 하세요."
DRIVE_AHEAD = "드라이브 편성이 이 PC보다 많아 자동으로 보내지 않았습니다. 맞다면 [드라이브로 보내기]를 눌러 주세요."


def last_month_of(max_date: str | None) -> str | None:
    """가장 늦은 칸 날짜(YYYY-MM-DD)에서 달(YYYY-MM). 칸이 없으면 None."""
    if not max_date or len(max_date) < 7:
        return None
    return max_date[:7]


def block_reason(local_cells: int, drive_cells: int | None, manual: bool) -> str | None:
    """보내면 안 되는 이유(사용자 문구). 보내도 되면 None.

    - 이 PC 편성 칸이 0이면 자동·수동 모두 막는다.
    - 자동일 때만, 드라이브(읽을 수 있을 때) 칸 수가 이 PC보다 많으면 막는다. 수동은 확인 뒤 보낼 수 있다.
    """
    if local_cells <= 0:
        return EMPTY_LOCAL
    if not manual and drive_cells is not None and drive_cells > local_cells:
        return DRIVE_AHEAD
    return None


def drive_is_ahead(local: dict, drive: dict | None) -> bool:
    """드라이브 편성이 이 PC보다 많거나 더 최근 달까지 있는가(수동 보내기 경고용)."""
    if not drive:
        return False
    if (drive.get("cells") or 0) > (local.get("cells") or 0):
        return True
    dl, ll = drive.get("last_month"), local.get("last_month")
    return bool(dl and (not ll or dl > ll))
