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


# ---------------------- ORDER BY / LIMIT ----------------------


def test_order_by_metric_desc_with_limit():
    """「按金额倒序取前 5」——2026-08-28 分流里 B 档最常见的一类拒绝原因。

    spec 原本没有排序字段，遇到这类问题只能整条退回 NL2SQL。排序目标限定在
    「本次查询已经选出来的东西」（metric 本身或 spec.dims 里的维度），
    LLM 递不进任意表达式。
    """
    cat = _get_cat()
    spec = MetricSpec(
        metric_id="transaction_amount",
        dims=["channel"],
        order_by={"by": "metric", "desc": True},
        limit=5,
    )
    sql = render_sql_from_spec(spec, cat)
    assert "ORDER BY total_amount DESC" in sql
    assert sql.rstrip().endswith("LIMIT 5")
    # 排序必须排在 GROUP BY 之后
    assert sql.index("GROUP BY") < sql.index("ORDER BY")


def test_order_by_dimension_ascending():
    cat = _get_cat()
    spec = MetricSpec(
        metric_id="transaction_amount",
        dims=["channel"],
        order_by={"by": "channel", "desc": False},
    )
    sql = render_sql_from_spec(spec, cat)
    assert "ORDER BY ft.transaction_channel ASC" in sql
    assert "LIMIT" not in sql


def test_order_by_unselected_dimension_is_rejected():
    """按一个没出现在 SELECT 里的维度排序——聚合查询下语义不成立，直接拒。"""
    cat = _get_cat()
    spec = MetricSpec(
        metric_id="transaction_amount",
        dims=["channel"],
        order_by={"by": "branch_id", "desc": True},
    )
    with pytest.raises(MetricResolverError, match="order_by"):
        render_sql_from_spec(spec, cat)


def test_limit_must_be_a_positive_integer():
    """LIMIT 直接进 SQL 文本，必须挡住非整数——这是唯一一处数字拼进模板的地方。"""
    cat = _get_cat()
    for bad in (0, -1, "5; DROP TABLE x", 2.5):
        spec = MetricSpec(metric_id="transaction_amount", limit=bad)
        with pytest.raises(MetricResolverError, match="limit"):
            render_sql_from_spec(spec, cat)


def test_no_order_by_renders_unchanged():
    """不带排序时 SQL 与从前逐字一致——这条特性不该影响既有查询。"""
    cat = _get_cat()
    spec = MetricSpec(metric_id="transaction_amount", dims=["channel"])
    sql = render_sql_from_spec(spec, cat)
    assert "ORDER BY" not in sql and "LIMIT" not in sql


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


def test_extractor_prompt_teaches_ordering_and_top_n():
    """spec 2026-08-28 起有了 order_by / limit，prompt 必须教它怎么用。

    这条测试的前身断言的是相反的事——「一律返回 metric_id=null」。当时 spec
    确实编码不了排序：A/B 实测问"存款余额最高的前 5 个分行"，LLM 丢掉 Top-5
    返回全部分行，只能整条拒掉。分流显示这类问题在生产池里占 B 档近半，
    于是给渲染层补了 ORDER BY / LIMIT，拒绝规则随之作废。

    仍然要拒的是「按没放进 dims 的列排序」——聚合查询下语义不成立。
    """
    from chat_bi_agent.agents.p1.metric_resolver import _build_extractor_prompt

    prompt = _build_extractor_prompt(_get_cat())
    assert "order_by" in prompt and "limit" in prompt
    # 光说有这个字段不够，得给出形状，否则 LLM 猜不到 by 的取值
    assert "'metric'" in prompt or '"metric"' in prompt
    # 越界排序仍须拒绝
    assert "没放进 dims" in prompt


def test_extractor_prompt_rejects_questions_wanting_several_numbers():
    """一题要两个并列的数 —— spec 编码不了，必须返回 null。

    实测（2026-08-28 example_pool 分流 idx30）：问「被触达的客户总数和已转化
    客户数分别是多少」，LLM 输出 dims=['response_type']，拼出一张按响应类型
    分组的明细表。SQL 合法、能跑、返回非空，但形状与问题完全不同——和当初丢掉
    IN 约束、丢掉 Top-N 同一类失败：欠约束，guardrail 抓不到。

    MetricSpec 是 {metric_id, dims, filters, time_window} 四元组，只能产出
    一个聚合表达式。条件分列（COUNT(CASE WHEN ...)）、两个指标并列、占比，
    都在它的表达能力之外。
    """
    from chat_bi_agent.agents.p1.metric_resolver import _build_extractor_prompt

    prompt = _build_extractor_prompt(_get_cat())
    assert "分别是多少" in prompt
    assert "占比" in prompt or "比率" in prompt
    assert "metric_id=null" in prompt


# ---------------------- prefilter recall：剥时间修饰 ----------------------


def test_strip_time_modifiers_removes_absolute_dates():
    """catalog 的 alias 是光秃秃的名词，真实问题带一堆时间修饰。

    整句 embedding 被修饰语稀释，「2026 年上半年利息入账总金额是多少？」对
    「交易金额」只有 0.5929，掉在 0.63 阈值外。剥掉时间部分后升到 0.7392。
    这不是调阈值——是消掉长问题被稀释的结构性劣势。
    """
    from chat_bi_agent.agents.p1.metric_resolver import _strip_time_modifiers

    assert "2026" not in _strip_time_modifiers("2026 年上半年利息入账总金额是多少？")
    assert "利息入账总金额" in _strip_time_modifiers("2026 年上半年利息入账总金额是多少？")
    assert _strip_time_modifiers("2026 年 3 月 ATM 渠道的交易总金额").strip().startswith("ATM")


def test_strip_time_modifiers_keeps_business_terms():
    """只剥能被正则穷举的时间表达，不碰业务词。

    「月末」「节假日」是口径而非时间点——剥掉它们会改变问题含义。规则只在
    数字后面才吃「月」，所以裸的「月末」留得住。
    """
    from chat_bi_agent.agents.p1.metric_resolver import _strip_time_modifiers

    assert "月末" in _strip_time_modifiers("每个月末最后一天的活期存款余额加总")
    assert "法定节假日" in _strip_time_modifiers("2026 年第二季度所有法定节假日当天的交易总笔数")
    # 不含时间修饰的问题原样返回
    assert _strip_time_modifiers("活跃的高净值客户有多少人") == "活跃的高净值客户有多少人"


def test_router_falls_back_to_time_stripped_question():
    """整句不过阈值、剥掉时间后过——prefilter 应当命中。

    embed_fn 按文本给向量：带 "2026" 的与 alias 正交（cos=0），剥完时间的与
    alias 完全同向（cos=1）。只有真的两路都算了才可能命中。
    """
    from unittest.mock import MagicMock, patch

    from chat_bi_agent.agents.p1 import metric_resolver as mr
    from chat_bi_agent.agents.p1.metric_resolver import MetricRouter

    router = MetricRouter(
        _get_cat(),
        embed_fn=lambda texts: [[0.0, 1.0] if "2026" in t else [1.0, 0.0] for t in texts],
        threshold=0.9,
    )
    spec = MetricSpec(metric_id="deposit_balance")
    with patch.object(mr, "_resolve_to_spec_and_sql", MagicMock(return_value=(spec, "SELECT 1"))):
        rr = router.try_route("2026 年 4 月的存款余额")
    assert rr.prefilter_hit is True
    assert rr.cosine == pytest.approx(1.0)


def test_router_reports_the_higher_of_the_two_cosines():
    """两路取 max：剥完时间反而更差时，不该把原本的命中拖下水。"""
    from unittest.mock import MagicMock, patch

    from chat_bi_agent.agents.p1 import metric_resolver as mr
    from chat_bi_agent.agents.p1.metric_resolver import MetricRouter

    def embed_fn(texts):
        # alias（"存款余额" 等）与带 "2026" 的整句同向，剥完时间的残句正交
        return [[1.0, 0.0] if ("2026" in t or "余额" in t) else [0.0, 1.0] for t in texts]

    router = MetricRouter(_get_cat(), embed_fn=embed_fn, threshold=0.9)
    spec = MetricSpec(metric_id="deposit_balance")
    with patch.object(mr, "_resolve_to_spec_and_sql", MagicMock(return_value=(spec, "SELECT 1"))):
        rr = router.try_route("2026 年的存款情况")  # 剥完变成「的存款情况」，不含 2026/余额
    assert rr.prefilter_hit is True
    assert rr.cosine == pytest.approx(1.0)


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


# ---------------------- 全局 join 的可达性剪枝 ----------------------


_GLOBAL_JOIN_YAML = """
version: 1
joins:
  account: JOIN dim_account da ON {fact}.account_id = da.account_id
  branch: JOIN dim_branch dbr ON {fact}.branch_id = dbr.branch_id
metrics:
  - id: customer_count
    display_name: 客户数
    aliases: [客户数]
    fact_table: dim_customer
    fact_alias: dc
    metric_expr: COUNT(dc.customer_id)
    metric_alias: cnt
    hard_filters: []
    joins: {}
    dim_catalog:
      branch_city: {select_expr: "dbr.city", alias: city, requires_join: [branch]}
    filter_catalog: {}
"""


def test_unreferenced_global_join_is_not_attached(tmp_path):
    """全局 join 只在被引用时才挂到 metric 上。

    全局模板假设每个 fact 都有 account_id / branch_id / customer_id / product_id，
    但只有真 fct 表成立。dim_customer 当 fact 用时没有 account_id，那条 join 拼出来
    必然无效——既然没人 requires_join 它，就不该挂上去。
    """
    yml = tmp_path / "metrics.yaml"
    yml.write_text(_GLOBAL_JOIN_YAML, encoding="utf-8")
    m = MetricCatalog.from_yaml(yml).get("customer_count")
    assert sorted(m.joins) == ["branch"]


def test_global_join_attached_once_referenced_by_hard_filter(tmp_path):
    """被 hard_filter_joins 引用就必须挂上——剪枝不能剪掉真正要用的。"""
    body = _GLOBAL_JOIN_YAML.replace(
        "    hard_filters: []", "    hard_filters: []\n    hard_filter_joins: [account]"
    )
    yml = tmp_path / "metrics.yaml"
    yml.write_text(body, encoding="utf-8")
    m = MetricCatalog.from_yaml(yml).get("customer_count")
    assert sorted(m.joins) == ["account", "branch"]


def test_local_join_kept_even_when_unreferenced(tmp_path):
    """metric 自己写的 join 是逃生舱，作者意图明确，不剪。"""
    body = _GLOBAL_JOIN_YAML.replace(
        "    joins: {}",
        "    joins:\n      odd: JOIN dim_date dd ON dc.open_date = dd.dt",
    )
    yml = tmp_path / "metrics.yaml"
    yml.write_text(body, encoding="utf-8")
    m = MetricCatalog.from_yaml(yml).get("customer_count")
    assert sorted(m.joins) == ["branch", "odd"]


def test_rank_metrics_matches_the_cosine_try_route_gates_on(tmp_path):
    """离线扫阈值必须与线上 prefilter 同源，否则扫出来的是一个不存在的分布。

    `sweep_prefilter_threshold.py` 原本自己重算 cosine（单路，只 embed 整句）。
    加了双路召回之后它与 `try_route` 悄悄分叉——脚本注释里「离线与跑批偏差
    0.000218」那条保证失效，而失效方式是静默的：扫出来的阈值看着正常，
    只是对不上线上行为。把打分收敛成一个方法，物理上杜绝再次分叉。
    """
    catalog = MetricCatalog.from_yaml(
        Path(__file__).resolve().parents[2] / "config" / "metrics.yaml"
    )
    # 带时间修饰的问题——单路与双路在这里必然不同
    question = "2026 年上半年利息入账总金额是多少？"
    from chat_bi_agent.agents.p1.metric_resolver import (
        MetricRouter,
        _strip_time_modifiers,
    )

    calls: list[list[str]] = []

    def embed_fn(texts):
        calls.append(list(texts))
        # 让「剥掉时间修饰」的那一路明显更像，双路取 max 才看得出差别
        return [[1.0, 0.0] if "2026" in t else [0.0, 1.0] for t in texts]

    router = MetricRouter(catalog=catalog, embed_fn=embed_fn, threshold=0.0)
    calls.clear()
    ranked = router.rank_metrics(question)

    assert calls, "rank_metrics 应该自己 embed"
    assert len(calls[0]) == 2, f"应该 embed 整句 + 剥完两路，实际 {calls[0]}"
    assert calls[0][0] == question
    assert calls[0][1] == _strip_time_modifiers(question)
    assert ranked == sorted(ranked, key=lambda kv: kv[1], reverse=True), "必须按 cosine 倒序"
    assert {mid for mid, _ in ranked} == {m.id for m in catalog.metrics}, "每个指标占一个候选位"
