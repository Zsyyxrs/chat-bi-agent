"""MetricResolver 单测：catalog 加载、spec → SQL 模板拼装、错误路径。

LLM 提取部分（question → MetricSpec）由 stub 提供，不真调 Qwen。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from chat_bi_agent.agents.p1.metric_resolver import (
    MetricCatalog,
    MetricResolverError,
    MetricSpec,
    render_sql_from_spec,
    resolve,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
METRICS_YAML = REPO_ROOT / "config" / "metrics.yaml"


# ---------------------- catalog loading ----------------------


def test_catalog_loads_all_metrics_from_yaml():
    cat = MetricCatalog.from_yaml(METRICS_YAML)
    ids = {m.id for m in cat.metrics}
    # 至少覆盖 6 个种子指标
    assert {
        "deposit_balance",
        "loan_balance",
        "customer_aum",
        "customer_count",
        "product_count",
        "transaction_amount",
    }.issubset(ids)


def test_catalog_get_by_id():
    cat = MetricCatalog.from_yaml(METRICS_YAML)
    m = cat.get("deposit_balance")
    assert m.display_name.startswith("存款余额")
    assert "存款" in m.aliases


def test_catalog_get_unknown_raises():
    cat = MetricCatalog.from_yaml(METRICS_YAML)
    with pytest.raises(MetricResolverError):
        cat.get("no_such_metric")


# ---------------------- render_sql_from_spec ----------------------


def _get_cat() -> MetricCatalog:
    return MetricCatalog.from_yaml(METRICS_YAML)


def test_render_simplest_no_dims_no_filters():
    """product_count 无维度无过滤 → SELECT COUNT(*) FROM dim_product dp"""
    spec = MetricSpec(metric_id="product_count", dims=[], filters=[], time_window=None)
    sql = render_sql_from_spec(spec, _get_cat())
    assert "COUNT(*)" in sql
    assert "FROM dim_product dp" in sql
    # no GROUP BY
    assert "GROUP BY" not in sql
    # no unnecessary joins
    assert "JOIN" not in sql


def test_render_with_enum_filter():
    """product_count 加 product_category='WEALTH' → 出现在 WHERE"""
    spec = MetricSpec(
        metric_id="product_count",
        dims=[],
        filters=[{"col": "product_category", "op": "=", "val": "WEALTH"}],
        time_window=None,
    )
    sql = render_sql_from_spec(spec, _get_cat())
    assert "dp.product_category = 'WEALTH'" in sql


def test_render_rejects_bad_enum_value():
    """题面写"高净值"却传成 filters['customer_tier']='高净值' → 拒绝，防生成语义错 SQL"""
    spec = MetricSpec(
        metric_id="deposit_balance",
        dims=[],
        filters=[{"col": "customer_tier", "op": "=", "val": "高净值"}],
        time_window=None,
    )
    with pytest.raises(MetricResolverError, match="enum"):
        render_sql_from_spec(spec, _get_cat())


def test_render_auto_adds_required_join_for_dim():
    """deposit_balance 用 dim=branch_city → 自动加 JOIN dim_branch"""
    spec = MetricSpec(
        metric_id="deposit_balance",
        dims=["branch_city"],
        filters=[],
        time_window=None,
    )
    sql = render_sql_from_spec(spec, _get_cat())
    assert "JOIN dim_branch dbr" in sql
    assert "dbr.city" in sql
    assert "GROUP BY" in sql


def test_render_auto_adds_required_join_for_filter():
    """deposit_balance 按 branch_city='上海' 过滤 → 自动加 JOIN dim_branch"""
    spec = MetricSpec(
        metric_id="deposit_balance",
        dims=[],
        filters=[{"col": "branch_city", "op": "=", "val": "上海"}],
        time_window=None,
    )
    sql = render_sql_from_spec(spec, _get_cat())
    assert "JOIN dim_branch dbr" in sql
    assert "dbr.city = '上海'" in sql


def test_render_no_duplicate_join_when_dim_and_filter_both_need_same_join():
    """dim=customer_tier + filter=customer_tier → 只加一次 JOIN dim_customer"""
    spec = MetricSpec(
        metric_id="deposit_balance",
        dims=["customer_tier"],
        filters=[{"col": "customer_tier", "op": "=", "val": "HIGH_NET_WORTH"}],
        time_window=None,
    )
    sql = render_sql_from_spec(spec, _get_cat())
    assert sql.count("JOIN dim_customer dc") == 1


def test_render_time_window_uses_date_column():
    """deposit_balance date_column=fbd.dt → time_window 走 fbd.dt BETWEEN"""
    spec = MetricSpec(
        metric_id="deposit_balance",
        dims=[],
        filters=[],
        time_window={"start": "2026-05-01", "end": "2026-05-31"},
    )
    sql = render_sql_from_spec(spec, _get_cat())
    assert "fbd.dt >= DATE '2026-05-01'" in sql
    assert "fbd.dt <= DATE '2026-05-31'" in sql


def test_render_time_window_ignored_when_metric_has_no_date_column():
    """customer_aum 是快照，date_column=null → 就算给了 time_window 也不加"""
    spec = MetricSpec(
        metric_id="customer_aum",
        dims=[],
        filters=[],
        time_window={"start": "2026-05-01", "end": "2026-05-31"},
    )
    sql = render_sql_from_spec(spec, _get_cat())
    assert "BETWEEN" not in sql
    assert "dt" not in sql


def test_render_preserves_hard_filters():
    """deposit_balance 的 account_type IN ('CURRENT','SAVING') 必须在 WHERE 里"""
    spec = MetricSpec(metric_id="deposit_balance", dims=[], filters=[], time_window=None)
    sql = render_sql_from_spec(spec, _get_cat())
    assert "account_type IN ('CURRENT','SAVING')" in sql


def test_render_unknown_dim_raises():
    spec = MetricSpec(
        metric_id="deposit_balance",
        dims=["not_a_dim"],
        filters=[],
        time_window=None,
    )
    with pytest.raises(MetricResolverError, match="unknown dim"):
        render_sql_from_spec(spec, _get_cat())


def test_render_unknown_filter_raises():
    spec = MetricSpec(
        metric_id="deposit_balance",
        dims=[],
        filters=[{"col": "not_a_field", "op": "=", "val": "x"}],
        time_window=None,
    )
    with pytest.raises(MetricResolverError, match="unknown filter"):
        render_sql_from_spec(spec, _get_cat())


# ---------------------- resolve() — end-to-end with mocked LLM ----------------------


def _mock_llm(spec_json: str):
    """给 qwen_client.chat 装一个假 client，返回固定 JSON。"""

    class _R:
        def __init__(self, c):
            self.content = c

    return _R(f"```json\n{spec_json}\n```")


def test_resolve_happy_path():
    """LLM 返回合法 spec → SQL 从模板拼出来。"""
    spec_json = (
        '{"metric_id":"customer_count","dims":["branch_city"],'
        '"filters":[{"col":"customer_tier","op":"=","val":"MASS"}],"time_window":null}'
    )
    with patch(
        "chat_bi_agent.agents.p1.metric_resolver.qwen_client.chat",
        return_value=_mock_llm(spec_json),
    ):
        sql = resolve(question="杭州分行的大众客户数量", catalog=_get_cat())
    assert "COUNT(DISTINCT dc.customer_id)" in sql
    assert "GROUP BY dbr.city" in sql or "GROUP BY city" in sql or "dbr.city" in sql
    assert "dc.customer_tier = 'MASS'" in sql


def test_resolve_null_metric_id_raises():
    """LLM 觉得题目不匹配任何 metric → metric_id=null → resolve 抛 NoMetricMatch。"""
    with patch(
        "chat_bi_agent.agents.p1.metric_resolver.qwen_client.chat",
        return_value=_mock_llm('{"metric_id":null,"dims":[],"filters":[],"time_window":null}'),
    ):
        with pytest.raises(MetricResolverError, match="no metric matched"):
            resolve(question="随便一个不像 metric 的问题", catalog=_get_cat())


def test_resolve_invalid_llm_json_raises():
    with patch(
        "chat_bi_agent.agents.p1.metric_resolver.qwen_client.chat",
        return_value=_mock_llm("not json"),
    ):
        with pytest.raises(MetricResolverError):
            resolve(question="q", catalog=_get_cat())


# ---------------------- IN operator tests ----------------------


def _mini_catalog(tmp_path):
    yml = tmp_path / "metrics.yaml"
    yml.write_text(
        """
version: 1
metrics:
  - id: customer_count
    display_name: 客户数
    aliases: [客户数, 客户数量]
    fact_table: dim_customer
    fact_alias: dc
    metric_expr: COUNT(*)
    metric_alias: cnt
    hard_filters: []
    joins:
      branch: JOIN dim_branch dbr ON dc.branch_id = dbr.branch_id
    dim_catalog: {}
    filter_catalog:
      customer_tier:
        column: dc.customer_tier
        type: enum
        enum_values: [HIGH_NET_WORTH, AFFLUENT, MASS, BASIC]
      branch_id:
        column: dc.branch_id
        type: string
      age:
        column: dc.age
        type: numeric
""",
        encoding="utf-8",
    )
    return MetricCatalog.from_yaml(yml)


def test_in_op_enum_multi_values(tmp_path):
    catalog = _mini_catalog(tmp_path)
    spec = MetricSpec(
        metric_id="customer_count",
        filters=[{"col": "customer_tier", "op": "IN", "val": ["HIGH_NET_WORTH", "AFFLUENT"]}],
    )
    sql = render_sql_from_spec(spec, catalog)
    assert "dc.customer_tier IN ('HIGH_NET_WORTH', 'AFFLUENT')" in sql


def test_in_op_enum_single_value(tmp_path):
    catalog = _mini_catalog(tmp_path)
    spec = MetricSpec(
        metric_id="customer_count",
        filters=[{"col": "customer_tier", "op": "IN", "val": ["MASS"]}],
    )
    sql = render_sql_from_spec(spec, catalog)
    assert "dc.customer_tier IN ('MASS')" in sql


def test_in_op_enum_bad_value_raises(tmp_path):
    catalog = _mini_catalog(tmp_path)
    spec = MetricSpec(
        metric_id="customer_count",
        filters=[{"col": "customer_tier", "op": "IN", "val": ["HIGH_NET_WORTH", "无此层级"]}],
    )
    with pytest.raises(MetricResolverError, match="bad enum value"):
        render_sql_from_spec(spec, catalog)


def test_in_op_string_type_quotes_and_escapes(tmp_path):
    catalog = _mini_catalog(tmp_path)
    spec = MetricSpec(
        metric_id="customer_count",
        filters=[{"col": "branch_id", "op": "IN", "val": ["BR_CITY_0000", "BR_CITY_0002"]}],
    )
    sql = render_sql_from_spec(spec, catalog)
    assert "dc.branch_id IN ('BR_CITY_0000', 'BR_CITY_0002')" in sql


def test_in_op_numeric_type_no_quotes(tmp_path):
    catalog = _mini_catalog(tmp_path)
    spec = MetricSpec(
        metric_id="customer_count",
        filters=[{"col": "age", "op": "IN", "val": [60, 65, 70]}],
    )
    sql = render_sql_from_spec(spec, catalog)
    assert "dc.age IN (60, 65, 70)" in sql


def test_in_op_empty_list_raises_unsupported(tmp_path):
    catalog = _mini_catalog(tmp_path)
    spec = MetricSpec(
        metric_id="customer_count",
        filters=[{"col": "customer_tier", "op": "IN", "val": []}],
    )
    with pytest.raises(MetricResolverError, match="empty IN"):
        render_sql_from_spec(spec, catalog)


def test_in_op_non_list_val_raises(tmp_path):
    catalog = _mini_catalog(tmp_path)
    spec = MetricSpec(
        metric_id="customer_count",
        filters=[{"col": "branch_id", "op": "IN", "val": "BR_CITY_0000"}],
    )
    with pytest.raises(MetricResolverError, match="IN.*list"):
        render_sql_from_spec(spec, catalog)


# ---------------------- hard_filter_joins ----------------------


def test_hard_filter_joins_always_rendered():
    """hard_filters 引用的 join 无条件拼进 FROM，即使 spec 不带 dims/filters。"""
    cat = _get_cat()
    spec = MetricSpec(metric_id="deposit_balance", dims=[], filters=[], time_window=None)
    sql = render_sql_from_spec(spec, cat)
    assert "JOIN dim_account da" in sql
    assert "da.account_type IN ('CURRENT','SAVING')" in sql


def test_hard_filter_joins_not_duplicated_when_also_required_by_dim():
    """hard_filter_joins 与 dim 的 requires_join 撞车时只拼一次。"""
    cat = _get_cat()
    m = cat.get("deposit_balance")
    m.dim_catalog["_probe"] = type(m.dim_catalog["branch_id"])(
        id="_probe",
        select_expr="da.account_type",
        alias="acct_type",
        requires_join=["account"],
    )
    spec = MetricSpec(metric_id="deposit_balance", dims=["_probe"], filters=[], time_window=None)
    sql = render_sql_from_spec(spec, cat)
    assert sql.count("JOIN dim_account da") == 1


def test_catalog_columns_match_real_schema_names():
    """transaction_amount 的 channel 列名必须是 transaction_channel。"""
    cat = _get_cat()
    m = cat.get("transaction_amount")
    assert m.dim_catalog["channel"].select_expr == "ft.transaction_channel"
    assert m.filter_catalog["channel"].column == "ft.transaction_channel"


def test_is_active_filter_typed_boolean_not_string():
    """dc.is_active 是 boolean 列，不能声明成 string（会拼出 = 'True'）。"""
    cat = _get_cat()
    m = cat.get("customer_count")
    assert m.filter_catalog["is_active"].type == "boolean"
    spec = MetricSpec(
        metric_id="customer_count",
        dims=[],
        filters=[{"col": "is_active", "op": "=", "val": True}],
        time_window=None,
    )
    sql = render_sql_from_spec(spec, cat)
    assert "dc.is_active = TRUE" in sql


# ---------------------- extractor prompt 与渲染能力对齐 ----------------------


def test_extractor_prompt_advertises_in_operator():
    """prompt 必须告诉 LLM 可以用 op='IN'。

    渲染层 2026-08-12 就支持 IN 了，但 prompt 一直写着"op 目前只支持 '='"，
    LLM 遇到"杭州和南京两个分行"这类多值约束会直接把约束丢掉（改成 group by
    全部城市），拼出的 SQL 合法、validator/executor 全过，但答的是另一个问题。
    这种语义欠约束是 guardrail 抓不到的，只能靠 prompt 与渲染能力对齐来防。
    """
    from chat_bi_agent.agents.p1.metric_resolver import _build_extractor_prompt

    prompt = _build_extractor_prompt(_get_cat())
    assert "'op 目前只支持" not in prompt
    assert "IN" in prompt
    # 必须给出 IN 的形状，否则 LLM 不知道 val 要传 list
    assert "'IN'" in prompt or '"IN"' in prompt


def test_extractor_prompt_warns_against_dropping_constraints():
    """prompt 要明确禁止「约束表达不了就丢掉」——这是 q006 回归的直接成因。"""
    from chat_bi_agent.agents.p1.metric_resolver import _build_extractor_prompt

    prompt = _build_extractor_prompt(_get_cat())
    assert "metric_id=null" in prompt
    assert "不要" in prompt or "禁止" in prompt


def test_extractor_prompt_requires_value_domain_match():
    """prompt 要求 val 与 col 的值域匹配。

    string 类型 filter 没有 enum_values 可校验，LLM 把 branch_id 的值
    ('BR_CITY_0000') 塞进 branch_city（dbr.city，值是「杭州」）时，
    SQL 合法、执行成功、静默返回 0 行——比报错更难发现。
    """
    from chat_bi_agent.agents.p1.metric_resolver import _build_extractor_prompt

    prompt = _build_extractor_prompt(_get_cat())
    assert "值域" in prompt


# ---------------------- 冗余别名 ----------------------


def test_dim_alias_omitted_when_same_as_bare_column():
    """别名与裸列名相同时不输出 AS——冗余，且会干扰按列名比对的评分器。"""
    cat = _get_cat()
    spec = MetricSpec(
        metric_id="customer_count",
        dims=["branch_id"],
        filters=[],
        time_window=None,
    )
    sql = render_sql_from_spec(spec, cat)
    assert "dc.branch_id AS branch_id" not in sql
    assert "SELECT dc.branch_id," in sql


def test_dim_alias_kept_when_it_renames_the_column():
    """别名与列名不同时必须保留 AS（branch_city 的列是 dbr.city，别名 city）。"""
    cat = _get_cat()
    m = cat.get("customer_count")
    assert m.dim_catalog["branch_city"].select_expr == "dbr.city"
    assert m.dim_catalog["branch_city"].alias == "city"
    # 别名 city == 裸列名 city → 应省略
    spec = MetricSpec(metric_id="customer_count", dims=["branch_city"], time_window=None)
    assert "dbr.city AS city" not in render_sql_from_spec(spec, cat)

    # 构造一个真正重命名的 dim，AS 必须留着
    probe = type(m.dim_catalog["branch_id"])(
        id="_probe", select_expr="dc.branch_id", alias="网点编号"
    )
    m.dim_catalog["_probe"] = probe
    spec2 = MetricSpec(metric_id="customer_count", dims=["_probe"], time_window=None)
    assert "dc.branch_id AS 网点编号" in render_sql_from_spec(spec2, cat)


def test_metric_expr_always_keeps_its_alias():
    """聚合表达式的别名永远保留——COUNT(...) 没有裸列名可省。"""
    cat = _get_cat()
    spec = MetricSpec(metric_id="customer_count", dims=[], time_window=None)
    sql = render_sql_from_spec(spec, cat)
    assert "AS customer_count" in sql


# ---------------------- string filter 值域探针 ----------------------


def test_domain_probe_sql_covers_only_string_filters():
    """探针只针对 string 类型 filter——enum 有 enum_values 兜底，数值/时间窗合法为空。"""
    from chat_bi_agent.agents.p1.metric_resolver import render_domain_probe_sql

    cat = _get_cat()
    spec = MetricSpec(
        metric_id="customer_count",
        dims=[],
        filters=[
            {"col": "branch_id", "op": "IN", "val": ["BR_CITY_0000"]},
            {"col": "customer_tier", "op": "=", "val": "MASS"},  # enum，不该进探针
        ],
        time_window=None,
    )
    sql = render_domain_probe_sql(spec, cat)
    assert sql is not None
    assert "dc.branch_id IN ('BR_CITY_0000')" in sql
    assert "customer_tier" not in sql
    assert "LIMIT 1" in sql


def test_domain_probe_sql_none_when_no_string_filter():
    from chat_bi_agent.agents.p1.metric_resolver import render_domain_probe_sql

    cat = _get_cat()
    spec = MetricSpec(metric_id="customer_count", dims=[], filters=[], time_window=None)
    assert render_domain_probe_sql(spec, cat) is None


def test_domain_probe_sql_includes_required_joins():
    """探针用到 dbr.city 就必须带上 branch join，否则别名不存在。"""
    from chat_bi_agent.agents.p1.metric_resolver import render_domain_probe_sql

    cat = _get_cat()
    spec = MetricSpec(
        metric_id="customer_count",
        dims=[],
        filters=[{"col": "branch_city", "op": "=", "val": "杭州"}],
        time_window=None,
    )
    sql = render_domain_probe_sql(spec, cat)
    assert "JOIN dim_branch dbr" in sql


def test_router_probe_rejects_out_of_domain_value(tmp_path):
    """探针查不到行 → value_out_of_domain，回退 NL2SQL 而不是静默返回空结果。"""
    from unittest.mock import MagicMock, patch

    from chat_bi_agent.agents.p1 import metric_resolver as mr
    from chat_bi_agent.agents.p1.metric_resolver import MetricRouter

    cat = _get_cat()
    router = MetricRouter(
        cat,
        embed_fn=lambda texts: [[1.0, 0.0] for _ in texts],
        threshold=0.0,
        probe_fn=lambda sql: ([], None),  # 探针返回空 = 值域外
    )
    spec = MetricSpec(
        metric_id="customer_count",
        dims=[],
        filters=[{"col": "branch_id", "op": "=", "val": "不存在的分行"}],
        time_window=None,
    )
    with patch.object(mr, "_resolve_to_spec_and_sql", MagicMock(return_value=(spec, "SELECT 1"))):
        rr = router.try_route("随便问点什么")
    assert rr.fail_reason == "value_out_of_domain"
    assert rr.sql is None
    assert rr.spec is not None  # 保留 spec 供审计


def test_router_probe_passes_when_value_exists(tmp_path):
    from unittest.mock import MagicMock, patch

    from chat_bi_agent.agents.p1 import metric_resolver as mr
    from chat_bi_agent.agents.p1.metric_resolver import MetricRouter

    cat = _get_cat()
    router = MetricRouter(
        cat,
        embed_fn=lambda texts: [[1.0, 0.0] for _ in texts],
        threshold=0.0,
        probe_fn=lambda sql: ([{"probe": 1}], None),
    )
    spec = MetricSpec(
        metric_id="customer_count",
        dims=[],
        filters=[{"col": "branch_id", "op": "=", "val": "BR_CITY_0000"}],
        time_window=None,
    )
    with patch.object(mr, "_resolve_to_spec_and_sql", MagicMock(return_value=(spec, "SELECT 1"))):
        rr = router.try_route("随便问点什么")
    assert rr.fail_reason is None
    assert rr.sql == "SELECT 1"


def test_router_without_probe_fn_skips_domain_check(tmp_path):
    """不注入 probe_fn 时行为与之前完全一致（向后兼容）。"""
    from unittest.mock import MagicMock, patch

    from chat_bi_agent.agents.p1 import metric_resolver as mr
    from chat_bi_agent.agents.p1.metric_resolver import MetricRouter

    cat = _get_cat()
    router = MetricRouter(cat, embed_fn=lambda texts: [[1.0, 0.0] for _ in texts], threshold=0.0)
    spec = MetricSpec(
        metric_id="customer_count",
        dims=[],
        filters=[{"col": "branch_id", "op": "=", "val": "无所谓"}],
        time_window=None,
    )
    with patch.object(mr, "_resolve_to_spec_and_sql", MagicMock(return_value=(spec, "SELECT 1"))):
        rr = router.try_route("随便问点什么")
    assert rr.fail_reason is None
    assert rr.sql == "SELECT 1"


def test_extractor_prompt_rejects_ordering_and_top_n():
    """spec 结构里没有 ORDER BY / LIMIT，排序取顶类问题必须拒绝而非硬凑。

    A/B 实测：问"存款余额最高的前 5 个分行"，LLM 映射到 deposit_balance 却丢掉
    Top-5，返回全部分行。SQL 合法、值域也对，只是答的是另一个问题——和当初丢掉
    IN 约束同一类失败。
    """
    from chat_bi_agent.agents.p1.metric_resolver import _build_extractor_prompt

    prompt = _build_extractor_prompt(_get_cat())
    assert "排序" in prompt
    assert "最高" in prompt or "前 N" in prompt


# ---------------------- 全局 joins 复用 ----------------------


def _global_joins_catalog(tmp_path, extra_metric_yaml: str = "") -> MetricCatalog:
    """带顶层 joins 注册表的 catalog：join 子句用 {fact} 占位 fact_alias。"""
    yml = tmp_path / "gj.yaml"
    yml.write_text(
        """
version: 1

joins:
  branch: "JOIN dim_branch dbr ON {fact}.branch_id = dbr.branch_id"
  customer: "JOIN dim_customer dc ON {fact}.customer_id = dc.customer_id"

metrics:
  - id: deposit_balance
    display_name: 存款余额
    aliases: [存款余额]
    fact_table: fct_balance_daily
    fact_alias: fbd
    metric_expr: AVG(fbd.balance)
    metric_alias: avg_bal
    hard_filters: []
    date_column: fbd.dt
    dim_catalog:
      branch_city: {select_expr: "dbr.city", alias: "city", requires_join: [branch]}
    filter_catalog: {}
"""
        + extra_metric_yaml,
        encoding="utf-8",
    )
    return MetricCatalog.from_yaml(yml)


def test_global_join_substitutes_metric_fact_alias(tmp_path):
    """顶层 joins 里的 {fact} 要换成该 metric 自己的 fact_alias。"""
    cat = _global_joins_catalog(tmp_path)
    sql = render_sql_from_spec(MetricSpec(metric_id="deposit_balance", dims=["branch_city"]), cat)
    assert "JOIN dim_branch dbr ON fbd.branch_id = dbr.branch_id" in sql
    assert "{fact}" not in sql


def test_same_global_join_reused_across_different_fact_aliases(tmp_path):
    """同一条全局 join 被两个 fact_alias 不同的 metric 复用，各自替换成自己的别名。"""
    cat = _global_joins_catalog(
        tmp_path,
        """
  - id: transaction_amount
    display_name: 交易金额
    aliases: [交易金额]
    fact_table: fct_transaction
    fact_alias: ft
    metric_expr: SUM(ft.amount)
    metric_alias: total_amt
    hard_filters: []
    dim_catalog:
      branch_city: {select_expr: "dbr.city", alias: "city", requires_join: [branch]}
    filter_catalog: {}
""",
    )
    sql = render_sql_from_spec(
        MetricSpec(metric_id="transaction_amount", dims=["branch_city"]), cat
    )
    assert "JOIN dim_branch dbr ON ft.branch_id = dbr.branch_id" in sql


def test_metric_local_join_overrides_global_one(tmp_path):
    """metric 自己写的 joins 是逃生舱，同名时压过全局注册表。"""
    cat = _global_joins_catalog(
        tmp_path,
        """
  - id: odd_metric
    display_name: 不规则指标
    aliases: [不规则]
    fact_table: fct_odd
    fact_alias: fo
    metric_expr: COUNT(*)
    metric_alias: cnt
    hard_filters: []
    joins:
      branch: "JOIN dim_branch dbr ON fo.legacy_branch_code = dbr.branch_id"
    dim_catalog:
      branch_city: {select_expr: "dbr.city", alias: "city", requires_join: [branch]}
    filter_catalog: {}
""",
    )
    sql = render_sql_from_spec(MetricSpec(metric_id="odd_metric", dims=["branch_city"]), cat)
    assert "JOIN dim_branch dbr ON fo.legacy_branch_code = dbr.branch_id" in sql
    assert "fo.branch_id" not in sql


def test_global_join_skipped_when_its_alias_collides_with_fact_alias(tmp_path):
    """fact 表本身就是 dim_customer（别名 dc）时，全局 customer join 会自连自己——必须不生效。"""
    cat = _global_joins_catalog(
        tmp_path,
        """
  - id: customer_aum
    display_name: 客户 AUM
    aliases: [AUM]
    fact_table: dim_customer
    fact_alias: dc
    metric_expr: SUM(dc.aum)
    metric_alias: total_aum
    hard_filters: []
    dim_catalog:
      branch_city: {select_expr: "dbr.city", alias: "city", requires_join: [branch]}
    filter_catalog: {}
""",
    )
    metric = cat.get("customer_aum")
    assert "customer" not in metric.joins
    # branch 不冲突，照常可用
    assert "JOIN dim_branch dbr ON dc.branch_id = dbr.branch_id" == metric.joins["branch"]


def test_catalog_exposes_global_joins_registry(tmp_path):
    cat = _global_joins_catalog(tmp_path)
    assert cat.joins["branch"] == "JOIN dim_branch dbr ON {fact}.branch_id = dbr.branch_id"


def test_production_yaml_uses_global_joins_registry():
    """生产 YAML 迁移守门：join 子句集中定义，不再每个 metric 复制一份。"""
    cat = MetricCatalog.from_yaml(METRICS_YAML)
    assert set(cat.joins) >= {"account", "branch", "customer", "product"}
    # 迁移后仍要能拼出正确的 join
    sql = render_sql_from_spec(MetricSpec(metric_id="deposit_balance", dims=["branch_city"]), cat)
    assert "JOIN dim_branch dbr ON fbd.branch_id = dbr.branch_id" in sql
    assert "JOIN dim_account da ON fbd.account_id = da.account_id" in sql
