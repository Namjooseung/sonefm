"""API 키 보관: Windows 자격 증명 관리자(keyring)에만 둔다. 환경 변수·파일·로그·예외에 키를 쓰지 않는다."""
from __future__ import annotations

import logging

import keyring
from keyring.errors import PasswordDeleteError

log = logging.getLogger("hub.ai")
SERVICE = "SoneFM.Platform"
ITEM = "anthropic"
MIN_LEN, MAX_LEN = 20, 500


class KeyRejected(ValueError):
    """사용자에게 그대로 보여 줄 한국어 메시지."""


def check_key(key: str) -> str:
    k = (key or "").strip()
    if not k:
        raise KeyRejected("API 키가 비어 있습니다. 키를 붙여 넣어 주세요.")
    if any(c.isspace() for c in k):
        raise KeyRejected("API 키에 공백이나 줄바꿈이 들어 있습니다. 키만 다시 복사해 붙여 넣어 주세요.")
    if len(k) < MIN_LEN:
        raise KeyRejected(f"API 키가 너무 짧습니다({MIN_LEN}자 이상). 끝까지 복사했는지 확인해 주세요.")
    if len(k) > MAX_LEN:
        raise KeyRejected(f"API 키가 너무 깁니다({MAX_LEN}자 이하). 키만 복사했는지 확인해 주세요.")
    return k


def get_key() -> str | None:
    try:
        return keyring.get_password(SERVICE, ITEM) or None
    except Exception as e:  # noqa: BLE001  백엔드 오류가 앱을 죽이면 안 된다. 종류만 남기고 키는 남기지 않는다
        log.warning("자격 증명 관리자를 읽지 못했습니다(%s). 키 없음으로 처리합니다.", type(e).__name__)
        return None


def has_key() -> bool:
    return get_key() is not None


def save_key(key: str) -> None:
    """거절되면 KeyRejected, 저장소 오류면 RuntimeError(고정 한국어 메시지)."""
    k = check_key(key)
    try:
        keyring.set_password(SERVICE, ITEM, k)
    except Exception as e:  # noqa: BLE001
        log.warning("자격 증명 관리자에 저장하지 못했습니다(%s).", type(e).__name__)
        raise RuntimeError("Windows 자격 증명 관리자에 저장하지 못했습니다. Windows에 로그인한 사용자로 실행했는지 확인해 주세요.") from None


def delete_key() -> None:
    try:
        keyring.delete_password(SERVICE, ITEM)
    except PasswordDeleteError:
        pass   # 이미 없음
    except Exception as e:  # noqa: BLE001
        log.warning("자격 증명 관리자에서 지우지 못했습니다(%s).", type(e).__name__)
        raise RuntimeError("Windows 자격 증명 관리자에서 키를 지우지 못했습니다. 자격 증명 관리자에서 SoneFM.Platform 항목을 직접 지워 주세요.") from None
