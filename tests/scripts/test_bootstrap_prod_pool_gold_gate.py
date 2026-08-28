"""promotion 的 gold 执行门禁：只认 👍 是不够的，SQL 得真的解出了东西。

存在理由：`d525ae0fc0e3`「上海分行 2026 年 5 月的反洗钱告警数量」是被 👍 过、
进了池子、又在 2026-08-28 被剔除的样本——它的 gold 实测返回 `alert_count = 0`，
因为 `dim_branch.city` 根本没有 '上海'。返回空的样本当 few-shot 是有害的：
它教模型去 filter 一个库里不存在的值。这个门禁把那次人工排查固化下来。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "bootstrap_prod_pool.py"
_spec = importlib.util.spec_from_file_location("bootstrap_prod_pool", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def _ex(eid: str, sql: str = "SELECT 1"):
    from chat_bi_agent.agents.shared.example_retriever import QAExample

    return QAExample(
        example_id=eid,
        question=f"q_{eid}",
        sql=sql,
        dialect="postgres",
        source="test",
        tags=[],
        ts="2026-08-28T00:00:00Z",
        embedding=None,
    )


def test_keeps_example_whose_gold_returns_rows():
    kept, rejected = _mod.reject_empty_gold([_ex("a")], lambda sql: ([{"n": 7}], None))
    assert [e.example_id for e in kept] == ["a"]
    assert rejected == []


def test_rejects_empty_result_set():
    kept, rejected = _mod.reject_empty_gold([_ex("a")], lambda sql: ([], None))
    assert kept == []
    assert [e.example_id for e, _ in rejected] == ["a"]


def test_rejects_aggregate_over_nothing():
    """d525ae0fc0e3 那一类——**它返回的是一行，不是空集**。

    `COUNT(...)` 匹配不到任何行时返回单行 0，只判「空集」会漏掉它，
    而它恰恰是这个门禁存在的全部理由。
    """
    kept, rejected = _mod.reject_empty_gold([_ex("a")], lambda sql: ([{"alert_count": 0}], None))
    assert kept == []
    assert "没有任何行" in rejected[0][1] or "空" in rejected[0][1]


def test_rejects_sql_that_errors():
    kept, rejected = _mod.reject_empty_gold([_ex("a")], lambda sql: (None, 'column "x" 不存在'))
    assert kept == []
    assert "x" in rejected[0][1]


def test_zero_among_other_values_is_kept():
    """分组结果里某一组是 0 是正常的，不能误杀。"""
    kept, _ = _mod.reject_empty_gold(
        [_ex("a")], lambda sql: ([{"g": "A", "n": 0}, {"g": "B", "n": 5}], None)
    )
    assert [e.example_id for e in kept] == ["a"]


def test_single_row_with_a_nonzero_value_is_kept():
    kept, _ = _mod.reject_empty_gold([_ex("a")], lambda sql: ([{"n": 0, "amt": 12.5}], None))
    assert [e.example_id for e in kept] == ["a"]


def test_connection_failure_raises_rather_than_silently_passing():
    """探针连不上时必须炸。

    静默放行等于门禁自动失效——「不验证门禁会不会红的门禁不算门禁」。
    宁可让 nightly cron 失败，也不要往池子里灌未经验证的样本。
    """

    def dead(sql):
        raise ConnectionError("could not connect to server: 5433")

    with pytest.raises(ConnectionError):
        _mod.reject_empty_gold([_ex("a")], dead)
