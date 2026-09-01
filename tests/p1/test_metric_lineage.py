"""metrics.yaml 与 schema_docs.yaml 的一致性校验，以及指标血缘推导。

存在理由见 ADR-013 Update 2026-08-12：模板里的列名只有真正 execute 才会被校验，
当时 48 个组合里 18 个是坏的。本模块把那次教训固化成静态门禁——不打 DB。
"""

from __future__ import annotations

from pathlib import Path

from chat_bi_agent.agents.p1.metric_lineage import (
    dim_filter_gaps,
    metric_lineage,
    sibling_filter_gaps,
    validate_catalog,
)
from chat_bi_agent.agents.p1.metric_resolver import MetricCatalog
from chat_bi_agent.schema.loader import SchemaLoader

REPO_ROOT = Path(__file__).resolve().parents[2]
METRICS_YAML = REPO_ROOT / "config" / "metrics.yaml"


def _loader() -> SchemaLoader:
    loader = SchemaLoader()
    loader.load()
    return loader


def _catalog_from(tmp_path: Path, body: str) -> MetricCatalog:
    yml = tmp_path / "metrics.yaml"
    yml.write_text(body, encoding="utf-8")
    return MetricCatalog.from_yaml(yml)


# ---------------------- 检出能力 ----------------------


def test_detects_column_missing_from_schema(tmp_path):
    """metric_expr 引用了 schema 里不存在的列——ADR-013 里 fbd.account_type 那一类。"""
    catalog = _catalog_from(
        tmp_path,
        """
version: 1
metrics:
  - id: deposit_balance
    display_name: 存款余额
    aliases: [存款余额]
    fact_table: fct_balance_daily
    fact_alias: fbd
    metric_expr: AVG(fbd.account_type)
    metric_alias: avg_balance
    hard_filters: []
    date_column: fbd.dt
    joins: {}
    dim_catalog: {}
    filter_catalog: {}
""",
    )
    issues = validate_catalog(catalog, _loader())
    assert [i.ref for i in issues] == ["fct_balance_daily.account_type"]


def test_detects_table_missing_from_schema(tmp_path):
    catalog = _catalog_from(
        tmp_path,
        """
version: 1
metrics:
  - id: ghost
    display_name: 幽灵指标
    aliases: [幽灵]
    fact_table: fct_not_a_real_table
    fact_alias: fx
    metric_expr: COUNT(*)
    metric_alias: cnt
    hard_filters: []
    joins: {}
    dim_catalog: {}
    filter_catalog: {}
""",
    )
    issues = validate_catalog(catalog, _loader())
    assert [i.ref for i in issues] == ["fct_not_a_real_table"]


def test_detects_bad_column_behind_a_join_alias(tmp_path):
    """join 引入的别名也要能解析回表——ADR-013 里 ft.channel 那一类。"""
    catalog = _catalog_from(
        tmp_path,
        """
version: 1
joins:
  account: JOIN dim_account da ON {fact}.account_id = da.account_id
metrics:
  - id: deposit_balance
    display_name: 存款余额
    aliases: [存款余额]
    fact_table: fct_balance_daily
    fact_alias: fbd
    metric_expr: AVG(fbd.balance)
    metric_alias: avg_balance
    hard_filters:
      - "da.no_such_col IN ('CURRENT')"
    hard_filter_joins: [account]
    joins: {}
    dim_catalog: {}
    filter_catalog: {}
""",
    )
    issues = validate_catalog(catalog, _loader())
    assert [i.ref for i in issues] == ["dim_account.no_such_col"]


def test_detects_bad_columns_in_dim_and_filter_catalog(tmp_path):
    catalog = _catalog_from(
        tmp_path,
        """
version: 1
metrics:
  - id: customer_count
    display_name: 客户数
    aliases: [客户数]
    fact_table: dim_customer
    fact_alias: dc
    metric_expr: COUNT(*)
    metric_alias: cnt
    hard_filters: []
    joins: {}
    dim_catalog:
      tier: {select_expr: "dc.bogus_dim", alias: tier}
    filter_catalog:
      tier: {column: "dc.bogus_filter", type: string}
""",
    )
    issues = validate_catalog(catalog, _loader())
    assert sorted(i.ref for i in issues) == ["dim_customer.bogus_dim", "dim_customer.bogus_filter"]


def test_detects_unknown_alias(tmp_path):
    """引用了一个没有任何 join 定义过的别名——拼出来必然是无效 SQL。"""
    catalog = _catalog_from(
        tmp_path,
        """
version: 1
metrics:
  - id: customer_count
    display_name: 客户数
    aliases: [客户数]
    fact_table: dim_customer
    fact_alias: dc
    metric_expr: COUNT(*)
    metric_alias: cnt
    hard_filters: []
    joins: {}
    dim_catalog:
      city: {select_expr: "dbr.city", alias: city}
    filter_catalog: {}
""",
    )
    issues = validate_catalog(catalog, _loader())
    assert [i.ref for i in issues] == ["dbr.city"]


def test_clean_fixture_catalog_has_no_issues(tmp_path):
    catalog = _catalog_from(
        tmp_path,
        """
version: 1
joins:
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
    filter_catalog:
      customer_tier: {column: "dc.customer_tier", type: string}
""",
    )
    assert validate_catalog(catalog, _loader()) == []


# ---------------------- 真实 catalog 门禁 ----------------------


def test_real_catalog_matches_real_schema():
    """config/metrics.yaml 里的每个表名/列名都必须在 schema_docs.yaml 里存在。

    这是 ADR-013 那次「18/48 组合全挂」的静态门禁：catalog 改动一旦引入
    不存在的表或列，这条会红，不必等真打 PG。
    """
    issues = [
        i
        for i in validate_catalog(MetricCatalog.from_yaml(METRICS_YAML), _loader())
        if i.severity == "error"
    ]
    assert issues == [], "\n".join(f"{i.metric_id}: {i.ref} — {i.message}" for i in issues)


# ---------------------- 可达性：真错 vs 潜伏 ----------------------

_UNREACHABLE_JOIN_YAML = """
version: 1
joins:
  account: JOIN dim_account da ON {fact}.account_id = da.account_id
metrics:
  - id: branch_count
    display_name: 网点数
    aliases: [网点数]
    fact_table: dim_branch
    fact_alias: dbr
    metric_expr: COUNT(dbr.branch_id)
    metric_alias: cnt
    hard_filters: []
    joins: {}
    dim_catalog: {}
    filter_catalog: {}
"""


def test_unreferenced_global_join_produces_no_issue(tmp_path):
    """全局 join 没人引用时会在加载期被剪掉，因此连潜伏项都不该有。

    dim_branch 没有 account_id 列，这条模板套在它身上是坏的——但 `_resolve_joins`
    只挂被 `requires_join` / `hard_filter_joins` 引用的全局 join，所以它根本不存在。
    """
    assert validate_catalog(_catalog_from(tmp_path, _UNREACHABLE_JOIN_YAML), _loader()) == []


def test_unreferenced_local_join_is_latent(tmp_path):
    """本地 join 是作者显式写的逃生舱，不剪；没人引用时报成潜伏项。"""
    body = _UNREACHABLE_JOIN_YAML.replace(
        "    joins: {}",
        "    joins:\n      odd: JOIN dim_account da ON dbr.account_id = da.account_id",
    )
    issues = validate_catalog(_catalog_from(tmp_path, body), _loader())
    assert [(i.ref, i.severity) for i in issues] == [("dim_branch.account_id", "latent")]


def test_reachable_join_with_bad_column_is_error(tmp_path):
    """同一条 join，一旦被 hard_filter_joins 接上就是真错。"""
    body = _UNREACHABLE_JOIN_YAML.replace(
        "    hard_filters: []", "    hard_filters: []\n    hard_filter_joins: [account]"
    )
    issues = validate_catalog(_catalog_from(tmp_path, body), _loader())
    assert [(i.ref, i.severity) for i in issues] == [("dim_branch.account_id", "error")]


# ---------------------- 血缘 ----------------------


def test_lineage_separates_required_from_conditional(tmp_path):
    """必然依赖 vs 条件依赖必须分开——拍平会让影响分析失真。

    改 dim_branch 影响的是「用了 branch 维度的那部分查询」，不是全部 deposit_balance
    查询；而改 dim_account 会影响该指标的每一次查询（hard_filter 挂在上面）。
    """
    catalog = _catalog_from(
        tmp_path,
        """
version: 1
joins:
  account: JOIN dim_account da ON {fact}.account_id = da.account_id
  branch: JOIN dim_branch dbr ON {fact}.branch_id = dbr.branch_id
  product: JOIN dim_product dp ON {fact}.product_id = dp.product_id
metrics:
  - id: deposit_balance
    display_name: 存款余额
    aliases: [存款余额]
    fact_table: fct_balance_daily
    fact_alias: fbd
    metric_expr: AVG(fbd.balance)
    metric_alias: avg_balance
    hard_filters:
      - "da.account_type IN ('CURRENT','SAVING')"
    hard_filter_joins: [account]
    date_column: fbd.dt
    joins: {}
    dim_catalog:
      branch_name: {select_expr: "dbr.branch_name", alias: branch_name, requires_join: [branch]}
    filter_catalog:
      branch_city: {column: "dbr.city", type: string, requires_join: [branch]}
""",
    )
    lin = metric_lineage(catalog.get("deposit_balance"))

    assert lin.required_tables == ["fct_balance_daily", "dim_account"]
    assert lin.conditional_tables == {"dim_branch": ["dim:branch_name", "filter:branch_city"]}
    # product join 挂着但没人引用，不算依赖
    assert "dim_product" not in lin.conditional_tables


# ---------------------- 同表指标的过滤器对称性 ----------------------

_SIBLINGS_YAML = """
version: 1
metrics:
  - id: risk_event_count
    display_name: 风险事件数
    aliases: [风险事件数]
    fact_table: fct_risk_event
    fact_alias: fre
    metric_expr: COUNT(*)
    metric_alias: cnt
    hard_filters: []
    joins: {}
    dim_catalog: {}
    filter_catalog:
      severity: {column: "fre.severity", type: string}
      status:   {column: "fre.status",   type: string}
  - id: risk_event_amount
    display_name: 风险事件金额
    aliases: [风险事件金额]
    fact_table: fct_risk_event
    fact_alias: fre
    metric_expr: SUM(fre.amount)
    metric_alias: amt
    hard_filters: []
    joins: {}
    dim_catalog: {}
    filter_catalog:
      severity: {column: "fre.severity", type: string}
"""


def test_sibling_metric_missing_a_filter_is_reported(tmp_path):
    """同一张 fact 表上，兄弟指标能过滤的列本指标也该能过滤。

    由来见 2026-08-28 分流：`campaign_conversion_amount` 缺 `response_type`，
    LLM 只能拿 campaign_name 顶替，SQL 跑得通、返回非空、数字偏大——执行反馈
    捕不到。计数类指标被评测题喂胖了，金额类是复制后删剩的。
    """
    gaps = sibling_filter_gaps(_catalog_from(tmp_path, _SIBLINGS_YAML))
    assert [(g.metric_id, g.ref) for g in gaps] == [("risk_event_amount", "fct_risk_event.status")]


def test_filter_written_into_hard_filters_is_not_a_gap(tmp_path):
    """本指标把该列写死在 hard_filters 里 —— 那是有意收窄口径，不是缺口。

    `deposit_balance` 硬过滤 `account_type`，就不该再把 account_type 开放成
    可选过滤器；`total_balance` 开放它是对的。两者不构成不对称。
    """
    body = _SIBLINGS_YAML.replace(
        """    metric_alias: amt
    hard_filters: []""",
        """    metric_alias: amt
    hard_filters:
      - "fre.status = 'OPEN'\"""",
    )
    assert sibling_filter_gaps(_catalog_from(tmp_path, body)) == []


def test_real_catalog_has_no_sibling_filter_gaps():
    """config/metrics.yaml 的门禁：同表指标的可选过滤器必须对齐。

    新增一个指标时最容易犯的错就是从兄弟指标复制后删剩几个 filter，这条会红。
    """
    gaps = sibling_filter_gaps(MetricCatalog.from_yaml(METRICS_YAML))
    assert gaps == [], "\n".join(f"{g.metric_id}: {g.ref} — {g.message}" for g in gaps)


def test_sibling_metrics_disagreeing_on_enum_values_is_reported(tmp_path):
    """同一列在两个指标里给了不同的 enum_values——必有一个是错的。

    实例：`transaction_amount` 把 channel 写成 [ATM, COUNTER, MOBILE, INTERNET, POS]，
    而同表的 `transaction_count` 写的是 [..., AGENT, API]。schema 里没有 POS。
    枚举漏一个值，用户问「利息入账」这类就会被判 enum 越界而整条拒掉。
    """
    body = _SIBLINGS_YAML.replace(
        """      severity: {column: "fre.severity", type: string}
      status:   {column: "fre.status",   type: string}""",
        """      severity:
        column: "fre.severity"
        type: enum
        enum_values: [CRITICAL, HIGH, MEDIUM]
      status:   {column: "fre.status",   type: string}""",
    ).replace(
        """    filter_catalog:
      severity: {column: "fre.severity", type: string}""",
        """    filter_catalog:
      status:   {column: "fre.status",   type: string}
      severity:
        column: "fre.severity"
        type: enum
        enum_values: [CRITICAL, HIGH]""",
    )
    gaps = sibling_filter_gaps(_catalog_from(tmp_path, body))
    assert [(g.metric_id, g.ref) for g in gaps] == [
        ("risk_event_amount", "fct_risk_event.severity")
    ]
    assert "MEDIUM" in gaps[0].message


# ---------------------- dim ↔ filter 不对称 ----------------------

_DIM_FILTER_YAML = """
version: 1
joins:
  branch: "JOIN dim_branch dbr ON {fact}.branch_id = dbr.branch_id"
metrics:
  - id: risk_event_count
    display_name: 风险事件数
    aliases: [风险事件数]
    fact_table: fct_risk_event
    fact_alias: fre
    metric_expr: COUNT(*)
    metric_alias: cnt
    hard_filters: []
    date_column: fre.dt
    dim_catalog:
      branch_city:
        select_expr: dbr.city
        alias: branch_city
        requires_join: [branch]
    filter_catalog:
      severity: {column: "fre.severity", type: string}
"""


def test_dimension_without_matching_filter_is_reported(tmp_path):
    """能 GROUP BY 却不能 WHERE 的列——第二类不对称。

    由来见 2026-08-28 分流：idx24「上海分行 2026 年 5 月的反洗钱告警数量」
    cosine 0.7566（全场第二高），prefilter 命中了，却因为 `risk_event_count`
    有 branch_city 维度、没有 branch_city 过滤器而被 resolve 拒掉。
    维度侧的对称性缺口不会让 SQL 出错，它让整条问题落不进语义层。
    """
    gaps = dim_filter_gaps(_catalog_from(tmp_path, _DIM_FILTER_YAML))
    assert [(g.metric_id, g.ref) for g in gaps] == [("risk_event_count", "dim_branch.city")]


def test_dimension_that_is_also_a_filter_is_not_a_gap(tmp_path):
    """维度与过滤器都挂着同一列——这正是要求的对称状态。"""
    body = _DIM_FILTER_YAML.replace(
        """      severity: {column: "fre.severity", type: string}""",
        """      severity: {column: "fre.severity", type: string}
      branch_city:
        column: "dbr.city"
        type: string
        requires_join: [branch]""",
    )
    assert dim_filter_gaps(_catalog_from(tmp_path, body)) == []


def test_dimension_pinned_in_hard_filters_is_not_a_gap(tmp_path):
    """该列被 hard_filters 写死——口径有意收窄，开放成过滤器反而是错的。"""
    body = _DIM_FILTER_YAML.replace(
        """    hard_filters: []""",
        """    hard_filters:
      - "dbr.city = 'SH'"
    hard_filter_joins: [branch]""",
    )
    assert dim_filter_gaps(_catalog_from(tmp_path, body)) == []


def test_dimension_on_the_date_column_is_not_a_gap(tmp_path):
    """时间列由 time_window 管，不该再要求一个同列过滤器。"""
    body = _DIM_FILTER_YAML.replace(
        """      branch_city:
        select_expr: dbr.city
        alias: branch_city
        requires_join: [branch]""",
        """      dt:
        select_expr: fre.dt
        alias: dt""",
    )
    assert dim_filter_gaps(_catalog_from(tmp_path, body)) == []


def test_real_catalog_has_no_dim_filter_gaps():
    """config/metrics.yaml 的门禁：能 GROUP BY 的列必须也能 WHERE。

    维度是从兄弟指标复制过来的，过滤器往往漏跟——漏了不报错，只是让
    「上海分行的 X」这类问题在 resolve 阶段被拒，静悄悄退回 NL2SQL。
    """
    gaps = dim_filter_gaps(MetricCatalog.from_yaml(METRICS_YAML))
    assert gaps == [], "\n".join(f"{g.metric_id}: {g.ref} — {g.message}" for g in gaps)


# ---------------------- row_policy 静态校验 ----------------------
# RLAC 的条件是渲染期才展开的，写错要到「某个特定权限的用户来查」才炸。
# 静态化到 gate 里，改完 metrics.yaml 就能发现。


def _policy_cat(tmp_path, policies: str, joins: str = "{}"):
    from chat_bi_agent.agents.p1.metric_resolver import MetricCatalog

    yml = tmp_path / "metrics.yaml"
    yml.write_text(
        f"""
version: 1
metrics:
  - id: customer_count
    display_name: 客户数
    aliases: [客户数]
    fact_table: dim_customer
    fact_alias: dc
    metric_expr: COUNT(*)
    metric_alias: cnt
    hard_filters: []
    joins: {joins}
    dim_catalog: {{}}
    filter_catalog: {{}}
    row_policies:
{policies}
""",
        encoding="utf-8",
    )
    return MetricCatalog.from_yaml(yml)


def test_policy_placeholder_not_declared_is_error(tmp_path):
    from chat_bi_agent.agents.p1.metric_lineage import row_policy_issues

    cat = _policy_cat(
        tmp_path,
        '      - name: p1\n'
        '        requires: [branch_id]\n'
        '        condition: "dc.branch_id = @branch_id AND dc.city = @city"\n',
    )
    issues = row_policy_issues(cat)
    assert any(i.severity == "error" and "city" in i.message for i in issues)


def test_policy_declared_but_unused_is_latent(tmp_path):
    from chat_bi_agent.agents.p1.metric_lineage import row_policy_issues

    cat = _policy_cat(
        tmp_path,
        '      - name: p1\n'
        '        requires: [branch_id, unused_prop]\n'
        '        condition: "dc.branch_id = @branch_id"\n',
    )
    issues = row_policy_issues(cat)
    assert any(i.severity == "latent" and "unused_prop" in i.message for i in issues)


def test_policy_requires_join_must_exist(tmp_path):
    from chat_bi_agent.agents.p1.metric_lineage import row_policy_issues

    cat = _policy_cat(
        tmp_path,
        '      - name: p1\n'
        '        requires: [region]\n'
        '        condition: "dbr.region = @region"\n'
        '        requires_join: [no_such_join]\n',
    )
    issues = row_policy_issues(cat)
    assert any(i.severity == "error" and "no_such_join" in i.message for i in issues)


def test_wellformed_policy_has_no_issues(tmp_path):
    from chat_bi_agent.agents.p1.metric_lineage import row_policy_issues

    cat = _policy_cat(
        tmp_path,
        '      - name: p1\n'
        '        requires: [branch_id]\n'
        '        condition: "dc.branch_id = @branch_id"\n',
    )
    assert row_policy_issues(cat) == []


def test_real_catalog_row_policies_clean():
    """生产 catalog 现在没有 row_policies，加了之后这条会替我们守住。"""
    from pathlib import Path

    from chat_bi_agent.agents.p1.metric_lineage import row_policy_issues
    from chat_bi_agent.agents.p1.metric_resolver import MetricCatalog

    root = Path(__file__).resolve().parents[2]
    cat = MetricCatalog.from_yaml(root / "config" / "metrics.yaml")
    errs = [i for i in row_policy_issues(cat) if i.severity == "error"]
    assert errs == [], errs
