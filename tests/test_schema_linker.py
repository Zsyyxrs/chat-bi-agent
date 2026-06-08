"""SchemaLinker 用确定性向量测排序逻辑。"""

from unittest.mock import patch

import pytest

from chat_bi_agent.schema.loader import SchemaLoader
from chat_bi_agent.agents.shared.schema_linker import SchemaLinker, TableMatch


@pytest.fixture
def loader_with_fake_index(tmp_path):
    yaml_text = """
tables:
  - {name: ta, type: dimension, domain: x, description: A, primary_key: id, columns: [{name: id, type: INT, description: pk}], embed_text: "table a"}
  - {name: tb, type: dimension, domain: x, description: B, primary_key: id, columns: [{name: id, type: INT, description: pk}], embed_text: "table b"}
  - {name: tc, type: dimension, domain: x, description: C, primary_key: id, columns: [{name: id, type: INT, description: pk}], embed_text: "table c"}
  - {name: td, type: dimension, domain: x, description: D, primary_key: id, columns: [{name: id, type: INT, description: pk}], embed_text: "table d"}
  - {name: te, type: dimension, domain: x, description: E, primary_key: id, columns: [{name: id, type: INT, description: pk}], embed_text: "table e"}
"""
    f = tmp_path / "s.yaml"
    f.write_text(yaml_text, encoding="utf-8")

    loader = SchemaLoader(yaml_path=f)
    loader.load()
    # 手工注入 embedding，让 cosine 排序结果可预测：
    # query embedding 与 ta 完全同向 = 1.0；tb 0.9 ；tc 0.5 ；td -0.1 ；te 0.0
    loader.docs[0].embedding = [1.0, 0.0]
    loader.docs[1].embedding = [0.9, 0.436]   # cos with [1,0] ≈ 0.9
    loader.docs[2].embedding = [0.5, 0.866]   # cos ≈ 0.5
    loader.docs[3].embedding = [-0.1, 0.995]  # cos ≈ -0.1
    loader.docs[4].embedding = [0.0, 1.0]     # cos = 0.0
    return loader


def test_link_returns_top_k_in_score_order(loader_with_fake_index):
    linker = SchemaLinker(loader=loader_with_fake_index, top_k=4)
    with patch("chat_bi_agent.agents.shared.schema_linker.qwen_client.embed") as mock_embed:
        mock_embed.return_value = [[1.0, 0.0]]
        matches = linker.link("test question")

    assert isinstance(matches, list)
    assert len(matches) == 4  # K=4
    assert isinstance(matches[0], TableMatch)
    assert [m.name for m in matches] == ["ta", "tb", "tc", "te"]  # td 被截掉
    # score 递减
    assert matches[0].score > matches[1].score > matches[2].score > matches[3].score


def test_link_returns_fewer_if_total_smaller_than_k(loader_with_fake_index):
    linker = SchemaLinker(loader=loader_with_fake_index, top_k=10)
    with patch("chat_bi_agent.agents.shared.schema_linker.qwen_client.embed") as mock_embed:
        mock_embed.return_value = [[1.0, 0.0]]
        matches = linker.link("test question")
    assert len(matches) == 5  # 只有 5 张表
