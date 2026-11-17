from app.domain.excel_style import cell_fill, is_red_text


def test_오락은_본방_재방_모두_청록():
    assert cell_fill("오락", False) == "entertainment"
    assert cell_fill("오락", True) == "entertainment"


def test_교양_재방만_연노랑():
    assert cell_fill("교양", True) == "rerun"
    assert cell_fill("교양", False) is None


def test_붉은_글자_조건():
    assert is_red_text("본방", True, False, False)
    assert not is_red_text("재방", True, False, False)
    assert not is_red_text("본방", True, True, False)      # 특집
    assert not is_red_text("본방", True, False, True)      # 고정 편성
    assert not is_red_text("본방", False, False, False)    # 회차 숨김
