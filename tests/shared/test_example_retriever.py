"""ExamplePool + ExampleRetriever 单测：
- Pool: JSONL load/save, dedup by example_id, atomic append
- Retriever: cosine top-k, min_similarity threshold, dialect filter,
  tag allow/block, exclude_example_ids, exclude_question_texts
所有 embedding 用 stub fn 直接注入，避免真调 DashScope。
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from chat_bi_agent.agents.shared.example_retriever import (
    ExamplePool,
    ExampleRetriever,
    QAExample,
    compute_example_id,
)

# ----------------------- helpers -----------------------


def _mk_example(
    question: str,
    sql: str,
    dialect: str = "sqlite",
    tags: list[str] | None = None,
    source: str = "test",
    embedding: list[float] | None = None,
) -> QAExample:
    return QAExample(
        example_id=compute_example_id(question, sql),
        question=question,
        sql=sql,
        dialect=dialect,
        source=source,
        tags=tags or [],
        ts="2026-07-05T00:00:00",
        embedding=embedding or [1.0, 0.0, 0.0],
    )


def _stub_embed(text_to_vec: dict[str, list[float]]):
    def fn(texts: list[str]) -> list[list[float]]:
        return [text_to_vec[t] for t in texts]

    return fn


def _norm(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n else v


# ----------------------- QAExample / example_id -----------------------


def test_example_id_is_deterministic_and_short():
    a = compute_example_id("q?", "SELECT 1")
    b = compute_example_id("q?", "SELECT 1")
    c = compute_example_id("q?", "SELECT 2")
    assert a == b
    assert a != c
    assert len(a) == 12
    assert all(ch in "0123456789abcdef" for ch in a)


# ----------------------- ExamplePool -----------------------


def test_pool_load_save_roundtrip(tmp_path: Path):
    ex1 = _mk_example("q1", "SELECT 1", embedding=[0.1, 0.2, 0.3])
    ex2 = _mk_example("q2", "SELECT 2", embedding=[0.4, 0.5, 0.6])
    pool = ExamplePool([ex1, ex2])

    path = tmp_path / "pool.jsonl"
    pool.save(path)

    loaded = ExamplePool.load(path)
    assert len(loaded) == 2
    ids = {e.example_id for e in loaded.examples}
    assert ids == {ex1.example_id, ex2.example_id}
    reloaded = {e.example_id: e for e in loaded.examples}
    assert reloaded[ex1.example_id].embedding == [0.1, 0.2, 0.3]
    assert reloaded[ex1.example_id].tags == []


def test_pool_load_missing_file_returns_empty(tmp_path: Path):
    pool = ExamplePool.load(tmp_path / "does_not_exist.jsonl")
    assert len(pool) == 0


def test_pool_add_dedup_by_example_id():
    ex1 = _mk_example("q1", "SELECT 1")
    pool = ExamplePool([ex1])
    dup = _mk_example("q1", "SELECT 1")  # same id
    added = pool.add(dup)
    assert added is False
    assert len(pool) == 1

    other = _mk_example("q2", "SELECT 2")
    added2 = pool.add(other)
    assert added2 is True
    assert len(pool) == 2


def test_pool_append_persist_atomic(tmp_path: Path):
    path = tmp_path / "pool.jsonl"
    pool = ExamplePool([])
    pool.save(path)

    ex = _mk_example("q1", "SELECT 1", embedding=[0.9, 0.1, 0.0])
    pool.add(ex)
    pool.save(path)

    on_disk = path.read_text(encoding="utf-8").splitlines()
    assert len(on_disk) == 1
    payload = json.loads(on_disk[0])
    assert payload["question"] == "q1"
    assert payload["embedding"] == [0.9, 0.1, 0.0]


# ----------------------- ExampleRetriever -----------------------


def test_retriever_returns_topk_sorted_by_cosine():
    # Query vec [1,0,0] → nearest to ex_a's [1,0,0], farther from ex_c's [0,0,1]
    ex_a = _mk_example("close", "SELECT a", embedding=[1.0, 0.0, 0.0])
    ex_b = _mk_example("medium", "SELECT b", embedding=[0.8, 0.6, 0.0])
    ex_c = _mk_example("far", "SELECT c", embedding=[0.0, 0.0, 1.0])
    pool = ExamplePool([ex_a, ex_b, ex_c])

    retriever = ExampleRetriever(
        pool=pool,
        dialect="sqlite",
        embed_fn=_stub_embed({"query": [1.0, 0.0, 0.0]}),
        min_similarity=0.0,
        max_k=3,
    )
    results = retriever.retrieve("query")
    assert [r[0].question for r in results] == ["close", "medium", "far"]
    assert results[0][1] > results[1][1] > results[2][1]


def test_retriever_min_similarity_filters_below_threshold():
    ex_close = _mk_example("close", "SELECT a", embedding=[1.0, 0.0, 0.0])
    ex_far = _mk_example("far", "SELECT c", embedding=[0.0, 1.0, 0.0])  # cos 0
    pool = ExamplePool([ex_close, ex_far])

    retriever = ExampleRetriever(
        pool=pool,
        dialect="sqlite",
        embed_fn=_stub_embed({"q": [1.0, 0.0, 0.0]}),
        min_similarity=0.5,
        max_k=5,
    )
    results = retriever.retrieve("q")
    assert len(results) == 1
    assert results[0][0].question == "close"


def test_retriever_dialect_filter_excludes_wrong_dialect():
    ex_sqlite = _mk_example(
        "sqlite ex", "SELECT 1", dialect="sqlite", embedding=[1.0, 0.0, 0.0]
    )
    ex_pg = _mk_example(
        "pg ex", "SELECT 1 as x", dialect="postgres", embedding=[1.0, 0.0, 0.0]
    )
    pool = ExamplePool([ex_sqlite, ex_pg])

    retriever = ExampleRetriever(
        pool=pool,
        dialect="sqlite",
        embed_fn=_stub_embed({"q": [1.0, 0.0, 0.0]}),
        min_similarity=0.0,
        max_k=5,
    )
    results = retriever.retrieve("q")
    assert len(results) == 1
    assert results[0][0].dialect == "sqlite"


def test_retriever_allowed_tags_intersects():
    ex_a = _mk_example(
        "a", "SELECT a", tags=["bird_non_financial"], embedding=[1.0, 0.0, 0.0]
    )
    ex_b = _mk_example(
        "b", "SELECT b", tags=["self_generated"], embedding=[1.0, 0.0, 0.0]
    )
    pool = ExamplePool([ex_a, ex_b])

    retriever = ExampleRetriever(
        pool=pool,
        dialect="sqlite",
        embed_fn=_stub_embed({"q": [1.0, 0.0, 0.0]}),
        min_similarity=0.0,
        max_k=5,
        allowed_tags=["bird_non_financial"],
    )
    results = retriever.retrieve("q")
    assert len(results) == 1
    assert results[0][0].question == "a"


def test_retriever_blocked_tags_removes_match():
    ex_a = _mk_example(
        "a", "SELECT a", tags=["bird_dev_financial"], embedding=[1.0, 0.0, 0.0]
    )
    ex_b = _mk_example("b", "SELECT b", tags=["gold_p1"], embedding=[1.0, 0.0, 0.0])
    pool = ExamplePool([ex_a, ex_b])

    retriever = ExampleRetriever(
        pool=pool,
        dialect="sqlite",
        embed_fn=_stub_embed({"q": [1.0, 0.0, 0.0]}),
        min_similarity=0.0,
        max_k=5,
        blocked_tags=["bird_dev_financial"],
    )
    results = retriever.retrieve("q")
    assert [r[0].question for r in results] == ["b"]


def test_retriever_exclude_question_texts_dedups_same_question():
    """防止 BIRD dev-set 里 eval 题混进 few-shot（同题即泄题）。"""
    ex_leak = _mk_example(
        "How many clients?", "SELECT COUNT(*) FROM client", embedding=[1.0, 0.0, 0.0]
    )
    ex_ok = _mk_example(
        "Average balance?",
        "SELECT AVG(balance) FROM account",
        embedding=[0.9, 0.1, 0.0],
    )
    pool = ExamplePool([ex_leak, ex_ok])

    retriever = ExampleRetriever(
        pool=pool,
        dialect="sqlite",
        embed_fn=_stub_embed({"How many clients?": [1.0, 0.0, 0.0]}),
        min_similarity=0.0,
        max_k=5,
    )
    results = retriever.retrieve(
        "How many clients?", exclude_question_texts={"How many clients?"}
    )
    assert [r[0].question for r in results] == ["Average balance?"]


def test_retriever_k_override_caps_result_size():
    exs = [
        _mk_example(f"q{i}", f"SELECT {i}", embedding=[1.0, 0.0, 0.0])
        for i in range(5)
    ]
    pool = ExamplePool(exs)
    retriever = ExampleRetriever(
        pool=pool,
        dialect="sqlite",
        embed_fn=_stub_embed({"query": [1.0, 0.0, 0.0]}),
        min_similarity=0.0,
        max_k=5,
    )
    assert len(retriever.retrieve("query", k=2)) == 2


def test_retriever_returns_empty_on_empty_pool():
    pool = ExamplePool([])
    retriever = ExampleRetriever(
        pool=pool,
        dialect="sqlite",
        embed_fn=_stub_embed({"query": [1.0, 0.0, 0.0]}),
    )
    assert retriever.retrieve("query") == []


def test_retriever_normalizes_zero_vec_to_no_match():
    ex_zero = _mk_example("z", "SELECT 0", embedding=[0.0, 0.0, 0.0])
    ex_ok = _mk_example("ok", "SELECT 1", embedding=[1.0, 0.0, 0.0])
    pool = ExamplePool([ex_zero, ex_ok])

    retriever = ExampleRetriever(
        pool=pool,
        dialect="sqlite",
        embed_fn=_stub_embed({"query": [1.0, 0.0, 0.0]}),
        min_similarity=0.0,
        max_k=5,
    )
    results = retriever.retrieve("query")
    # zero-vec example should score 0 and come last (or be filtered by threshold)
    assert results[0][0].question == "ok"
