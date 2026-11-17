"""④ 문장 종류: 지시 / 진행 보고 / 완료 보고. 문장 끝 규칙이 먼저, 애매하면 unsure=True(⑥ LLM 몫).

업무일지는 관리자가 그날 할 일을 적는 곳이라 명사로 끝나면 지시다.
"""
from __future__ import annotations

import re

INSTRUCT, PROGRESS, DONE = "지시", "진행 보고", "완료 보고"

_PROGRESS = re.compile(r"(?:진행|작성|제작|정리|작업|하는|입력|확인|검토|수정)\s*중(?!\s*에)")
_DONE = re.compile(r"(?:했|하였|마쳤|끝냈|올렸|보냈|만들었|드렸)\s*(?:습니다|어요|음)\s*[.!]*\s*$"
                   r"|완료\s*(?:했|됐|되었)\s*(?:습니다|어요|다)"
                   r"|(?<![가-힣])완료\s*[.!]*\s*$")
_INSTRUCT = re.compile(r"(주세요|세요|해 ?줘|해 ?주시|부탁|바랍니다|바래요|해야|하세요|해요|합니다|할 ?것|하기|드립니다|보내|알려|말아)")


def sentence_kind(text: str) -> tuple[str, bool]:
    """→ (문장 종류, unsure). 보고 표현과 지시 표현이 같이 있으면 unsure."""
    body = re.sub(r"\[[^\]]*\]|\([^)]*(?:예시|예:)[^)]*\)", " ", text or "")
    lines = [l.strip() for l in body.splitlines() if l.strip()]
    flat = " ".join(lines)
    progress = bool(_PROGRESS.search(flat))
    done = any(_DONE.search(l) for l in lines)
    if progress and done:
        return PROGRESS, False
    if done and _INSTRUCT.search(flat):
        return INSTRUCT, True          # 지시 글에 '표기했습니다'처럼 지난 일을 설명한 것일 수 있다
    if progress or done:
        kind = PROGRESS if progress else DONE
        # 지시 표현이 마지막 줄 끝에 있으면 지시 문장에 보고 낱말이 섞인 것
        tail_instruct = bool(_INSTRUCT.search(lines[-1][-14:])) if lines else False
        if tail_instruct:
            return INSTRUCT, True
        return kind, False
    return INSTRUCT, False
