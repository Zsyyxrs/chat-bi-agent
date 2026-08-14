"""近似泄题守门：leave-one-out 不能只挡精确文本。

实测动机（2026-08-14）：生产 pool 31 条 vs 34 题评测集，**8 道题在池里有
cosine>0.85 的近似副本**，其中 1 道逐字相同。原来的 leave-one-out 只按精确文本
排除，只能挡住那 1 道；其余 7 道会被当成范例喂回去——等于开卷考试。
拿这种数字回答"同域 few-shot 加不加分"是白跑。

刻意做成**可选、默认关**：生产环境里"历史相似问题的 Q-SQL 对"正是 few-shot 的
价值所在，不该屏蔽；只有评测时才需要这道闸。
"""

from unittest.mock import MagicMock

from chat_bi_agent.agents.shared.example_retriever import (
    ExamplePool,
    ExampleRetriever,
    QAExample,
)


def _pool(*specs):
    return ExamplePool(
        [
            QAExample(
                example_id=f"e{i}",
                question=q,
                sql="SELECT 1",
                dialect="postgres",
                source="test",
                embedding=vec,
            )
            for i, (q, vec) in enumerate(specs)
        ]
    )


def _retriever(pool, **kw):
    # 问题向量固定为 [1,0]；池内各条用自己的 embedding
    return ExampleRetriever(
        pool=pool,
        dialect="postgres",
        embed_fn=MagicMock(side_effect=lambda t: [[1.0, 0.0] for _ in t]),
        min_similarity=0.0,
        max_k=5,
        **kw,
    )


def test_without_guard_near_duplicate_is_returned():
    """默认行为不变：近似副本照常召回（生产场景要的就是这个）。"""
    pool = _pool(("近似副本", [1.0, 0.0]), ("不相关", [0.0, 1.0]))
    hits = _retriever(pool).retrieve("原题")
    assert "近似副本" in [ex.question for ex, _ in hits]


def test_guard_drops_near_duplicates():
    """开了守门后，与当前问题过于相似的池内条目被剔除。"""
    pool = _pool(("近似副本", [1.0, 0.0]), ("有点像", [0.8, 0.6]))
    hits = _retriever(pool, leak_guard_similarity=0.95).retrieve("原题")
    qs = [ex.question for ex, _ in hits]
    assert "近似副本" not in qs  # cos=1.0 >= 0.95 → 剔除
    assert "有点像" in qs  # cos=0.8 < 0.95 → 保留，仍是有用范例


def test_guard_keeps_everything_below_threshold():
    pool = _pool(("弱相关", [0.5, 0.866]))
    hits = _retriever(pool, leak_guard_similarity=0.95).retrieve("原题")
    assert len(hits) == 1


def test_guard_can_empty_the_result():
    """全是近似副本时应返空，而不是硬塞——宁可没有 few-shot 也不要泄题。"""
    pool = _pool(("副本1", [1.0, 0.0]), ("副本2", [1.0, 0.0]))
    assert _retriever(pool, leak_guard_similarity=0.95).retrieve("原题") == []
