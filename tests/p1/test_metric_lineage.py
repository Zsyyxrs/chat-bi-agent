"""metrics.yaml 与 schema_docs.yaml 的一致性校验，以及指标血缘推导。

存在理由见 ADR-013 Update 2026-08-12：模板里的列名只有真正 execute 才会被校验，
当时 48 个组合里 18 个是坏的。本模块把那次教训固化成静态门禁——不打 DB。
"""

from __future__ import annotations

from pathlib import Path

from chat_bi_agent.agents.p1.metric_lineage import metric_lineage, validate_catalog
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
