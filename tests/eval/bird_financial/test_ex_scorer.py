"""Tests for BIRD EX scorer."""

from __future__ import annotations

from chat_bi_agent.eval.bird_financial.ex_scorer import rows_equal, rows_hash, score_ex


def test_rows_equal_ignores_order() -> None:
    a = [(1, "x"), (2, "y")]
    b = [(2, "y"), (1, "x")]
    assert rows_equal(a, b)


def test_rows_equal_folds_float_to_int_when_whole() -> None:
    # BIRD official evaluator does this — 5.0 vs 5 must compare equal
    assert rows_equal([(5.0,)], [(5,)])


def test_rows_equal_distinguishes_non_whole_floats() -> None:
    assert not rows_equal([(5.5,)], [(5,)])


def test_rows_equal_handles_null() -> None:
    assert rows_equal([(1, None)], [(1, None)])
    assert not rows_equal([(1, None)], [(1, 0)])


def test_score_ex_matches_primary() -> None:
    assert score_ex([(1,)], [(1,)]) == 1


def test_score_ex_matches_tied_alternate() -> None:
    predicted = [("A",)]
    gold = [("B",)]
    tied = [[("C",)], [("A",)]]  # one alt matches
    assert score_ex(predicted, gold, tied_alternates=tied) == 1


def test_score_ex_returns_zero_when_no_match() -> None:
    assert score_ex([(1,)], [(2,)], tied_alternates=[[(3,)]]) == 0


def test_rows_hash_is_order_independent_and_stable() -> None:
    a = [(1, "x"), (2, "y")]
    b = [(2, "y"), (1, "x")]
    assert rows_hash(a) == rows_hash(b)
    # Different content must differ
    assert rows_hash(a) != rows_hash([(1, "x")])
