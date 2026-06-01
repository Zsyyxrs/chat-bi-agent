"""Test SchemaLoader without invoking embedding (mock embed)."""

from unittest.mock import patch

import pytest

from chat_bi_agent.schema.loader import SchemaLoader, TableDoc


@pytest.fixture
def fake_yaml(tmp_path):
    yaml_text = """
tables:
  - name: t1
    type: dimension
    domain: 测试
    description: 测试表 1
    primary_key: id
    columns:
      - {name: id, type: INT, description: "主键"}
      - {name: name, type: VARCHAR, description: "名称"}
    embed_text: "测试表 1 关键列 id name"
  - name: t2
    type: fact
    domain: 测试
    description: 测试表 2
    primary_key: id
    columns:
      - {name: id, type: INT, description: "主键"}
    embed_text: "测试表 2"
"""
    f = tmp_path / "schema.yaml"
    f.write_text(yaml_text, encoding="utf-8")
    return f


def test_load_yaml_returns_table_docs(fake_yaml):
    loader = SchemaLoader(yaml_path=fake_yaml)
    loader.load()
    assert len(loader.docs) == 2
    assert isinstance(loader.docs[0], TableDoc)
    assert loader.docs[0].name == "t1"
    assert loader.docs[0].embed_text == "测试表 1 关键列 id name"
    assert "id" in [c["name"] for c in loader.docs[0].columns]


def test_build_index_calls_embed_once_with_all_embed_texts(fake_yaml):
    loader = SchemaLoader(yaml_path=fake_yaml)
    loader.load()
    with patch("chat_bi_agent.schema.loader.qwen_client.embed") as mock_embed:
        mock_embed.return_value = [[0.1] * 1024, [0.2] * 1024]
        loader.build_index()
        # 一次批量调用，包含两个 embed_text
        assert mock_embed.call_count == 1
        called_texts = mock_embed.call_args[0][0]
        assert called_texts == ["测试表 1 关键列 id name", "测试表 2"]
    assert loader.docs[0].embedding == [0.1] * 1024
    assert loader.docs[1].embedding == [0.2] * 1024


def test_get_ddl_text_formats_columns(fake_yaml):
    loader = SchemaLoader(yaml_path=fake_yaml)
    loader.load()
    ddl = loader.get_ddl_text("t1")
    assert "CREATE TABLE t1" in ddl
    assert "id INT" in ddl
    assert "name VARCHAR" in ddl
    assert "主键" in ddl  # 列 description 作为 SQL 注释保留
