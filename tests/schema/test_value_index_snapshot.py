"""值检索快照的漂移闸。

`schema/value_index.json` 是 `scripts/build_value_index.py` 从库里生成的产物，
提交进 git 是为了让 `commit_hash` 真正覆盖这份输入（`verify_ab.py` 把 commit_hash
列为 CRITICAL 字段，前提是「同一 commit ⇒ 同样的输入」）。代价是它会漂移：

  1. 有人给新列标了 `value_index: true` 但忘了重跑生成脚本 → 第一道闸（不需要库）
  2. reseed 或改了 dimension_generator 之后忘了重跑 → 第二道闸（需要库）

两者的失败都是**静默**的：快照对不上只会让注入的 examples 变少或过期，分数悄悄
下滑，日志里什么都看不到。所以宁可用测试挡住。
"""

import json

import pytest
import yaml

from chat_bi_agent.agents.shared.sql_executor import SQLExecutor
from chat_bi_agent.schema.loader import DEFAULT_YAML_PATH
from chat_bi_agent.schema.value_index import DEFAULT_CACHE_PATH
from tests.pg_probe import PG_UP, SKIP_REASON


def _marked_columns() -> set[str]:
    """schema_docs.yaml 里标了 value_index: true 的列。"""
    data = yaml.safe_load(DEFAULT_YAML_PATH.read_text(encoding="utf-8"))
    return {
        f"{table['name']}.{col['name']}"
        for table in data["tables"]
        for col in table["columns"]
        if col.get("value_index")
    }


def _snapshot() -> dict[str, list[str]]:
    return json.loads(DEFAULT_CACHE_PATH.read_text(encoding="utf-8"))


def test_snapshot_covers_exactly_the_marked_columns():
    """标了就得有快照，快照里也不该有已经取消标记的列。"""
    assert set(_snapshot()) == _marked_columns(), (
        "value_index.json 与 schema_docs.yaml 的 value_index 标记不一致，"
        "请重跑 scripts/build_value_index.py"
    )


def test_snapshot_has_no_empty_column():
    """空列说明生成时那张表还没 seed，属于坏快照。"""
    empty = [col for col, values in _snapshot().items() if not values]
    assert not empty, f"这些列的取值为空，快照可能是在空库上生成的: {empty}"


@pytest.mark.integration
@pytest.mark.skipif(not PG_UP, reason=SKIP_REASON)
def test_snapshot_matches_live_database():
    """需要跑着的 PG + seed 数据：快照取值必须与库内 DISTINCT 完全一致。

    连通性由 `pg_probe` 在模块级判定一次。库起着却查不动某一列，是真失败而不是
    skip——「查询报错就跳过」会把漂移伪装成「环境没准备好」，正是本项目反复
    出现的那类静默失效。
    """
    executor = SQLExecutor(statement_timeout_ms=30_000)
    stale: list[str] = []
    for qualified, cached in _snapshot().items():
        table, _, column = qualified.partition(".")
        rows, err = executor.execute(
            f"SELECT DISTINCT {column} AS v FROM {table} WHERE {column} IS NOT NULL"
        )
        assert err is None, f"{qualified} 查询失败（库是通的，所以这是真失败）: {err}"
        live = {str(r["v"]) for r in (rows or [])}
        if live != set(cached):
            missing = sorted(live - set(cached))[:5]
            extra = sorted(set(cached) - live)[:5]
            stale.append(
                f"{qualified}: 快照 {len(cached)} 个 / 库内 {len(live)} 个"
                f"（快照缺 {missing}；快照多出 {extra}）"
            )

    assert not stale, "快照已与库漂移，请重跑 scripts/build_value_index.py：" + "; ".join(stale)
