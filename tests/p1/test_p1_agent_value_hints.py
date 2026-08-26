"""P1 把 ValueIndex 检索到的库内取值注入 schema DDL。

只 mock LLM/DB 边界，SchemaLoader 与 ValueIndex 都用真实实现——要验的正是
「真实取值有没有走到喂给 SQLGenerator 的那段 DDL 里」。
"""

from unittest.mock import patch

import pytest

from chat_bi_agent.agents.p1.nl2sql_agent import P1NL2SQLAgent
from chat_bi_agent.agents.p1.sql_generator import SQLGenResult
from chat_bi_agent.agents.p1.sql_validator import ValidationResult
from chat_bi_agent.schema.value_index import ValueIndex


class _StubMatch:
    def __init__(self, name):
        self.name = name


@pytest.fixture
def branch_yaml(tmp_path):
    f = tmp_path / "schema.yaml"
    f.write_text(
        """
tables:
  - name: dim_branch
    type: dimension
    domain: 机构
    description: 机构维度表
    primary_key: branch_id
    columns:
      - {name: branch_id, type: VARCHAR(16), description: "机构编号"}
      - {name: province, type: VARCHAR(32), description: "所在省份"}
      - {name: city, type: VARCHAR(32), description: "所在城市"}
    embed_text: "机构维度表"
""",
        encoding="utf-8",
    )
    return f


def _agent(branch_yaml, value_index):
    with (
        patch("chat_bi_agent.agents.p1.nl2sql_agent.SchemaLinker") as msl,
        patch("chat_bi_agent.agents.p1.nl2sql_agent.SQLGenerator") as msg,
        patch("chat_bi_agent.agents.p1.nl2sql_agent.SQLValidator") as msv,
        patch("chat_bi_agent.agents.p1.nl2sql_agent.SQLExecutor") as mse,
        patch("chat_bi_agent.agents.p1.nl2sql_agent.Reflector"),
        patch("chat_bi_agent.schema.loader.qwen_client.embed", return_value=[[0.1, 0.2]]),
    ):
        agent = P1NL2SQLAgent(top_k=1, schema_yaml_path=branch_yaml, value_index=value_index)
        agent.schema_linker = msl.return_value
        agent.schema_linker.link.return_value = [_StubMatch("dim_branch")]
        agent.sql_generator = msg.return_value
        agent.sql_generator.generate.return_value = SQLGenResult(
            sql="SELECT 1", thought="t", tables_used=["dim_branch"], raw_response="{}"
        )
        agent.sql_validator = msv.return_value
        agent.sql_validator.validate.return_value = ValidationResult(ok=True, error=None)
        agent.sql_executor = mse.return_value
        agent.sql_executor.execute.return_value = ([{"x": 1}], None)
        return agent


def test_matched_value_reaches_the_ddl_given_to_the_generator(branch_yaml):
    """问题里的「上海」在库里是 province 的取值，这一事实必须出现在 DDL 里。"""
    agent = _agent(branch_yaml, ValueIndex({"dim_branch.province": ["上海"]}))

    agent.run("q1", "上海的分行有多少家")

    schema_ddl = agent.sql_generator.generate.call_args.kwargs["schema_ddl"]
    assert "province VARCHAR(32)  -- 所在省份  -- examples: 上海" in schema_ddl


def test_empty_value_index_leaves_ddl_untouched(branch_yaml):
    """空索引 = 关闭值检索：DDL 与改造前逐字一致。

    A/B 对照的基线臂靠这个开关，所以「关掉」必须是可显式表达的状态，
    而不是只能靠删缓存文件。
    """
    agent = _agent(branch_yaml, ValueIndex({}))

    agent.run("q1", "上海的分行有多少家")

    assert "-- examples" not in agent.sql_generator.generate.call_args.kwargs["schema_ddl"]


def test_defaults_to_the_on_disk_snapshot_when_index_not_supplied(branch_yaml):
    """不传 value_index 时读默认快照——生产调用点无需改代码即可获得该能力。"""
    agent = _agent(branch_yaml, None)

    assert agent.value_index is not None
    assert agent.value_index.lookup("上海分行的存款")
