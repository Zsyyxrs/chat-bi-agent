"""metrics.yaml 的静态一致性校验。

存在理由（ADR-013 Update 2026-08-12）：模板里的列名只有真正 execute 才会被校验，
当时 6 个指标 × 全部 dim/filter 共 48 个组合里有 18 个是坏的——`fbd.account_type`
列不在 fact 表上、`ft.channel` 实际叫 `transaction_channel`。那次的结论是「catalog
类改动必须配一个全组合回归扫描」，本模块是它的静态版本：不打 DB，只对照
`schema_docs.yaml`，因此可以放进单测常规跑。

校验范围是 catalog 里**带别名前缀的列引用**（`fbd.balance` 这种）。裸标识符不查——
它们混着 SQL 函数名和关键字，判不准，宁可漏报也不误报。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from chat_bi_agent.agents.p1.metric_resolver import Metric, MetricCatalog
from chat_bi_agent.schema.loader import SchemaLoader

# 形如 `JOIN dim_branch dbr ON ...`（可带 LEFT/INNER 等前缀），同时抓表名与别名。
# metric_resolver 里的 _JOIN_ALIAS_RE 只抓别名，这里两个都要。
_JOIN_TABLE_ALIAS_RE = re.compile(
    r"^\s*(?:(?:LEFT|RIGHT|INNER|FULL|OUTER|CROSS)\s+)*JOIN\s+(\S+)\s+(\S+)\s+ON\b",
    re.IGNORECASE,
)

# `alias.column`。只认带点的，理由见模块 docstring。
_QUALIFIED_REF_RE = re.compile(r"\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)")

# 光秃秃的 `alias.column`，整串就这么多——dim 的 select_expr 是它才谈得上「对应的过滤器」
_PLAIN_COLUMN_RE = re.compile(r"^[A-Za-z_]\w*\.[A-Za-z_]\w*$")

# SQL 字符串字面量——先剥掉，否则 'BR_CITY_0006' 之类会被当成引用
_STRING_LITERAL_RE = re.compile(r"'[^']*'")


@dataclass(frozen=True)
class CatalogIssue:
    """catalog 里一处对不上 schema 的引用。

    severity:
      - `error`  该引用会出现在真实渲染的 SQL 里，现在就是坏的
      - `latent` 只出现在**不可达的 join** 里。全局 join 注册表会把每条 join 挂到
        每个指标上，但 `render_sql_from_spec` 只拼 `hard_filter_joins` 与被选中
        dim/filter 的 `requires_join`，所以没人引用的 join 永远拼不出来。它不是
        当前的错，而是「谁要给它加一条 requires_join 就会炸」的潜伏项。
    """

    metric_id: str
    ref: str  # `table.column` / 表名 / 解析不出表时的原始 `alias.column`
    message: str
    severity: str = "error"


@dataclass(frozen=True)
class Lineage:
    """一个指标依赖哪些物理表。

    分两层是有意的：拍平成一个表清单会让影响分析失真。改 `dim_branch` 影响的只是
    「用了 branch 维度/过滤的那部分查询」，而改 fact 表或 hard_filter 挂着的表，
    影响的是该指标的**每一次**查询。
    """

    metric_id: str
    required_tables: list[str]  # 每次查询都会出现在 FROM/JOIN 里
    conditional_tables: dict[str, list[str]]  # 表 → 触发它的 `dim:x` / `filter:y`


def metric_lineage(metric: Metric) -> Lineage:
    """推导单个指标的血缘。不可达的 join 不算依赖（永远拼不进 SQL）。"""
    join_table = {}
    for join_id, clause in metric.joins.items():
        m = _JOIN_TABLE_ALIAS_RE.match(clause)
        if m:
            join_table[join_id] = m.group(1)

    required = [metric.fact_table]
    for join_id in metric.hard_filter_joins:
        table = join_table.get(join_id)
        if table and table not in required:
            required.append(table)

    conditional: dict[str, list[str]] = {}
    triggers: list[tuple[str, list[str]]] = [
        *((f"dim:{d.id}", d.requires_join) for d in metric.dim_catalog.values()),
        *((f"filter:{f.id}", f.requires_join) for f in metric.filter_catalog.values()),
    ]
    for trigger, join_ids in triggers:
        for join_id in join_ids:
            table = join_table.get(join_id)
            if table is None or table in required:
                continue
            conditional.setdefault(table, [])
            if trigger not in conditional[table]:
                conditional[table].append(trigger)

    return Lineage(metric_id=metric.id, required_tables=required, conditional_tables=conditional)


def _reachable_joins(metric: Metric) -> set[str]:
    """能真正被渲染进 SQL 的 join_id——与 render_sql_from_spec 的 needed_joins 同源。"""
    reachable = set(metric.hard_filter_joins)
    for d in metric.dim_catalog.values():
        reachable |= set(d.requires_join)
    for f in metric.filter_catalog.values():
        reachable |= set(f.requires_join)
    return reachable


def _alias_to_table(metric: Metric) -> dict[str, str]:
    """该 metric 下 别名 → 物理表名。fact 别名 + 全部生效 join。"""
    mapping = {metric.fact_alias: metric.fact_table}
    for clause in metric.joins.values():
        m = _JOIN_TABLE_ALIAS_RE.match(clause)
        if m:
            table, alias = m.group(1), m.group(2)
            mapping[alias] = table
    return mapping


def _qualified_refs(*fragments: str | None) -> list[tuple[str, str]]:
    """从 SQL 片段里抽出 (别名, 列名)，保持出现顺序、去重。"""
    seen: set[tuple[str, str]] = set()
    refs: list[tuple[str, str]] = []
    for frag in fragments:
        if not frag:
            continue
        for alias, column in _QUALIFIED_REF_RE.findall(_STRING_LITERAL_RE.sub("", frag)):
            if (alias, column) not in seen:
                seen.add((alias, column))
                refs.append((alias, column))
    return refs


def validate_catalog(catalog: MetricCatalog, loader: SchemaLoader) -> list[CatalogIssue]:
    """比对 catalog 与 schema，返回全部对不上的引用（空列表 = 干净）。

    `loader` 需已 `load()`；本函数不触碰 embedding，因此不联网。
    """
    schema: dict[str, set[str]] = {
        doc.name: {c["name"] for c in doc.columns} for doc in loader.docs
    }
    issues: list[CatalogIssue] = []

    for metric in catalog.metrics:
        alias_map = _alias_to_table(metric)
        reachable = _reachable_joins(metric)
        reachable_clauses = [c for j, c in metric.joins.items() if j in reachable]
        latent_clauses = [c for j, c in metric.joins.items() if j not in reachable]

        # 只有可达 join 引入的别名才算「真的会用到这张表」
        live_tables = {metric.fact_table}
        for clause in reachable_clauses:
            m = _JOIN_TABLE_ALIAS_RE.match(clause)
            if m:
                live_tables.add(m.group(1))

        for alias, table in alias_map.items():
            if table not in schema:
                issues.append(
                    CatalogIssue(
                        metric_id=metric.id,
                        ref=table,
                        message=f"表不在 schema_docs.yaml 里（别名 {alias}）",
                        severity="error" if table in live_tables else "latent",
                    )
                )

        reachable_fragments: list[str | None] = [
            metric.metric_expr,
            metric.date_column,
            *metric.hard_filters,
            *reachable_clauses,
            *(d.select_expr for d in metric.dim_catalog.values()),
            *(f.column for f in metric.filter_catalog.values()),
        ]

        reported: set[str] = set()
        for severity, fragments in (
            ("error", reachable_fragments),
            ("latent", list(latent_clauses)),
        ):
            for alias, column in _qualified_refs(*fragments):
                table = alias_map.get(alias)
                if table is None:
                    ref, message = (
                        f"{alias}.{column}",
                        "别名没有对应的 fact 或 join，拼出来会是无效 SQL",
                    )
                elif table in schema and column not in schema[table]:
                    ref, message = (
                        f"{table}.{column}",
                        f"列不在表 {table} 上（别名 {alias}）",
                    )
                else:
                    continue
                if ref not in reported:
                    reported.add(ref)
                    issues.append(
                        CatalogIssue(
                            metric_id=metric.id, ref=ref, message=message, severity=severity
                        )
                    )

    return issues


def sibling_filter_gaps(catalog: MetricCatalog) -> list[CatalogIssue]:
    """同一张 fact 表上，兄弟指标能过滤的列本指标却不能——报为不对称。

    由来（2026-08-28 example_pool 分流）：`campaign_conversion_amount` 的
    filter_catalog 缺 `response_type`，而同表的 `campaign_response_count` 有。
    问「已转化客户的转化总金额」时 LLM 只能拿 campaign_name 顶替，拼出的 SQL
    语法正确、能跑、返回非空，只是数字偏大。执行反馈捕不到这类错。

    成因是复制粘贴：计数类指标被评测题逐步喂胖，金额类是从它复制后删剩的。

    同一列两边都有、但 `enum_values` 不一致时也报：必有一边是错的。实例是
    `transaction_amount` 把 channel 写成 [..., POS]（schema 里没这个值）而漏了
    AGENT/API，漏掉的值会让整条问题被判越界拒掉。

    **例外**：本指标把该列写死在 `hard_filters` 里，说明是有意收窄口径
    （`deposit_balance` 硬过滤 account_type），不算缺口。
    """
    by_fact: dict[str, list[Metric]] = {}
    for metric in catalog.metrics:
        by_fact.setdefault(metric.fact_table, []).append(metric)

    gaps: list[CatalogIssue] = []
    for fact_table, metrics in by_fact.items():
        if len(metrics) < 2:
            continue
        # 过滤器身份用「物理列」而非 filter_id：不同指标可能给同一列起不同的 id
        for metric in metrics:
            own_by_column = {f.column: f for f in metric.filter_catalog.values()}
            own_columns = set(own_by_column)
            pinned = {f"{a}.{c}" for a, c in _qualified_refs(*metric.hard_filters)}
            for sibling in metrics:
                if sibling.id == metric.id:
                    continue
                for f in sibling.filter_catalog.values():
                    if f.column in pinned:
                        continue
                    ref = f"{fact_table}.{f.column.split('.')[-1]}"
                    if any(g.metric_id == metric.id and g.ref == ref for g in gaps):
                        continue

                    if f.column not in own_columns:
                        message = (
                            f"同表指标 {sibling.id} 能按 {f.column} 过滤，本指标不能——"
                            "用户这么问时 LLM 会拿别的列顶替，SQL 跑得通但数字错"
                        )
                    else:
                        own = own_by_column[f.column]
                        missing = [
                            v for v in f.enum_values or [] if v not in (own.enum_values or [])
                        ]
                        if not missing:
                            continue
                        message = (
                            f"同表指标 {sibling.id} 的 {f.column} 枚举里有 "
                            f"{'、'.join(missing)}，本指标没有——必有一边是错的，"
                            "漏掉的那个值会被判越界而整条拒掉"
                        )

                    gaps.append(
                        CatalogIssue(
                            metric_id=metric.id, ref=ref, message=message, severity="asymmetry"
                        )
                    )
    return gaps


def dim_filter_gaps(catalog: MetricCatalog) -> list[CatalogIssue]:
    """同一个指标里，能 GROUP BY 的列却不能 WHERE——第二类不对称。

    与 `sibling_filter_gaps` 是两回事：那个比的是**兄弟指标之间**的过滤器，
    这个比的是**同一指标内部** dim_catalog 与 filter_catalog 的对齐。

    由来（2026-08-28 example_pool 分流）：`risk_event_count` 有 branch_city 维度、
    没有 branch_city 过滤器。idx24「上海分行 2026 年 5 月的反洗钱告警数量」
    cosine 0.7566（全场第二高），prefilter 命中了，resolve 阶段却没有能按分行名
    过滤的列，只能拒掉、静悄悄退回 NL2SQL。

    这类缺口不会让 SQL 出错——它让整条问题落不进语义层，所以执行反馈同样捕不到。
    成因也是复制粘贴：维度是从兄弟指标抄来的，过滤器漏跟。

    **例外**两条：
      - 该列写死在 `hard_filters` 里，那是有意收窄口径（同 `sibling_filter_gaps`）；
      - 该列就是 `date_column`，时间由 time_window 管，不需要另一个过滤器。

    非 `alias.column` 形态的 select_expr（`DATE_TRUNC(...)` 之类）不查——它们
    本来就没有一个可直接 WHERE 的对应列。
    """
    gaps: list[CatalogIssue] = []
    for metric in catalog.metrics:
        alias_map = _alias_to_table(metric)
        filter_columns = {f.column for f in metric.filter_catalog.values()}
        pinned = {f"{a}.{c}" for a, c in _qualified_refs(*metric.hard_filters)}

        for dim in metric.dim_catalog.values():
            column = dim.select_expr.strip()
            if not _PLAIN_COLUMN_RE.match(column):
                continue
            if column in filter_columns or column in pinned or column == metric.date_column:
                continue

            alias, _, name = column.partition(".")
            ref = f"{alias_map.get(alias, alias)}.{name}"
            gaps.append(
                CatalogIssue(
                    metric_id=metric.id,
                    ref=ref,
                    message=(
                        f"维度 {dim.id} 能按 {column} 分组，却没有同列的过滤器——"
                        "用户问「某个具体值的本指标」时 prefilter 命中、resolve 拒掉，"
                        "整条问题落不进语义层"
                    ),
                    severity="asymmetry",
                )
            )
    return gaps
