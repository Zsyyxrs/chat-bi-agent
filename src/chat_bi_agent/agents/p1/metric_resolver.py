"""语义层 / Metric Resolver（ADR-013 原型）。

用户问题 → LLM 抽出 {metric_id, dims, filters, time_window} → 用 config/metrics.yaml
里的模板拼 SQL。命中就走这条 governed path；未命中回退给 SQLGenerator 走原来的
NL2SQL 路径。

设计原则：
- **只拼模板，不写 SQL**：模板已通过审核，LLM 只能选指标/维度/过滤，不能自造字段
- **enum 严格校验**：题面写"高净值"如果 LLM 没归一化成 HIGH_NET_WORTH → 拒绝
- **join 自动化 & 去重**：dim/filter 声明 requires_join → resolver 自动收集
- **未命中优雅退出**：LLM 觉得题目不匹配 → 返回 metric_id=null → 抛
  MetricResolverError("no metric matched") 由上游 fallback 处理

依赖：qwen_client.chat（结构化 JSON 输出，与 SQLGenerator 一致的调用风格）。
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from langfuse import observe

from chat_bi_agent.llm import qwen_client


class MetricResolverError(Exception):
    """未命中任何 metric、LLM 输出不合法、或 spec 不通过模板校验时抛出。"""


# ---------------------------- 数据类 ----------------------------


@dataclass
class MetricDim:
    id: str
    select_expr: str
    alias: str
    requires_join: list[str] = field(default_factory=list)


@dataclass
class MetricFilter:
    id: str
    column: str
    type: str  # "string" | "enum" | "date_range" | "numeric" | "boolean"
    enum_values: list[str] = field(default_factory=list)
    requires_join: list[str] = field(default_factory=list)


@dataclass
class Metric:
    id: str
    display_name: str
    aliases: list[str]
    fact_table: str
    fact_alias: str
    metric_expr: str
    metric_alias: str
    hard_filters: list[str]
    date_column: str | None
    joins: dict[str, str]
    dim_catalog: dict[str, MetricDim]
    filter_catalog: dict[str, MetricFilter]
    # hard_filters 自身依赖的 join，无条件拼进 FROM（dims/filters 为空时也要）
    hard_filter_joins: list[str] = field(default_factory=list)


@dataclass
class MetricCatalog:
    metrics: list[Metric]

    @classmethod
    def from_yaml(cls, path: Path) -> MetricCatalog:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        ms: list[Metric] = []
        for m in raw.get("metrics", []) or []:
            dim_catalog = {
                dim_id: MetricDim(
                    id=dim_id,
                    select_expr=v["select_expr"],
                    alias=v.get("alias", dim_id),
                    requires_join=v.get("requires_join") or [],
                )
                for dim_id, v in (m.get("dim_catalog") or {}).items()
            }
            filter_catalog = {
                fid: MetricFilter(
                    id=fid,
                    column=v["column"],
                    type=v["type"],
                    enum_values=v.get("enum_values") or [],
                    requires_join=v.get("requires_join") or [],
                )
                for fid, v in (m.get("filter_catalog") or {}).items()
            }
            ms.append(
                Metric(
                    id=m["id"],
                    display_name=m["display_name"],
                    aliases=m.get("aliases") or [],
                    fact_table=m["fact_table"],
                    fact_alias=m["fact_alias"],
                    metric_expr=m["metric_expr"],
                    metric_alias=m.get("metric_alias", "metric_value"),
                    hard_filters=m.get("hard_filters") or [],
                    date_column=m.get("date_column"),
                    joins=m.get("joins") or {},
                    dim_catalog=dim_catalog,
                    filter_catalog=filter_catalog,
                    hard_filter_joins=m.get("hard_filter_joins") or [],
                )
            )
        return cls(metrics=ms)

    def get(self, metric_id: str) -> Metric:
        for m in self.metrics:
            if m.id == metric_id:
                return m
        raise MetricResolverError(f"unknown metric_id: {metric_id!r}")


@dataclass
class MetricSpec:
    """LLM 从 NL 里抽出来的结构化查询意图。"""

    metric_id: str
    dims: list[str] = field(default_factory=list)
    filters: list[dict[str, Any]] = field(default_factory=list)
    time_window: dict[str, str] | None = None  # {"start": "2026-05-01", "end": "2026-05-31"}


# ---------------------------- SQL 拼装 ----------------------------

# 形如 `tbl.col` 或 `col` 的裸列引用（不含函数/运算/字面量）
_PLAIN_COLUMN_RE = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?")


def render_sql_from_spec(spec: MetricSpec, catalog: MetricCatalog) -> str:
    """把 MetricSpec 套上 metric 模板生成 SQL。所有验证都在这里做。"""
    metric = catalog.get(spec.metric_id)

    # 1. 收集需要的 join。hard_filters 的 join 先入队：它们无条件需要，
    #    且排在前面能保证 FROM 后的 join 顺序稳定。
    needed_joins: list[str] = list(metric.hard_filter_joins)
    for dim_id in spec.dims:
        if dim_id not in metric.dim_catalog:
            raise MetricResolverError(f"unknown dim {dim_id!r} for metric {metric.id}")
        for j in metric.dim_catalog[dim_id].requires_join:
            if j not in needed_joins:
                needed_joins.append(j)
    for f in spec.filters:
        col_id = f.get("col")
        if col_id not in metric.filter_catalog:
            raise MetricResolverError(f"unknown filter {col_id!r} for metric {metric.id}")
        for j in metric.filter_catalog[col_id].requires_join:
            if j not in needed_joins:
                needed_joins.append(j)

    for j in needed_joins:
        if j not in metric.joins:
            raise MetricResolverError(f"metric {metric.id} 声明 join={j!r} 但 joins 里没定义")

    # 2. SELECT 列表
    select_parts: list[str] = []
    group_by_parts: list[str] = []
    for dim_id in spec.dims:
        d = metric.dim_catalog[dim_id]
        # 别名与裸列名相同就别输出 AS：纯冗余，且会干扰按列名比对的下游
        # （评分器要剥掉带 AS 的列来还原"真实 schema 列"，全别名化会剥成空集）
        bare_col = d.select_expr.rsplit(".", 1)[-1]
        is_plain_column = _PLAIN_COLUMN_RE.fullmatch(d.select_expr) is not None
        if is_plain_column and bare_col == d.alias:
            select_parts.append(d.select_expr)
        else:
            select_parts.append(f"{d.select_expr} AS {d.alias}")
        group_by_parts.append(d.select_expr)
    select_parts.append(f"{metric.metric_expr} AS {metric.metric_alias}")

    # 3. WHERE 子句 = hard_filters + user filters + time_window
    where_parts: list[str] = list(metric.hard_filters)
    for f in spec.filters:
        fdef = metric.filter_catalog[f["col"]]
        op = f.get("op", "=")
        val = f.get("val")

        if op == "IN":
            if not isinstance(val, list):
                raise MetricResolverError(
                    f"IN filter {f['col']!r} val 必须是 list，收到 {type(val).__name__}"
                )
            if len(val) == 0:
                raise MetricResolverError(f"unsupported_op: empty IN for filter {f['col']!r}")

            if fdef.type == "enum":
                for v in val:
                    if v not in fdef.enum_values:
                        raise MetricResolverError(
                            f"bad enum value {v!r} for filter {f['col']}; "
                            f"expected one of {fdef.enum_values}"
                        )
                joined = ", ".join(f"'{v}'" for v in val)
            elif fdef.type == "string":
                joined = ", ".join(f"'{str(v).replace(chr(39), chr(39) * 2)}'" for v in val)
            elif fdef.type == "numeric":
                joined = ", ".join(str(v) for v in val)
            else:
                raise MetricResolverError(f"unsupported filter type {fdef.type!r} for IN")
            where_parts.append(f"{fdef.column} IN ({joined})")
            continue

        if fdef.type == "enum":
            if val not in fdef.enum_values:
                raise MetricResolverError(
                    f"bad enum value {val!r} for filter {f['col']}; "
                    f"expected one of {fdef.enum_values}"
                )
            where_parts.append(f"{fdef.column} {op} '{val}'")
        elif fdef.type == "string":
            safe = str(val).replace("'", "''")
            where_parts.append(f"{fdef.column} {op} '{safe}'")
        elif fdef.type == "numeric":
            where_parts.append(f"{fdef.column} {op} {val}")
        elif fdef.type == "boolean":
            if isinstance(val, str):
                truthy = val.strip().lower() in {"true", "t", "1", "yes", "是"}
            else:
                truthy = bool(val)
            where_parts.append(f"{fdef.column} {op} {'TRUE' if truthy else 'FALSE'}")
        else:
            raise MetricResolverError(f"unsupported filter type {fdef.type!r}")

    if spec.time_window and metric.date_column:
        start = spec.time_window.get("start")
        end = spec.time_window.get("end")
        if start:
            where_parts.append(f"{metric.date_column} >= DATE '{start}'")
        if end:
            where_parts.append(f"{metric.date_column} <= DATE '{end}'")

    # 4. 拼 SQL
    lines: list[str] = []
    lines.append("SELECT " + ", ".join(select_parts))
    lines.append(f"FROM {metric.fact_table} {metric.fact_alias}")
    for j in needed_joins:
        lines.append(metric.joins[j])
    if where_parts:
        lines.append("WHERE " + "\n  AND ".join(where_parts))
    if group_by_parts:
        lines.append("GROUP BY " + ", ".join(group_by_parts))

    return "\n".join(lines)


# ---------------------------- LLM spec 抽取 ----------------------------


_JSON_FENCE_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def _build_extractor_prompt(catalog: MetricCatalog) -> str:
    """让 LLM 从 NL 抽 {metric_id, dims, filters, time_window} 的 system prompt。"""
    lines = [
        "你是一个银行业务语义层指标抽取器。任务：读用户中文问题，从给定 metric 目录里",
        "选出**唯一**匹配的指标，抽出它需要的维度、过滤、时间窗口。**只输出 JSON**。",
        "",
        "严格要求：",
        "1. 只输出一个 JSON 对象，必须用 ```json``` 代码块包裹",
        "2. 字段：metric_id（str 或 null）、dims（str 数组）、filters（对象数组）、"
        "time_window（对象或 null）",
        "3. 如果没有任何 metric 匹配问题（例如题目是要列出明细、事件流、单条记录），",
        "   **必须**返回 metric_id=null，其他字段留空——不要硬凑",
        "4. filters 元素形如 {col, op, val}；op 支持 '=' 与 'IN'",
        "   - 单值用 {col, op: '=', val: 'X'}",
        "   - **多值必须用** {col, op: 'IN', val: ['X', 'Y']}——val 是数组",
        "   - 例：「杭州和南京两个分行」→ {col: 'branch_id', op: 'IN', "
        "val: ['BR_CITY_0000', 'BR_CITY_0002']}",
        "5. **约束一个都不能丢**：题目里的每个限定条件（分行、层级、时间、类型…）"
        "都必须落到 filters 或 time_window 里。",
        "   如果某个约束在本指标的可用 filters 里表达不了，**返回 metric_id=null**——",
        "   宁可退回 NL2SQL，也不要丢掉约束后拼一个「看起来对」的查询。",
        "   反例：问「杭州和南京两个分行的客户数」却输出 dims=['branch_city'] 且不带 "
        "branch 过滤——这是按全部城市分组，答的是另一个问题",
        "6. **val 必须落在 col 的值域里**：ID 列传 ID，名称列传名称，不要混。",
        "   题面常写成「杭州（BR_CITY_0000）」这种「名称（ID）」形式——",
        "   选 branch_id 就传 'BR_CITY_0000'，选 branch_city 就传 '杭州'。",
        "   传错不会报错，只会静默返回 0 行，比报错更难查",
        "7. filter 是 enum 类型时，val **必须**用目录里给的英文枚举代码，禁止用中文",
        "8. time_window 形如 {start: 'YYYY-MM-DD', end: 'YYYY-MM-DD'}；只有指标支持时间窗才填",
        "9. dims 只能选目录里列出的",
        "",
        "可用 metric 目录：",
    ]
    for m in catalog.metrics:
        lines.append("")
        lines.append(f"- **{m.id}**：{m.display_name}")
        if m.aliases:
            lines.append(f"  同义词：{'、'.join(m.aliases)}")
        if m.dim_catalog:
            lines.append(f"  可用 dims：{', '.join(m.dim_catalog)}")
        if m.filter_catalog:
            filter_desc = []
            for fid, f in m.filter_catalog.items():
                if f.type == "enum":
                    filter_desc.append(f"{fid}(enum: {'/'.join(f.enum_values)})")
                else:
                    filter_desc.append(f"{fid}({f.type})")
            lines.append(f"  可用 filters：{', '.join(filter_desc)}")
        if m.date_column:
            lines.append("  支持 time_window")
    lines.append("")
    lines.append("输出示例：")
    lines.append("```json")
    lines.append(
        '{"metric_id":"deposit_balance","dims":["branch_city"],'
        '"filters":[{"col":"customer_tier","op":"=","val":"HIGH_NET_WORTH"}],'
        '"time_window":{"start":"2026-05-01","end":"2026-05-31"}}'
    )
    lines.append("```")
    return "\n".join(lines)


def _parse_spec(raw: str) -> MetricSpec:
    m = _JSON_FENCE_RE.search(raw)
    candidate = m.group(1) if m else raw.strip()
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as e:
        raise MetricResolverError(
            f"LLM 输出不是合法 JSON: {e}; raw 前 200 字符: {raw[:200]}"
        ) from e

    metric_id = data.get("metric_id")
    if metric_id is None:
        raise MetricResolverError("no metric matched (LLM returned metric_id=null)")
    if not isinstance(metric_id, str):
        raise MetricResolverError(f"metric_id 必须是 str，收到 {type(metric_id).__name__}")

    return MetricSpec(
        metric_id=metric_id,
        dims=list(data.get("dims") or []),
        filters=list(data.get("filters") or []),
        time_window=data.get("time_window"),
    )


def _resolve_to_spec_and_sql(question: str, catalog: MetricCatalog) -> tuple[MetricSpec, str]:
    """内部：question → (spec, sql)。失败抛 MetricResolverError。"""
    system_prompt = _build_extractor_prompt(catalog)
    user_prompt = f"用户问题：{question}\n请输出 JSON。"
    chat_result = qwen_client.chat(system_prompt=system_prompt, user_prompt=user_prompt)
    spec = _parse_spec(chat_result.content)
    sql = render_sql_from_spec(spec, catalog)
    return spec, sql


@observe(name="metric_resolve")
def resolve(question: str, catalog: MetricCatalog) -> str:
    """端到端：question → SQL；失败抛 MetricResolverError（调用方兜底走 NL2SQL）。"""
    _, sql = _resolve_to_spec_and_sql(question, catalog)
    return sql


# ---------------------------- 前置路由 ----------------------------


@dataclass
class RouteResult:
    """MetricRouter.try_route 的返回值——永远不 raise。"""

    prefilter_hit: bool
    metric_id: str | None
    cosine: float
    sql: str | None
    spec: MetricSpec | None
    # "no_metric" | "unknown_dim" | "enum_out_of_range" | "unsupported_op" | None
    fail_reason: str | None


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _classify_metric_error(msg: str) -> str:
    """把 MetricResolverError 的 message 映射到 fail_reason 枚举。"""
    low = msg.lower()
    if "no metric matched" in low or "metric_id=null" in low:
        return "no_metric"
    if "unknown dim" in low:
        return "unknown_dim"
    if "bad enum value" in low:
        return "enum_out_of_range"
    if "unsupported_op" in low or "unsupported filter type" in low or "in filter" in low:
        return "unsupported_op"
    if "unknown filter" in low or "unknown metric_id" in low:
        return "unknown_dim"  # 归到最相近的
    return "unknown_dim"  # 兜底：未识别的 metric 结构错


class MetricRouter:
    """catalog embedding prefilter + resolve 的一体化路由层。构造时批量 embed 所有 aliases。"""

    def __init__(
        self,
        catalog: MetricCatalog,
        embed_fn,  # Callable[[list[str]], list[list[float]]]
        threshold: float = 0.7,
    ):
        self.catalog = catalog
        self.embed_fn = embed_fn
        self.threshold = threshold

        # 构建索引：list of (metric_id, alias_vec)
        all_aliases: list[tuple[str, str]] = []
        for m in catalog.metrics:
            for a in m.aliases:
                all_aliases.append((m.id, a))

        self._alias_index: list[tuple[str, list[float]]] = []
        if all_aliases:
            texts = [a for _, a in all_aliases]
            vecs = embed_fn(texts)
            self._alias_index = [
                (mid, vec) for (mid, _), vec in zip(all_aliases, vecs, strict=True)
            ]

    def try_route(self, question: str) -> RouteResult:
        """从不抛异常。"""
        # 1. embed 问题
        q_vec = self.embed_fn([question])[0]

        # 2. 找 top-1 alias
        best_mid: str | None = None
        best_cos: float = -1.0
        for mid, vec in self._alias_index:
            cos = _cosine(q_vec, vec)
            if cos > best_cos:
                best_cos = cos
                best_mid = mid

        # 3. 阈值 gate
        if best_cos < self.threshold or best_mid is None:
            return RouteResult(
                prefilter_hit=False,
                metric_id=None,
                cosine=best_cos if best_cos > -1.0 else 0.0,
                sql=None,
                spec=None,
                fail_reason=None,
            )

        # 4. resolve
        try:
            spec, sql = _resolve_to_spec_and_sql(question, self.catalog)
        except MetricResolverError as e:
            return RouteResult(
                prefilter_hit=True,
                metric_id=best_mid,
                cosine=best_cos,
                sql=None,
                spec=None,
                fail_reason=_classify_metric_error(str(e)),
            )

        return RouteResult(
            prefilter_hit=True,
            metric_id=spec.metric_id,
            cosine=best_cos,
            sql=sql,
            spec=spec,
            fail_reason=None,
        )
