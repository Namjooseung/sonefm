from datetime import date

from app.domain.journal import D_LIMIT, fit_text, header_text, journal_days, sheet_title, text_px


def test_달력_날짜_기준_한달_전부():
    # 11/1(일)은 10월 소속 주에 있지만 11월 일지에 들어간다
    cells = {(date(2026, 11, 1), h): (f"프로_{h}화", "교양") for h in range(6, 23)}
    j = journal_days(2026, 11, cells)
    assert len(j.days) == 30 and j.days[0].day == date(2026, 11, 1)
    assert j.days[0].slots[0] == ("프로_6화", "교양")
    # 11/2부터는 칸이 없으니 빈칸 경고 (29일 × 17칸)
    assert len(j.warnings) == 29 * 17 and j.warnings[0] == "11/2 6시 빈칸"


def test_분야_없음_경고():
    j = journal_days(2026, 2, {(date(2026, 2, 1), 6): ("굿모닝!", None)})
    assert "2/1 6시 굿모닝!: 방송분야 없음" in j.warnings


def test_머리말과_시트이름():
    assert header_text(date(2026, 10, 1)) == "2026년 10 월 1 일 목요일_x000D_\n전체방송시간 1,020 분"
    assert sheet_title(date(2026, 10, 1)) == "261001"


def test_짧은_이름은_그대로():
    assert fit_text("수원은 지금_75화") == "수원은 지금_75화"
    assert fit_text("수원은  지금\n_75화") == "수원은 지금 _75화"     # 공백·줄바꿈 정리


def test_긴_이름은_두줄_대괄호_뒤_우선():
    s = "[416재단 재난약자 안전보장사업] 오늘도 안녕하세요_10화(재)"
    assert text_px(s) > D_LIMIT
    assert fit_text(s) == "[416재단 재난약자 안전보장사업]\n오늘도 안녕하세요_10화(재)"


def test_공백_없으면_회차_앞에서_끊음():
    s = "가나다라마바사아자차카타파하가나다라마바사아자_123화(재)"
    assert fit_text(s) == "가나다라마바사아자차카타파하가나다라마바사아자\n_123화(재)"
