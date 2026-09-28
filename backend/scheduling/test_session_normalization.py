from scheduling.solver.engine import normalize_session_pattern

def test_normalize_session_pattern_unit_blocks():
    assert normalize_session_pattern([1, 1, 1, 1], 4, 1) == [1, 1, 1, 1]
    assert normalize_session_pattern([1, 1, 1, 1], 3, 1) == [1, 1, 1]
    assert normalize_session_pattern([2, 2], 2, 2) == [2]
    assert normalize_session_pattern([2, 2], 0, 2) == []

def test_normalize_session_pattern_rejects_illegal_remainder():
    assert normalize_session_pattern([2, 2], 3, 2) is None
