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


def test_get_ddl_text_appends_matched_values_as_examples(fake_yaml):
    """命中的库内取值以 CHESS 风格的 `-- examples:` 挂在对应列后面。"""
    from chat_bi_agent.schema.value_index import ValueHit

    loader = SchemaLoader(yaml_path=fake_yaml)
    loader.load()

    ddl = loader.get_ddl_text("t1", value_hits=[ValueHit("t1", "name", "上海")])

    assert "name VARCHAR  -- 名称  -- examples: 上海" in ddl
    assert "-- examples" not in ddl.split("name VARCHAR")[0]  # 不该污染其他列


def test_get_ddl_text_ignores_hits_belonging_to_other_tables(fake_yaml):
    loader = SchemaLoader(yaml_path=fake_yaml)
    loader.load()
    from chat_bi_agent.schema.value_index import ValueHit

    ddl = loader.get_ddl_text("t1", value_hits=[ValueHit("t2", "id", "999")])

    assert "-- examples" not in ddl


def test_customer_name_ddl_warns_that_names_are_not_unique():
    """姓名不唯一这件事必须出现在**真正进 prompt 的那段文本**里。

    2026-09-08 实测（P1 题集新增 q013「每个城市分行交易额前 3 的客户」）：模型写了
    `GROUP BY b.branch_name, c.customer_name`——按姓名而非客户号聚合。库里 5230 个客户
    只有 3801 个不同姓名，2026-06 单月就有 11 组「同分行同名且都有交易」，按姓名聚合
    会把不同客户的交易额并成一笔。

    那次没被判错纯属运气：11 组冲突没有一组落进任何分行的 top 3（2026-01~09 全月份
    全分行实测 0 例），于是行数、result_match、六个评分维度全部为它背书，拿了 0.837。

    断言 get_ddl_text 而不是断言 yaml 原文：注释写在没人加载的地方等于没写，
    只有进了 DDL 文本才真的送到模型眼前。
    """
    from chat_bi_agent.schema.loader import SchemaLoader

    loader = SchemaLoader()
    loader.load()
    ddl = loader.get_ddl_text("dim_customer")

    name_line = next(ln for ln in ddl.splitlines() if ln.strip().startswith("customer_name "))
    assert "不唯一" in name_line
    assert "customer_id" in name_line, "得直接给出正确做法，而不只是说姓名有重复"
