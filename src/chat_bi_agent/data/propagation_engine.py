"""Event propagation engine: apply event effects to database rows."""

import random
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Optional


@dataclass
class PropagationRule:
    """单条传导规则：事件如何影响某一指标。"""

    target_table: str  # fct_transaction, fct_balance_daily, dim_customer
    target_column: str  # balance, amount, aum
    metric_name: str  # 业务指标名称
    delta: float  # 变化幅度（百分比），-8.5 表示下降 8.5%
    delay_days: int  # 延迟多少天开始显现
    ramp_days: int  # 渐变天数（0 = 瞬间，>0 = 线性渐变）
    ramp_type: str = "linear"  # linear, exponential
    affected_account_sample: float = 1.0  # 影响多少比例的账户
    affected_customer_sample: float = 1.0  # 影响多少比例的客户
    renewal_rate: Optional[float] = None  # 续作率（产品转移）
    related_products: Optional[list[str]] = None  # 转移目标产品
    transaction_type: Optional[str] = None  # 仅影响特定交易类型
    transaction_channel: Optional[list[str]] = None  # 仅影响特定渠道
    is_percentage: bool = True  # delta 是百分比还是绝对值
    # 新增：维度过滤字段
    branch_ids: Optional[list[str]] = None
    customer_tiers: Optional[list[str]] = None
    branch_levels: Optional[list[str]] = None
    product_ids: Optional[list[str]] = None
    product_subcategories: Optional[list[str]] = None
    # 新增：效应类型
    effect_type: str = "transient"  # "transient" | "sustained"


class PropagationEngine:
    """
    将事件转换为数据修改。给定一条事件，在 seed 过程中对生成的行进行修改。

    使用场景：
    1. 生成交易时，检查日期是否在事件影响期内 → 修改 amount
    2. 生成余额快照时，检查日期是否在事件影响期内 → 修改 balance
    3. 生成客户维度时，检查是否在事件影响的客户范围 → 修改 aum
    """

    def __init__(
        self,
        seed: int = 42,
        customer_index: dict[str, dict] | None = None,
        branch_index: dict[str, dict] | None = None,
        product_index: dict[str, dict] | None = None,
    ):
        self.seed = seed
        self.customer_index = customer_index
        self.branch_index = branch_index
        self.product_index = product_index
        random.seed(seed)

    def should_apply_rule(
        self,
        rule: PropagationRule,
        row_data: dict[str, Any],
        event_date: date,
        current_date: date,
    ) -> bool:
        """检查规则是否应该应用到这一行数据。"""
        if rule.target_table not in ["fct_transaction", "fct_balance_daily", "dim_customer"]:
            return False

        # === 新版：transient vs sustained ===
        start_date = event_date + timedelta(days=rule.delay_days)
        if current_date < start_date:
            return False
        if rule.effect_type == "transient":
            end_date = start_date + timedelta(days=rule.ramp_days)
            if current_date > end_date:
                return False
        # sustained: 没有上界

        # 交易类型过滤（如果规则指定且行中有该字段）
        if rule.transaction_type and "transaction_type" in row_data:
            if row_data.get("transaction_type") != rule.transaction_type:
                return False

        # 交易渠道过滤（如果规则指定且行中有该字段）
        if rule.transaction_channel and "transaction_channel" in row_data:
            if row_data.get("transaction_channel") not in rule.transaction_channel:
                return False

        # 采样过滤（随机决定是否影响这条记录）
        if random.random() > rule.affected_account_sample:
            return False

        # === 新增：维度过滤（Bug A 修复） ===
        if rule.branch_ids and row_data.get("branch_id") not in rule.branch_ids:
            return False

        if rule.customer_tiers:
            if self.customer_index is None:
                raise RuntimeError(
                    "PropagationRule.customer_tiers requires Engine customer_index"
                )
            cust = self.customer_index.get(row_data.get("customer_id"))
            if not cust or cust.get("customer_tier") not in rule.customer_tiers:
                return False

        if rule.branch_levels:
            if self.branch_index is None:
                raise RuntimeError(
                    "PropagationRule.branch_levels requires Engine branch_index"
                )
            branch = self.branch_index.get(row_data.get("branch_id"))
            if not branch or branch.get("branch_level") not in rule.branch_levels:
                return False

        if rule.product_ids and row_data.get("product_id") not in rule.product_ids:
            return False

        if rule.product_subcategories:
            if self.product_index is None:
                raise RuntimeError(
                    "PropagationRule.product_subcategories requires Engine product_index"
                )
            prod = self.product_index.get(row_data.get("product_id"))
            if not prod or prod.get("product_subcategory") not in rule.product_subcategories:
                return False

        return True

    def compute_delta_multiplier(self, rule: PropagationRule, days_since_start: int) -> float:
        """
        根据渐变规则计算乘数。

        示例：
        - rule.delta = -8.5 (下降 8.5%)
        - rule.ramp_days = 3
        - day 0: multiplier = 0 (还未开始)
        - day 1: multiplier = 0.33 (线性渐变第 1/3)
        - day 2: multiplier = 0.66
        - day 3: multiplier = 1.0 (完全应用)
        """
        if days_since_start < 0:
            return 0.0
        if days_since_start >= rule.ramp_days:
            return 1.0

        if rule.ramp_type == "linear":
            return days_since_start / rule.ramp_days if rule.ramp_days > 0 else 1.0
        elif rule.ramp_type == "exponential":
            # 指数缓入缓出
            progress = days_since_start / rule.ramp_days if rule.ramp_days > 0 else 1.0
            return progress ** 2
        else:
            return 1.0

    def apply_rule_to_row(
        self,
        rule: PropagationRule,
        row_data: dict[str, Any],
        event_date: date,
        current_date: date,
        event_id: str = "unknown",
    ) -> None:
        """对单条记录应用规则。"""
        if not self.should_apply_rule(rule, row_data, event_date, current_date):
            return

        # 计算距离事件开始的天数
        days_since_start = (current_date - (event_date + timedelta(days=rule.delay_days))).days

        # 获取渐变系数
        multiplier = self.compute_delta_multiplier(rule, days_since_start)

        # 修改目标列
        if rule.target_column in row_data:
            original_value = row_data[rule.target_column]
            if original_value is None:
                return

            if rule.is_percentage:
                # 百分比变化
                delta_factor = 1.0 + (rule.delta / 100.0) * multiplier
                new_value = original_value * delta_factor
            else:
                # 绝对值变化
                new_value = original_value + rule.delta * multiplier

            # 保持数值类型和精度
            if isinstance(original_value, float):
                row_data[rule.target_column] = round(new_value, 2)
            else:
                row_data[rule.target_column] = int(new_value)

        # 记录元数据（用于后续评估）
        if "_propagations" not in row_data:
            row_data["_propagations"] = []
        row_data["_propagations"].append(
            {
                "event_id": event_id,
                "metric": rule.metric_name,
                "delta": rule.delta,
                "applied_multiplier": multiplier,
            }
        )

    def apply_all_rules(
        self,
        row_data: dict[str, Any],
        rules: list[PropagationRule],
        event_date: date,
        current_date: date,
        event_id: str = "unknown",
    ) -> None:
        """依次应用多条规则。"""
        for rule in rules:
            self.apply_rule_to_row(rule, row_data, event_date, current_date, event_id)
